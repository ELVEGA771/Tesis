"""Pipeline de construccion del grafo (A5 + A6): cuatro nodos secuenciales en LangGraph.

    python pipeline.py --particion desarrollo [--limite N] [--adr repo|archivo.md]

  1. extraer   (Qwen)  contexto, problema, criterios, alternativas, argumentos,
                       consecuencias, justificacion, confirmacion, participantes, info adicional
  2. decidir   (Qwen)  decision adoptada, alternativa elegida, estado y relaciones con otros ADR
  3. escribir  (codigo) las llamadas se traducen a filas de la hoja de anotacion y se
                       construyen con csv_a_grafo.construir(): el grafo del pipeline y
                       el gold salen del mismo codigo
  4. validar   (codigo) conformidad.py (esquema, integridad, fundamentacion) + soporte:
                       la evidencia de una alternativa tiene que nombrarla
     -> si hay errores y quedan intentos (max. 3) vuelve a 1 con los errores como
        retroalimentacion (patron Reflexion); si no, termina.

Qwen no escribe en el grafo directamente: emite llamadas a funciones con esquema
fijo cuyos vocabularios (estados, tipos de relacion) salen del esquema v1.1.
El tema (CQ4/CQ8) no se extrae aqui: es un paso de asignacion posterior.
Salida: datos/grafos/<particion>/<adr>.json, registros/pipeline.jsonl (tokens y
segundos por llamada) y datos/corridas/<particion>.jsonl (resumen por ADR).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import TypedDict

import networkx as nx
from langgraph.graph import END, StateGraph

from comun import CORPUS, DATOS, TESIS, chat, manifiesto, particion, texto_adr

sys.path.insert(0, str(CORPUS / "anotacion"))
from csv_a_grafo import COLUMNAS, adr_id as nodo_adr, construir  # noqa: E402
from conformidad import VOCABULARIO, comprobar  # noqa: E402  (csv_a_grafo ya lo pone en sys.path)

MAX_INTENTOS = 3
ESTADOS = sorted(s.removeprefix("status_") for s in VOCABULARIO["NormalizedStatus"])
TIPOS_REL = sorted(s.removeprefix("reltype_") for s in VOCABULARIO["RelationType"])
POLARIDAD = ["positiva", "negativa", "neutra"]
ROLES = ["decision-maker", "consulted", "informed"]

EV = {"type": "string", "description": "Fragmento COPIADO LITERALMENTE del ADR (sin parafrasear) que respalda este elemento."}


def _f(funcion, descripcion, **props):
    req = ["evidencia"] + list(props)
    return {"type": "function", "function": {"name": funcion, "description": descripcion, "parameters": {
        "type": "object", "properties": {"evidencia": EV, **props}, "required": req}}}


S = lambda d, enum=None: {"type": "string", "description": d, **({"enum": enum} if enum else {})}  # noqa: E731

HERRAMIENTAS_EXTRAER = [
    _f("contexto", "Situacion o antecedentes que motivan la decision (seccion Context). Una llamada con el primer parrafo."),
    _f("problema", "Pregunta o problema que la decision resuelve, si esta formulado."),
    _f("criterio", "Decision driver: criterio o fuerza que se usa para elegir. Solo si hay una seccion de drivers."),
    _f("alternativa", "Una opcion considerada (lista de Considered Options u opciones descritas). Una llamada por opcion. "
       "Sin lista de opciones, no llames a esta funcion. La evidencia debe ser el texto que nombra ESA opcion.",
       nombre=S("Nombre corto de la opcion tal como aparece en el ADR.")),
    _f("argumento", "Pro o contra de UNA alternativa concreta, solo si el ADR lo escribe explicitamente (Pros and Cons "
       "of the Options, Good/Bad, o ventajas descritas junto a la opcion). Una lista de opciones sin pros ni contras no tiene argumentos.",
       alternativa=S("Nombre exacto de la alternativa, igual al usado en la funcion alternativa."),
       polaridad=S("positiva = pro, negativa = contra, neutra.", POLARIDAD)),
    _f("consecuencia", "Lo que cambia o cuesta por haber tomado la decision (Consequences, Risks, Implications).",
       polaridad=S("positiva, negativa (incluye riesgos) o neutra.", POLARIDAD)),
    _f("justificacion", "Razon por la que se eligio la opcion (because..., selected as...), incluida evidencia previa (we tested...)."),
    _f("confirmacion", "Mecanismo de verificacion continua del cumplimiento de la decision (seccion Confirmation). Casi nunca existe."),
    _f("participante", "Persona o equipo que decide, es consultado o informado (Deciders, Consulted, Informed). No Contributors.",
       nombre=S("Nombre o usuario del participante."), rol=S("Rol.", ROLES)),
    _f("info_adicional", "Informacion adicional: condiciones para revisar la decision (we will revisit if...), "
       "sub-decisiones, enlaces o notas que no son ninguno de los anteriores."),
]


def herramientas_decidir(otros):
    return [
        _f("decision", "La decision adoptada, en una frase (Decision, Decision Outcome, We will...). Exactamente una llamada.",
           resumen=S("Resumen de la decision en una frase."),
           alternativa_elegida=S("Nombre exacto de la alternativa elegida si hay lista de alternativas; cadena vacia si no.")),
        _f("estado", "Estado declarado del ADR (Status). Una llamada si el ADR declara estado.",
           literal=S("Literal tal como aparece, p. ej. 'Accepted', 'Superseded by ADR-0011'."),
           normalizado=S("Estado normalizado.", ESTADOS)),
        _f("relacion", "Relacion DECLARADA con otro ADR del mismo repositorio (supersedes / superseded by, amends / amended by, "
           "related to). Una llamada por relacion declarada. No inventes relaciones que el texto no declare.",
           tipo=S("Tipo de relacion.", TIPOS_REL),
           sentido=S("'este_es_origen' si ESTE ADR reemplaza/enmienda/relaciona al otro; 'este_es_destino' si el otro reemplaza/enmienda a este.",
                     ["este_es_origen", "este_es_destino"]),
           otro_adr=S("Nombre de fichero del otro ADR, elegido de la lista dada.", otros or None)),
    ]


SISTEMA = ("Eres un extractor de informacion de Architecture Decision Records (ADR). Registras cada elemento "
           "llamando a la funcion correspondiente. Toda evidencia es una copia literal de un fragmento del ADR. "
           "Si un elemento no aparece en el ADR, no llamas a su funcion.")


class Estado(TypedDict, total=False):
    repo: str
    archivo: str
    texto: str
    otros: list[str]
    llamadas_extraer: list[dict]
    llamadas_decidir: list[dict]
    filas: list[dict]
    grafo: dict
    errores: list[str]
    intentos: int
    historial: list[dict]


def _llamadas(msg):
    return [{**(c["function"].get("arguments") or {}), "funcion": c["function"]["name"]}
            for c in msg.get("tool_calls") or []]


def nodo_extraer(st: Estado) -> Estado:
    pedido = f"ADR `{st['archivo']}` del repositorio {st['repo']}:\n\n{st['texto']}"
    if st.get("errores"):
        previas = json.dumps(st["llamadas_extraer"], ensure_ascii=False)
        pedido += ("\n\nEn el intento anterior hiciste estas llamadas:\n" + previas +
                   "\n\nEl validador encontro estos errores:\n- " + "\n- ".join(st["errores"]) +
                   "\n\nVuelve a hacer TODAS las llamadas: repite sin cambios las correctas y corrige las "
                   "erroneas: en cada argumento usa exactamente el nombre de una alternativa registrada; si una "
                   "evidencia no aparece literalmente en el ADR, busca el fragmento exacto y, si no existe, "
                   "ese elemento no esta en el ADR y NO debes registrarlo. No elimines elementos correctos.")
    msg = chat([{"role": "system", "content": SISTEMA}, {"role": "user", "content": pedido}],
               herramientas=HERRAMIENTAS_EXTRAER, registro="pipeline",
               etiqueta=f"extraer|{st['repo']}|{st['archivo']}|{st.get('intentos', 0) + 1}")
    return {"llamadas_extraer": _llamadas(msg), "intentos": st.get("intentos", 0) + 1}


def nodo_decidir(st: Estado) -> Estado:
    alts = [c.get("nombre", "") for c in st["llamadas_extraer"] if c["funcion"] == "alternativa"]
    pedido = (f"ADR `{st['archivo']}` del repositorio {st['repo']}:\n\n{st['texto']}\n\n"
              f"Alternativas ya registradas: {alts or 'ninguna'}.\n"
              f"Otros ADR del mismo repositorio: {st['otros'] or 'ninguno'}.\n"
              "Registra la decision, el estado y las relaciones declaradas con otros ADR.")
    msg = chat([{"role": "system", "content": SISTEMA}, {"role": "user", "content": pedido}],
               herramientas=herramientas_decidir(st["otros"]), registro="pipeline",
               etiqueta=f"decidir|{st['repo']}|{st['archivo']}|{st['intentos']}")
    return {"llamadas_decidir": _llamadas(msg)}


def _fila(st, elemento, ev, **kw):
    f = dict.fromkeys(COLUMNAS, "")
    f.update(repo=st["repo"], archivo=st["archivo"], elemento=elemento, evidencia=(ev or "").strip(), **kw)
    return f


def _emparejar(ref: str, nombres: list[str]) -> str:
    """Nombre de alternativa registrada al que se refiere `ref` (igual, sin mayusculas, o uno contiene al otro)."""
    r = ref.strip().lower()
    for cond in (lambda x: x == r, lambda x: r and (r in x or x in r)):
        hits = [n for n in nombres if n.strip() and cond(n.strip().lower())]
        if len(hits) == 1:
            return hits[0]
    return ref


def a_filas(st: Estado) -> list[dict]:
    filas = []
    elegida = next((c.get("alternativa_elegida", "") for c in st["llamadas_decidir"] if c["funcion"] == "decision"), "")
    nombres_alt = [c.get("nombre", "") for c in st["llamadas_extraer"] if c["funcion"] == "alternativa"]
    elegida = _emparejar(elegida, nombres_alt) if elegida else ""
    for c in st["llamadas_extraer"]:
        n, ev = c["funcion"], c.get("evidencia", "")
        if n == "alternativa":
            filas.append(_fila(st, "alternativa", ev, valor=c.get("nombre", ""),
                               elegida="1" if elegida and c.get("nombre", "").strip() == elegida.strip() else "0"))
        elif n == "argumento":
            filas.append(_fila(st, "argumento", ev, polaridad=c.get("polaridad", ""),
                               alternativa=_emparejar(c.get("alternativa", ""), nombres_alt)))
        elif n == "consecuencia":
            filas.append(_fila(st, "consecuencia", ev, polaridad=c.get("polaridad", "")))
        elif n == "participante":
            filas.append(_fila(st, "participante", ev, valor=c.get("nombre", ""), rol=c.get("rol", "")))
        elif n == "criterio":
            filas.append(_fila(st, "driver", ev))
        elif n in ("contexto", "problema", "justificacion", "confirmacion", "info_adicional"):
            filas.append(_fila(st, n, ev))
    for c in st["llamadas_decidir"]:
        n, ev = c["funcion"], c.get("evidencia", "")
        if n == "decision":
            filas.append(_fila(st, "decision", ev, valor=c.get("resumen", "")))
        elif n == "estado":
            filas.append(_fila(st, "estado", ev, valor=c.get("literal", ""), estado_normalizado=c.get("normalizado", "")))
        elif n == "relacion":
            este, otro = st["archivo"], c.get("otro_adr", "")
            nuevo, afect = (este, otro) if c.get("sentido") == "este_es_origen" else (otro, este)
            filas.append(_fila(st, "relacion", ev, tipo_relacion=c.get("tipo", ""), adr_nuevo=nuevo, adr_afectado=afect))
    return filas


def nodo_escribir(st: Estado) -> Estado:
    filas = a_filas(st)
    G, problemas, _ = construir(filas)
    errores = [f"{filas[i]['elemento']}: {p}" for i, p in problemas]
    return {"filas": filas, "grafo": nx.node_link_data(G, edges="links"), "errores": errores}


def _soporte(G):
    """La evidencia de cada alternativa tiene que contener su nombre (alternativa considerada != decision adoptada)."""
    out = []
    for n, d in G.nodes(data=True):
        if d.get("type") == "Alternative":
            nombre, ev = (d.get("alternativeName") or "").lower(), (d.get("evidenceText") or "").lower()
            if nombre and nombre not in ev:
                out.append(f"alternativa '{d.get('alternativeName')}': la evidencia no la nombra; cita el texto que describe esa opcion")
    return out


def nodo_validar(st: Estado) -> Estado:
    G = nx.node_link_graph(st["grafo"], directed=True, multigraph=True, edges="links")
    hallazgos, met = comprobar(G, CORPUS / "adr")
    propio = nodo_adr(st["repo"], st["archivo"])
    errores = list(st.get("errores", []))
    errores += [f"{h.tipo}: {h.mensaje}" for h in hallazgos
                if h.severidad == "error" and (h.nodo.startswith(propio) or h.nodo.startswith("rd_"))]
    if not any(f["elemento"] == "decision" for f in st["filas"]):
        errores.append("falta la decision: llama exactamente una vez a la funcion decision")
    errores += _soporte(G)
    hist = st.get("historial", []) + [{"intento": st["intentos"], "errores": errores,
                                       "n_elementos": len(st["filas"]),
                                       "llamadas": st["llamadas_extraer"] + st["llamadas_decidir"],
                                       "fundamentadas": f"{met['grounded_ok']}/{met['grounded_total']}"}]
    return {"errores": errores, "historial": hist}


def _siguiente(st: Estado) -> str:
    return "extraer" if st["errores"] and st["intentos"] < MAX_INTENTOS else END


def construir_pipeline():
    g = StateGraph(Estado)
    g.add_node("extraer", nodo_extraer)
    g.add_node("decidir", nodo_decidir)
    g.add_node("escribir", nodo_escribir)
    g.add_node("validar", nodo_validar)
    g.set_entry_point("extraer")
    g.add_edge("extraer", "decidir")
    g.add_edge("decidir", "escribir")
    g.add_edge("escribir", "validar")
    g.add_conditional_edges("validar", _siguiente, {"extraer": "extraer", END: END})
    return g.compile()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--particion", choices=["desarrollo", "prueba"], default="desarrollo")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--adr", help="repo|archivo.md para correr uno solo")
    args = ap.parse_args()

    part = particion()
    objetivos = [r for r in manifiesto() if part[f"{r['repo']}|{r['filename']}"] == args.particion]
    if args.adr:
        objetivos = [r for r in manifiesto() if f"{r['repo']}|{r['filename']}" == args.adr]
    objetivos = objetivos[:args.limite] if args.limite else objetivos

    app = construir_pipeline()
    sal = DATOS / "grafos" / args.particion
    sal.mkdir(parents=True, exist_ok=True)
    (DATOS / "corridas").mkdir(exist_ok=True)
    for r in objetivos:
        otros = sorted(x["filename"] for x in manifiesto() if x["repo"] == r["repo"] and x["filename"] != r["filename"])
        t0 = time.perf_counter()
        fin = app.invoke({"repo": r["repo"], "archivo": r["filename"], "texto": texto_adr(r["repo"], r["filename"]),
                          "otros": otros, "intentos": 0, "errores": []}, {"recursion_limit": 25})
        seg = time.perf_counter() - t0
        nombre = nodo_adr(r["repo"], r["filename"])
        (sal / f"{nombre}.json").write_text(json.dumps({"filas": fin["filas"], "grafo": fin["grafo"],
                                                         "historial": fin["historial"]}, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
        res = {"adr": f"{r['repo']}|{r['filename']}", "intentos": fin["intentos"], "segundos": round(seg, 1),
               "errores_finales": fin["errores"], "elementos": len(fin["filas"])}
        with open(DATOS / "corridas" / f"{args.particion}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(res, ensure_ascii=False) + "\n")
        print(f"{'OK ' if not fin['errores'] else 'ERR'} {r['filename']:<55} intentos={fin['intentos']} "
              f"{len(fin['filas'])} elementos {seg:.0f}s" + (f"  {fin['errores'][:2]}" if fin["errores"] else ""))


if __name__ == "__main__":
    main()
