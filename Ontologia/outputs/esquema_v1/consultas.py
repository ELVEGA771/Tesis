"""Las 8 competency questions del esquema v1.0 como consultas sobre el grafo.

Cada funcion recibe un nx.MultiDiGraph con el esquema y los individuos (el que
produce csv_a_grafo.py o, mas adelante, el pipeline de extraccion) y devuelve
datos, no texto. Son la base de las consultas del protocolo de evaluacion
(bloques 2 y 3) en la condicion "grafo".
"""
from __future__ import annotations

import networkx as nx


def _out(G, n, rel):
    return [v for _, v, d in G.out_edges(n, data=True) if d.get("relation") == rel]


def _in(G, n, rel):
    return [u for u, _, d in G.in_edges(n, data=True) if d.get("relation") == rel]


def _a(G, n, k):
    return G.nodes[n].get(k)


def adrs(G):
    return sorted(n for n, d in G.nodes(data=True)
                  if d.get("type") == "ArchitectureDecisionRecord" and d.get("title"))


# --- Bloque 1 ------------------------------------------------------------------
def cq1_decision(G, adr):
    """¿Qué decisión registra un ADR y a qué repositorio pertenece?"""
    d = _out(G, adr, "recordsDecision")
    r = _out(G, adr, "belongsToRepository")
    return {"decision": _a(G, d[0], "decisionDescription") if d else None,
            "repositorio": _a(G, r[0], "repositoryName") if r else None}


def cq2_alternativas(G, adr):
    """¿Qué alternativas se consideraron y cuál se adoptó?"""
    elegida = set(_out(G, adr, "selectsAlternative"))
    return [{"alternativa": _a(G, x, "alternativeDescription"), "elegida": x in elegida}
            for x in _out(G, adr, "considersAlternative")]


# --- Bloque 2 ------------------------------------------------------------------
def cq3_estado(G, adr):
    """¿Qué estado declara, con qué literal, a qué estado normalizado corresponde y en qué fecha?"""
    s = _out(G, adr, "hasStatusDeclaration")
    norm = _out(G, s[0], "hasNormalizedStatus") if s else []
    return {"literal": _a(G, s[0], "rawStatusLiteral") if s else None,
            "normalizado": norm[0].removeprefix("status_") if norm else None,
            "fecha": _a(G, adr, "recordDate")}


def _declaraciones(G, tipo):
    """{(nuevo, afectado): {documentos que lo declaran}} para un tipo de relacion."""
    pares: dict[tuple[str, str], set[str]] = {}
    for n, d in G.nodes(data=True):
        if d.get("type") != "RelationDeclaration" or _out(G, n, "relationType") != [tipo]:
            continue
        src, dst = _in(G, n, "sourceADR"), _in(G, n, "targetADR")
        if src and dst:
            pares.setdefault((src[0], dst[0]), set()).update(_in(G, n, "declaresRelation"))
    return pares


def cq7_estado_en_fecha(G, adr, fecha: str):
    """¿En qué estado estaba la decisión de un ADR en la fecha F (AAAA-MM-DD)?

    Regla: antes de su propia fecha el ADR no existia; despues toma su estado
    declarado (si hoy declara superseded/amended, antes de eso estaba accepted),
    y pasa a superseded en la fecha del primer ADR que lo reemplaza.
    """
    propia = _a(G, adr, "recordDate")
    if not propia:
        return "indeterminado (sin fecha)"
    if fecha < propia:
        return "no existia"
    base = cq3_estado(G, adr)["normalizado"] or "desconocido"
    if base in ("superseded", "amended"):
        base = "accepted"
    for (nuevo, afectado) in _declaraciones(G, "reltype_supersedes"):
        if afectado != adr:
            continue
        f = _a(G, nuevo, "recordDate")
        if f is None:
            return "indeterminado (el ADR que lo reemplaza no tiene fecha)"
        if f <= fecha:
            return "superseded"
    return base


# --- Bloque 3 ------------------------------------------------------------------
def cq4_tema(G, adr):
    """¿Sobre qué tema trata y qué otros ADR del mismo repositorio tratan ese tema?"""
    out = []
    for t in _out(G, adr, "addressesTopic"):
        otros = sorted(x for x in _in(G, t, "addressesTopic") if x != adr)
        out.append({"tema": _a(G, t, "topicName"), "otros_adr": otros})
    return out


def cq5_reemplazos(G):
    """¿Qué ADR reemplaza a cuál, y lo declaran los dos documentos o solo uno?"""
    return [{"nuevo": n, "reemplazado": a, "declarado_por": sorted(w),
             "bidireccional": {n, a} <= w}
            for (n, a), w in sorted(_declaraciones(G, "reltype_supersedes").items())]


def cq6_enmiendas(G):
    """¿Qué ADR enmienda a otro sin reemplazarlo?"""
    sup = _declaraciones(G, "reltype_supersedes")
    return [{"enmienda": n, "enmendado": a, "sigue_vigente": not any(x == a for (_, x) in sup)}
            for (n, a) in sorted(_declaraciones(G, "reltype_amends"))]


def cq8_vigente(G, topic):
    """¿Cuál es la decisión vigente sobre un tema y por qué cadena se llega?"""
    miembros = set(_in(G, topic, "addressesTopic"))
    sup = [(n, a) for (n, a) in _declaraciones(G, "reltype_supersedes")
           if n in miembros and a in miembros]
    H = nx.DiGraph()
    H.add_nodes_from(miembros)
    H.add_edges_from((a, n) for n, a in sup)          # arista: viejo -> nuevo
    reemplazados = {a for _, a in sup}
    vig = sorted(miembros - reemplazados)
    try:
        cadena = list(nx.topological_sort(H))
    except nx.NetworkXUnfeasible:
        cadena = None                                  # ciclo: anomalia
    return {"tema": _a(G, topic, "topicName"),
            "vigentes": [{"adr": v, "decision": cq1_decision(G, v)["decision"]} for v in vig],
            "cadena": cadena,
            "conflicto": len(vig) != 1}
