"""Criterios estructurales binarios.

Adaptacion de los 8 criterios de la fase 2 del experimento inicial del paper,
que alli se aplicaban sobre Turtle y aqui sobre el grafo NetworkX. El paper
los usa para filtrar combinaciones LLM/tecnica antes del experimento
principal; sirven igual como chequeo rapido de salud de una ontologia.

Ojo con el criterio 6 (coherencia semantica de la jerarquia): el paper lo
evalua a mano. Aqui se automatiza solo la parte decidible (ciclos, subsuncion
que contradice una disjuncion declarada) y el resto queda `needs_human`.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from .ontograph import OntoGraph


@dataclass
class Criterion:
    key: str
    label: str
    passed: bool
    detail: str = ""
    needs_human: bool = False


def _subclass_graph(g: OntoGraph) -> nx.DiGraph:
    classes = set(g.classes())
    H = nx.DiGraph()
    H.add_nodes_from(classes)
    for u, v, d in g.G.edges(data=True):
        if d.get("relation") == "subClassOf" and u in classes and v in classes:
            H.add_edge(u, v)
    return H


def evaluate_criteria(g: OntoGraph, syntax_clean: bool = True) -> list[Criterion]:
    s = g.summary()
    out: list[Criterion] = []

    out.append(Criterion(
        "equivalent_class", "Presencia de una clase definida por equivalencia",
        s["equivalent_axioms"] > 0,
        "%d axiomas equivalentClass" % s["equivalent_axioms"]))

    pivots = g.pivots()
    out.append(Criterion(
        "reification", "Presencia de una clase pivote (relacion n-aria)",
        bool(pivots), "clases pivote: %s" % (", ".join(pivots) or "ninguna")))

    out.append(Criterion(
        "syntax", "Correccion sintactica y de convencion", syntax_clean,
        "sin problemas residuales tras el bucle de correccion" if syntax_clean
        else "quedaron incumplimientos sin resolver"))

    props = g.properties()
    sin_dominio = [p for p in props if not g.targets(p, "domain")]
    sin_rango = [p for p in props if not g.targets(p, "range")]
    out.append(Criterion(
        "domain_range", "Propiedades con dominio y rango",
        bool(props) and not sin_dominio and not sin_rango,
        "%d propiedades; sin dominio: %s; sin rango: %s"
        % (len(props), ", ".join(sin_dominio) or "ninguna",
           ", ".join(sin_rango) or "ninguna")))

    out.append(Criterion(
        "hierarchy", "Presencia de axiomas de jerarquia de clases",
        s["subclass_axioms"] > 0, "%d axiomas subClassOf" % s["subclass_axioms"]))

    H = _subclass_graph(g)
    problems: list[str] = []
    for cycle in list(nx.simple_cycles(H))[:3]:
        problems.append("ciclo en la jerarquia: %s" % " -> ".join(cycle))
    for u, v, d in g.G.edges(data=True):
        if d.get("relation") == "disjointWith" and u in H and v in H:
            if nx.has_path(H, u, v) or nx.has_path(H, v, u):
                problems.append("'%s' y '%s' se declaran disjuntas pero una "
                                "subsume a la otra" % (u, v))
    orphans = [c for c in g.classes()
               if H.out_degree(c) == 0 and H.in_degree(c) == 0]
    detail = "; ".join(problems) if problems else \
        "sin ciclos ni disjunciones contradictorias"
    if orphans:
        detail += " | clases sueltas (sin jerarquia): %s" % ", ".join(sorted(orphans))
    out.append(Criterion(
        "hierarchy_coherence", "Coherencia semantica de la jerarquia",
        not problems, detail, needs_human=True))

    out.append(Criterion(
        "datatype_property", "Presencia de propiedades de datos",
        s["datatype_properties"] > 0,
        "%d propiedades de datos" % s["datatype_properties"]))

    out.append(Criterion(
        "instances", "Presencia de individuos",
        s["individuals"] > 0,
        "%d individuos y %d aserciones" % (s["individuals"], s["assertions"])))

    return out


def criteria_score(criteria: list[Criterion]) -> float:
    """Media de los criterios binarios, como en la fase 2 del paper."""
    if not criteria:
        return 0.0
    return sum(1 for c in criteria if c.passed) / len(criteria)


def render_criteria(criteria: list[Criterion]) -> str:
    lines = []
    for c in criteria:
        mark = "PASS" if c.passed else "FAIL"
        human = "  [revisar a mano]" if c.needs_human else ""
        lines.append("  [%s] %-48s %s%s" % (mark, c.label, c.detail, human))
    lines.append("  score estructural: %.2f" % criteria_score(criteria))
    return "\n".join(lines)
