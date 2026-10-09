"""Convierte anotaciones.csv en el grafo de referencia (gold) y lo valida.

    python csv_a_grafo.py

1. Lee la hoja y comprueba cada fila (valores de vocabulario, extremos de las
   relaciones, campos obligatorios), indicando el numero de linea.
2. Construye el grafo con el esquema v1.1 (MADR 4.0.0 completo): ADR (titulo
   del fichero, fecha del manifiesto), decision, justificacion, contexto,
   problema, criterios, alternativas, argumentos, consecuencias, confirmacion,
   participantes, informacion adicional, estado, tema y relaciones.
3. Lo pasa por conformidad.py: esquema, integridad referencial y
   fundamentacion (cada evidencia tiene que estar literalmente en su ADR).
4. Escribe gold_anotado.json (node-link) e informa del avance.

Solo entran al grafo los ADR con al menos una evidencia escrita (sin contar
las filas driver precargadas); los demas cuentan como pendientes.
La herramienta anotador/ importa `construir()` para validar en vivo.
"""
import csv
import json
import re
import sys
from pathlib import Path

import networkx as nx

HERE = Path(__file__).resolve().parent
CORPUS = HERE.parent
ESQUEMA_DIR = CORPUS.parents[1] / "Ontologia" / "outputs" / "esquema_v1_1"
sys.path.insert(0, str(ESQUEMA_DIR))
sys.path.insert(0, str(ESQUEMA_DIR.parents[1]))
from conformidad import ESQUEMA, comprobar  # noqa: E402

HOJA = HERE / "anotaciones.csv"
COLUMNAS = ["repo", "archivo", "cadena", "elemento", "evidencia", "valor", "elegida",
            "estado_normalizado", "polaridad", "alternativa", "rol", "tipo_relacion",
            "adr_nuevo", "adr_afectado", "nota"]
ESTADOS = {"proposed", "accepted", "rejected", "deprecated", "superseded", "amended"}
TIPOS = {"supersedes", "amends", "relatesTo"}
ARGUMENTO = {"positiva": "ProArgumentStatement", "negativa": "ConArgumentStatement",
             "neutra": "NeutralArgumentStatement"}
CONSECUENCIA = {"positiva": "PositiveConsequenceStatement", "negativa": "NegativeConsequenceStatement",
                "neutra": "NeutralConsequenceStatement"}
ROLES = {"decision-maker": "DecisionMakerStatement", "consulted": "ConsultedParticipantStatement",
         "informed": "InformedParticipantStatement"}
# elemento -> (clase, propiedad ADR->nodo, atributo descriptivo)
SIMPLES = {
    "decision": ("DecisionStatement", "hasDecisionStatement", "decisionSummary"),
    "justificacion": ("JustificationStatement", "hasJustificationStatement", "justificationDescription"),
    "contexto": ("ContextStatement", "hasContextStatement", "contextDescription"),
    "problema": ("ProblemStatement", "hasProblemStatement", "problemDescription"),
    "driver": ("DecisionDriverStatement", "hasDecisionDriverStatement", "driverDescription"),
    "confirmacion": ("ConfirmationStatement", "hasConfirmationStatement", "confirmationDescription"),
    "info_adicional": ("AdditionalInformationStatement", "hasAdditionalInformationStatement",
                       "additionalInformationDescription"),
}
ELEMENTOS = set(SIMPLES) | {"estado", "alternativa", "argumento", "consecuencia", "participante",
                            "fecha", "tema", "relacion"}
SIN_EVIDENCIA = {"tema", "fecha"}


def slug(s):
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)


def adr_id(repo, archivo):
    return "adr_%s__%s" % (slug(repo), slug(archivo.removesuffix(".md")))


def ruta_adr(repo, archivo):
    return CORPUS / "adr" / repo.replace("/", "__") / archivo


def titulo(repo, archivo):
    f = ruta_adr(repo, archivo)
    if not f.exists():
        return None
    for linea in f.read_text(encoding="utf-8", errors="replace").splitlines():
        if linea.startswith("# "):
            return linea[2:].strip()
    return archivo


def leer_hoja(hoja=HOJA):
    filas = list(csv.DictReader(open(hoja, encoding="utf-8-sig"), delimiter=";"))
    for f in filas:
        for c in COLUMNAS:
            v = f.get(c) or ""
            f[c] = v if c == "evidencia" else v.strip()
    return filas


def escribir_hoja(filas, hoja=HOJA):
    tmp = hoja.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS, delimiter=";", lineterminator="\r\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(filas)
    tmp.replace(hoja)


def fechas_manifiesto():
    """registrationDate = primer commit de git (fechas_git.csv, regla O1).

    Sin fechas_git.csv cae a la fecha escrita en el ADR (manifiesto), que es de
    ultima actualizacion y no sirve para ordenar reemplazos.
    """
    git = CORPUS / "fechas_git.csv"
    if git.exists():
        return {(r["repo"], r["filename"]): r["fecha_registro"]
                for r in csv.DictReader(open(git, encoding="utf-8"))}
    return {(r["repo"], r["filename"]): r["date"]
            for r in csv.DictReader(open(CORPUS / "corpus_manifest.csv", encoding="utf-8"))}


def construir(filas, fechas=None):
    """Devuelve (G, problemas, empezados). problemas = [(indice_fila, mensaje)]."""
    fechas = fechas if fechas is not None else fechas_manifiesto()
    G = ESQUEMA.G.copy()
    problemas = []
    empezados = {(f["repo"], f["archivo"]) for f in filas
                 if f["evidencia"].strip() and f["elemento"] != "driver"}

    def adr(repo, archivo, completo):
        n = adr_id(repo, archivo)
        rid = "repo_" + slug(repo)
        if rid not in G:
            G.add_node(rid, type="Repository", repositoryName=repo)
        if n not in G:
            G.add_node(n, type="ADR", fileName=archivo)
            G.add_edge(n, rid, relation="belongsToRepository")
        if completo and not G.nodes[n].get("title"):
            G.nodes[n]["title"] = titulo(repo, archivo) or archivo
            if fechas.get((repo, archivo)):
                G.nodes[n]["registrationDate"] = fechas[(repo, archivo)]
        return n

    contador = {}
    alternativas = {}                      # (adr, texto) -> nodo
    argumentos = []
    for i, f in enumerate(filas):
        clave = (f["repo"], f["archivo"])
        if clave not in empezados:
            continue
        el, ev, val = f["elemento"], f["evidencia"].strip(), f["valor"]

        def mal(msg):
            problemas.append((i, msg))

        if el not in ELEMENTOS:
            mal("elemento '%s' desconocido" % el)
            continue
        if el not in SIN_EVIDENCIA and not ev:
            if el not in ("estado", "decision"):
                mal("fila sin evidencia: rellenala o borra la fila")
            continue
        a = adr(*clave, completo=True)
        k = contador[(a, el)] = contador.get((a, el), 0) + 1

        if el in SIMPLES:
            clase, prop, attr = SIMPLES[el]
            n = "%s_%s%d" % (a, el, k)
            G.add_node(n, type=clase, evidenceText=ev, **{attr: val or ev})
            G.add_edge(a, n, relation=prop)
        elif el == "estado":
            norm = f["estado_normalizado"]
            if norm not in ESTADOS:
                mal("estado_normalizado '%s' no valido" % norm)
                continue
            n = a + "_status"
            G.add_node(n, type="StatusStatement", declaredStatusLiteral=val or ev, evidenceText=ev)
            G.add_edge(a, n, relation="hasStatusStatement")
            G.add_edge(n, "status_" + norm, relation="hasNormalizedStatus")
        elif el == "alternativa":
            n = "%s_alt%d" % (a, k)
            G.add_node(n, type="Alternative", alternativeName=val or ev, evidenceText=ev)
            G.add_edge(a, n, relation="considersAlternative")
            if f["elegida"] == "1":
                G.add_edge(a, n, relation="selectsAlternative")
            elif f["elegida"] not in ("0", ""):
                mal("elegida debe ser 1 o 0")
            alternativas[(a, ev)] = n
            if val:
                alternativas[(a, val)] = n
        elif el in ("argumento", "consecuencia"):
            tabla = ARGUMENTO if el == "argumento" else CONSECUENCIA
            if f["polaridad"] not in tabla:
                mal("polaridad '%s' no valida: positiva, negativa o neutra" % f["polaridad"])
                continue
            n = "%s_%s%d" % (a, "arg" if el == "argumento" else "cons", k)
            attr = "argumentDescription" if el == "argumento" else "consequenceDescription"
            G.add_node(n, type=tabla[f["polaridad"]], evidenceText=ev, **{attr: val or ev})
            G.add_edge(a, n, relation="hasArgumentStatement" if el == "argumento" else "hasConsequenceStatement")
            if el == "argumento":
                argumentos.append((i, a, n, f["alternativa"]))
        elif el == "participante":
            if f["rol"] not in ROLES:
                mal("rol '%s' no valido: decision-maker, consulted o informed" % f["rol"])
                continue
            n = "%s_part%d" % (a, k)
            G.add_node(n, type=ROLES[f["rol"]], participantName=val or ev, evidenceText=ev)
            G.add_edge(a, n, relation="hasParticipantStatement")
        elif el == "fecha":
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", val):
                mal("fecha: el valor tiene que ser AAAA-MM-DD")
                continue
            G.nodes[a]["lastUpdateDate"] = val
        elif el == "tema":
            if not val:
                continue
            t = "topic_%s__%s" % (slug(f["repo"]), slug(val.lower()))
            if t not in G:
                G.add_node(t, type="Topic", topicName=val.lower())
            G.add_edge(a, t, relation="hasTopic")
        elif el == "relacion":
            tipo, nuevo, afect = f["tipo_relacion"], f["adr_nuevo"], f["adr_afectado"]
            if tipo not in TIPOS:
                mal("tipo_relacion '%s' no valido: supersedes, amends o relatesTo" % tipo)
                continue
            if f["archivo"] not in (nuevo, afect):
                mal("el documento que declara la relacion deberia ser uno de sus extremos")
            if not nuevo or not afect:
                mal("faltan adr_nuevo o adr_afectado")
                continue
            faltan = [x for x in (nuevo, afect) if not ruta_adr(f["repo"], x).exists()]
            if faltan:
                mal("'%s' no existe en el corpus de ese repositorio" % faltan[0])
                continue
            an = adr(f["repo"], nuevo, completo=(f["repo"], nuevo) in empezados)
            aa = adr(f["repo"], afect, completo=(f["repo"], afect) in empezados)
            n = "rd_%s__%s__por_%s" % (an.split("__", 1)[1], aa.split("__", 1)[1], a.split("__", 1)[1])
            if n in G:
                n += "_%d" % k
            G.add_node(n, type="RelationshipStatement", evidenceText=ev)
            G.add_edge(an, n, relation="sourceADR")
            G.add_edge(aa, n, relation="targetADR")
            G.add_edge(a, n, relation="declaringADR")
            G.add_edge(n, "reltype_" + tipo, relation="relationType")

    for i, a, n, ref in argumentos:
        alt = alternativas.get((a, ref.strip()))
        if alt:
            G.add_edge(alt, n, relation="argumentAlternative")
        else:
            problemas.append((i, "argumento: la columna alternativa no coincide con ninguna alternativa del ADR"))
    return G, problemas, empezados


def main():
    filas = leer_hoja()
    subset = {(r["repo"], r["filename"])
              for r in csv.DictReader(open(CORPUS / "anotacion_subset.csv", encoding="utf-8"))}
    G, problemas, empezados = construir(filas)
    hallazgos, met = comprobar(G, CORPUS / "adr")
    data = nx.node_link_data(G, edges="links")
    (HERE / "gold_anotado.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")

    completos = [k for k in subset if G.has_node(adr_id(*k))
                 and any(d.get("relation") == "hasDecisionStatement"
                         for _, _, d in G.out_edges(adr_id(*k), data=True))]
    print("Avance: %d/%d ADR con decision anotada | %d empezados" % (len(completos), len(subset), len(empezados)))
    print("\nProblemas en la hoja (%d):" % len(problemas))
    for i, p in problemas:
        print("  linea %d (%s, %s): %s" % (i + 2, filas[i]["archivo"], filas[i]["elemento"], p))
    errores = [h for h in hallazgos if h.severidad == "error"]
    print("\nConformidad con el esquema v1.1: %d errores, %d avisos" % (len(errores), len(hallazgos) - len(errores)))
    for h in hallazgos:
        if h.severidad == "error" or h.tipo not in ("referenciado", "fecha"):
            print("  " + str(h))
    print("\nFundamentacion: %d/%d evidencias encontradas en su ADR (tasa no fundamentados = %s)"
          % (met["grounded_ok"], met["grounded_total"], met.get("tasa_no_fundamentados")))
    print("Escrito gold_anotado.json")
    return 1 if (problemas or errores) else 0


if __name__ == "__main__":
    sys.exit(main())
