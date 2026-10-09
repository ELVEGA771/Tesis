"""Validador de INSTANCIAS contra el esquema v1.1 (MADR 4.0.0 completo).

Cubre los tres mecanismos de validacion de la planificacion (actividad A6):

  1. Verificacion de esquema: tipos, dominios y rangos (con subclases),
     atributos declarados, vocabularios cerrados y propiedades funcionales.
  2. Integridad referencial: cada RelationshipStatement tiene exactamente un
     origen, un destino, un documento declarante y un tipo; origen != destino.
     Cada Statement cuelga de un ADR; cada argumento, de una alternativa.
  3. Nodos no fundamentados: el evidenceText de cada Statement tiene que
     aparecer literalmente en el fichero de su ADR de origen.

Uso como modulo:
    from conformidad import comprobar
    hallazgos, metricas = comprobar(G, dir_corpus)

Uso por linea de comandos (grafo en node-link JSON):
    python conformidad.py grafo.json --corpus ../../../Extraccion_ADR/corpus_tesis/adr
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from cqbycq.dsl import load_graph  # noqa: E402

ESQUEMA = load_graph(HERE / "ontology.py")
S = ESQUEMA.G
SCHEMA_NODES = set(S.nodes)
RESERVADOS = {"type", "comment", "pivot", "characteristics", "cardinality",
              "restriction", "provenance"}

CLASES = set(ESQUEMA.classes())
OBJ = {p for p in ESQUEMA.object_properties()}
DAT = set(ESQUEMA.nodes_of_type("DatatypeProperty"))

ADR, STMT, EVID = "ADR", "Statement", "evidenceText"
REL_PIVOT = "RelationshipStatement"
EXTREMOS = {"sourceADR", "targetADR", "declaringADR"}


def _tgt(n, rel):
    return [v for _, v, d in S.out_edges(n, data=True) if d.get("relation") == rel]


def _ancestros(c):
    vistos, pila = {c}, [c]
    while pila:
        for p in _tgt(pila.pop(), "subClassOf"):
            if p not in vistos:
                vistos.add(p)
                pila.append(p)
    return vistos


ANC = {c: _ancestros(c) for c in CLASES}
DOMINIO = {p: set(_tgt(p, "domain")) for p in OBJ | DAT}
RANGO = {p: set(_tgt(p, "range")) for p in OBJ | DAT}
FUNCIONAL = {n for n in OBJ | DAT if "Functional" in (S.nodes[n].get("characteristics") or [])}
VOCABULARIO = {c: {n for n, d in S.nodes(data=True) if d.get("type") == c}
               for c in ("NormalizedStatus", "RelationType")}
DISJUNTAS = [(u, v) for u, v, d in S.edges(data=True) if d.get("relation") == "disjointWith"]


@dataclass
class Hallazgo:
    severidad: str   # error | aviso
    tipo: str
    nodo: str
    mensaje: str

    def __str__(self):
        return "[%s] %-16s %s: %s" % (self.severidad, self.tipo, self.nodo, self.mensaje)


def _es(tipo, clase):
    return tipo in ANC and clase in ANC[tipo]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def comprobar(G: nx.MultiDiGraph, corpus: Path | None = None):
    h: list[Hallazgo] = []

    def err(t, n, m):
        h.append(Hallazgo("error", t, n, m))

    def aviso(t, n, m):
        h.append(Hallazgo("aviso", t, n, m))

    ind = {n: d for n, d in G.nodes(data=True) if n not in SCHEMA_NODES}

    def out(n, rel):
        return [v for _, v, d in G.out_edges(n, data=True) if d.get("relation") == rel]

    def inc(n, rel):
        return [u for u, _, d in G.in_edges(n, data=True) if d.get("relation") == rel]

    def tipo(n):
        return G.nodes[n].get("type")

    def duenos(n):
        """ADR de los que sale el Statement (el declarante, en las relaciones)."""
        if tipo(n) == REL_PIVOT:
            return inc(n, "declaringADR")
        return [u for u, _, d in G.in_edges(n, data=True)
                if tipo(u) == ADR and d.get("relation") not in EXTREMOS]

    # --- 1. individuos y atributos --------------------------------------------
    for n, d in ind.items():
        if not re.match(r"^[A-Za-z][A-Za-z0-9_-]*$", str(n)):
            err("nombre", n, "id no valido: letras, digitos, _ y -, empezando por letra")
        t = d.get("type")
        if t not in CLASES:
            err("tipo", n, "type=%r no es una clase del esquema" % t)
            continue
        if t in VOCABULARIO:
            err("vocabulario", n, "no se pueden crear valores nuevos de %s; usa: %s"
                % (t, ", ".join(sorted(VOCABULARIO[t]))))
        for k, v in d.items():
            if k in RESERVADOS:
                continue
            if k not in DAT:
                err("atributo", n, "'%s' no es una DatatypeProperty del esquema" % k)
            elif not any(_es(t, dom) for dom in DOMINIO[k]):
                err("dominio", n, "'%s' no aplica a %s (dominio: %s)" % (k, t, ", ".join(DOMINIO[k])))
            elif not isinstance(v, str) or not v.strip():
                err("valor", n, "'%s' vacio o no textual" % k)
        if _es(t, STMT) and not d.get(EVID):
            err("sin_evidencia", n, "%s sin %s" % (t, EVID))

    # --- 2. aristas entre individuos ------------------------------------------
    for u, v, d in G.edges(data=True):
        rel = d.get("relation")
        if u in SCHEMA_NODES and v in SCHEMA_NODES:
            continue
        if rel not in OBJ:
            err("relacion", u, "'%s' no es una ObjectProperty del esquema (-> %s)" % (rel, v))
            continue
        tu, tv = tipo(u), tipo(v)
        if not any(_es(tu, c) for c in DOMINIO[rel]):
            err("dominio", u, "%s -%s-> %s: el origen es %s y el dominio es %s"
                % (u, rel, v, tu, ", ".join(DOMINIO[rel])))
        if not any(_es(tv, c) for c in RANGO[rel]):
            err("rango", u, "%s -%s-> %s: el destino es %s y el rango es %s"
                % (u, rel, v, tv, ", ".join(RANGO[rel])))
    for n in ind:
        for p in FUNCIONAL & OBJ:
            if len(out(n, p)) > 1:
                err("funcional", n, "'%s' es Functional y tiene %d valores" % (p, len(out(n, p))))
        t = tipo(n)
        for a, b in DISJUNTAS:
            if _es(t, a) and _es(t, b):
                err("disjuntas", n, "%s es a la vez %s y %s" % (t, a, b))

    # --- 3. obligatorios e integridad referencial -------------------------------
    for n, d in ind.items():
        t = d.get("type")
        if t == ADR and not d.get("title"):
            aviso("referenciado", n, "ADR referenciado por una relacion pero sin anotar")
            if not out(n, "belongsToRepository") or not d.get("fileName"):
                err("obligatorio", n, "ADR referenciado sin repositorio o sin fileName")
        elif t == ADR:
            if not out(n, "belongsToRepository"):
                err("obligatorio", n, "ADR sin belongsToRepository")
            if not d.get("fileName"):
                err("obligatorio", n, "ADR sin fileName")
            if not out(n, "hasDecisionStatement"):
                err("obligatorio", n, "todo ADR registra una decision (falta hasDecisionStatement)")
            if out(n, "selectsAlternative") and not out(n, "considersAlternative"):
                aviso("alternativas", n, "elige una alternativa pero no considera ninguna")
            if not d.get("registrationDate"):
                aviso("fecha", n, "sin registrationDate: no entra en las consultas de estado en fecha")
            if not out(n, "hasTopic"):
                aviso("tema", n, "sin hasTopic")
        elif t == REL_PIVOT:
            src, dst, dec, rt = (inc(n, "sourceADR"), inc(n, "targetADR"),
                                 inc(n, "declaringADR"), out(n, "relationType"))
            for nombre, lista in (("sourceADR", src), ("targetADR", dst),
                                  ("declaringADR", dec), ("relationType", rt)):
                if len(lista) != 1:
                    err("integridad", n, "necesita exactamente 1 %s y tiene %d" % (nombre, len(lista)))
            if src and dst and src[0] == dst[0]:
                err("integridad", n, "origen y destino son el mismo ADR")
            if src and dst and dec and dec[0] not in (src[0], dst[0]):
                aviso("integridad", n, "la declara %s, que no es ninguno de los extremos" % dec[0])
            if src and dst and rt == ["reltype_supersedes"]:
                ts, td = set(out(src[0], "hasTopic")), set(out(dst[0], "hasTopic"))
                if ts and td and not ts & td:
                    aviso("tema", n, "%s reemplaza a %s pero no comparten Topic" % (src[0], dst[0]))
        elif t == "StatusStatement" and len(out(n, "hasNormalizedStatus")) != 1:
            err("obligatorio", n, "StatusStatement sin hasNormalizedStatus")
        if _es(t, STMT) and t != REL_PIVOT and not duenos(n):
            err("huerfano", n, "%s sin ADR del que cuelgue" % t)
        if _es(t, "ArgumentStatement") and not inc(n, "argumentAlternative"):
            aviso("argumento", n, "argumento sin la alternativa a la que se refiere")

    # --- 4. fundamentacion ------------------------------------------------------
    metricas = {"grounded_total": 0, "grounded_ok": 0}
    if corpus is not None:
        cache: dict[str, str] = {}

        def texto_adr(a):
            repo = out(a, "belongsToRepository")
            fn = G.nodes[a].get("fileName")
            if not repo or not fn:
                return None
            rn = G.nodes[repo[0]].get("repositoryName", "")
            f = Path(corpus) / rn.replace("/", "__") / fn
            if str(f) not in cache:
                cache[str(f)] = _norm(f.read_text(encoding="utf-8", errors="replace")) if f.exists() else None
            return cache[str(f)]

        for n, d in ind.items():
            t = d.get("type")
            if not _es(t, STMT) or not d.get(EVID):
                continue
            metricas["grounded_total"] += 1
            textos = [x for x in (texto_adr(a) for a in duenos(n)) if x is not None]
            if not textos:
                aviso("fundamentacion", n, "no se encontro el fichero del ADR de origen")
                continue
            if any(_norm(d[EVID]) in tx for tx in textos):
                metricas["grounded_ok"] += 1
            else:
                err("no_fundamentado", n, "el evidenceText no aparece en el ADR de origen: %r"
                    % d[EVID][:70])
        tot = metricas["grounded_total"]
        metricas["tasa_no_fundamentados"] = round(1 - metricas["grounded_ok"] / tot, 4) if tot else None
    return h, metricas


def cargar_json(path: Path) -> nx.MultiDiGraph:
    data = json.loads(path.read_text(encoding="utf-8"))
    clave = "links" if "links" in data else "edges"
    return nx.node_link_graph(data, directed=True, multigraph=True, edges=clave)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("grafo", type=Path, help="grafo poblado en node-link JSON")
    ap.add_argument("--corpus", type=Path, help="carpeta adr/ del corpus (para fundamentacion)")
    a = ap.parse_args()
    hallazgos, met = comprobar(cargar_json(a.grafo), a.corpus)
    for x in hallazgos:
        print(x)
    e = sum(x.severidad == "error" for x in hallazgos)
    print("\n%d errores, %d avisos | %s" % (e, len(hallazgos) - e, met))
    sys.exit(1 if e else 0)
