"""
Convencion de grafo y validador de la ontologia.

El LLM escribe NetworkX plano (`G.add_node(...)` / `G.add_edge(...)`), tal y
como lo define el prompt. Este modulo no construye el grafo con una API de
alto nivel: recibe el grafo ya construido y comprueba que cumple la convencion,
que es el equivalente de validar el Turtle del metodo original.

CONVENCION
==========

Todo es un nodo con atributo `type` y toda arista lleva atributo `relation`.

Nivel esquema (TBox)
--------------------
  type="Class"              una clase. `pivot=True` si es de reificacion.
  type="ObjectProperty"     relacion entre clases.
  type="DatatypeProperty"   atributo literal de una clase.
  type="Restriction"        restriccion anonima (el bnode de OWL).
  type="Datatype"           un xsd:* (se declara solo al usarlo como rango).

Nivel instancia (ABox)
----------------------
  type="<NombreDeClaseDeclarada>"   un individuo de esa clase.
      Los valores literales van como atributos del nodo:
      G.add_node("ADR-001", type="ADR", title="Usar PostgreSQL")

RELACIONES
----------
  subClassOf       Class -> Class            jerarquia
                   Restriction -> Class      axioma de clase general
  equivalentClass  Class -> Class | Restriction
  disjointWith     Class -> Class
  domain           Property -> Class
  range            ObjectProperty -> Class
                   DatatypeProperty -> Datatype (xsd:*)
  subPropertyOf    Property -> Property
  inverseOf        ObjectProperty -> ObjectProperty
  onProperty       Restriction -> Property
  someValuesFrom   Restriction -> Class      condicion suficiente "existe"
  allValuesFrom    Restriction -> Class      condicion "solo"
  hasValue         Restriction -> Individual
  <nombreDeObjectProperty>   Individual -> Individual   (asercion)

Se usa `MultiDiGraph` y no `DiGraph`: un `DiGraph` solo admite una arista por
par de nodos, y hace falta poder tener, por ejemplo, `considersAlternative` y
`selectsAlternative` entre el mismo ADR y la misma alternativa.
"""

from __future__ import annotations

import json
import re
from typing import Any

import networkx as nx

SCHEMA_TYPES = {"Class", "ObjectProperty", "DatatypeProperty", "Restriction",
                "Datatype"}
PROPERTY_TYPES = {"ObjectProperty", "DatatypeProperty"}

SCHEMA_RELATIONS = {
    "subClassOf", "equivalentClass", "disjointWith", "domain", "range",
    "subPropertyOf", "inverseOf", "onProperty",
    "someValuesFrom", "allValuesFrom", "hasValue",
}
RESTRICTION_FILLERS = {"someValuesFrom", "allValuesFrom", "hasValue"}

CHARACTERISTICS = {
    "Functional", "InverseFunctional", "Transitive", "Symmetric",
    "Asymmetric", "Reflexive", "Irreflexive",
}

XSD_TYPES = {
    "xsd:string", "xsd:integer", "xsd:decimal", "xsd:float", "xsd:double",
    "xsd:boolean", "xsd:byte", "xsd:date", "xsd:dateTime", "xsd:time",
    "xsd:duration", "xsd:gYear", "xsd:anyURI",
}

# Los nombres de nodo admiten letras, digitos, guion bajo y guion; los xsd
# llevan prefijo con dos puntos.
_NAME_RE = re.compile(r"^(xsd:[A-Za-z][A-Za-z0-9]*|[A-Za-z][A-Za-z0-9_\-]*)$")

# Atributos reservados del nodo que no son valores de datos de un individuo.
RESERVED_NODE_ATTRS = {"type", "comment", "pivot", "characteristics",
                       "cardinality", "restriction", "provenance"}


class OntoGraphError(ValueError):
    """Problema de convencion en el grafo."""


class Issue:
    """Un incumplimiento de la convencion, con la frase que hay que corregir."""

    def __init__(self, kind: str, message: str, where: str = "") -> None:
        self.kind = kind
        self.message = message
        self.where = where

    def render(self) -> str:
        return "(%s) %s%s" % (self.kind, self.message,
                              (" -> %s" % self.where) if self.where else "")

    def __repr__(self) -> str:
        return "Issue%r" % ((self.kind, self.message, self.where),)


class OntoGraph:
    """Contenedor del `MultiDiGraph` + validacion de la convencion."""

    def __init__(self, graph: nx.MultiDiGraph | None = None) -> None:
        self.G: nx.MultiDiGraph = graph if graph is not None else nx.MultiDiGraph()

    # ------------------------------------------------------------------ #
    # Consultas de conveniencia
    # ------------------------------------------------------------------ #

    def nodes_of_type(self, *types: str) -> list[str]:
        wanted = set(types)
        return sorted(n for n, d in self.G.nodes(data=True)
                      if d.get("type") in wanted)

    def classes(self) -> list[str]:
        return self.nodes_of_type("Class")

    def properties(self) -> list[str]:
        return self.nodes_of_type("ObjectProperty", "DatatypeProperty")

    def object_properties(self) -> list[str]:
        return self.nodes_of_type("ObjectProperty")

    def individuals(self) -> list[str]:
        classes = set(self.classes())
        return sorted(n for n, d in self.G.nodes(data=True)
                      if d.get("type") in classes)

    def restrictions(self) -> list[str]:
        return self.nodes_of_type("Restriction")

    def pivots(self) -> list[str]:
        return sorted(n for n in self.classes() if self.G.nodes[n].get("pivot"))

    def targets(self, node: str, relation: str) -> list[str]:
        return sorted(v for _, v, d in self.G.out_edges(node, data=True)
                      if d.get("relation") == relation)

    def sources(self, node: str, relation: str) -> list[str]:
        return sorted(u for u, _, d in self.G.in_edges(node, data=True)
                      if d.get("relation") == relation)

    # ------------------------------------------------------------------ #
    # Validacion de la convencion
    # ------------------------------------------------------------------ #

    def validate(self) -> list[Issue]:
        """Comprueba la convencion. Cada Issue alimenta el bucle de correccion.

        Implementa, uno a uno, los fallos que el footer del prompt enumera:
        nodos sin `type`, aristas sin `relation`, clases de reificacion mal
        conectadas, atributos tratados como clases, propiedades sin dominio o
        rango, etc.
        """
        issues: list[Issue] = []
        classes = set(self.classes())
        props = {n: self.G.nodes[n]["type"] for n in self.properties()}

        # --- nodos -------------------------------------------------------
        for n, d in self.G.nodes(data=True):
            if not _NAME_RE.match(str(n)):
                issues.append(Issue(
                    "node_name",
                    "el nombre de nodo '%s' no es valido: usa letras, digitos, "
                    "'_' y '-', empezando por letra (o un xsd:* para los "
                    "tipos de dato)" % n))
            ntype = d.get("type")
            if not ntype:
                issues.append(Issue(
                    "missing_type",
                    "el nodo '%s' no tiene atributo `type`" % n,
                    'G.add_node("%s", type="...")' % n))
                continue
            if ntype not in SCHEMA_TYPES and ntype not in classes:
                issues.append(Issue(
                    "unknown_type",
                    "el nodo '%s' declara type='%s', que no es ni un tipo de "
                    "esquema (%s) ni una clase declarada. Si '%s' es una clase, "
                    "declarala con G.add_node(\"%s\", type=\"Class\")"
                    % (n, ntype, ", ".join(sorted(SCHEMA_TYPES)), ntype, ntype)))
            if ntype == "Datatype" and n not in XSD_TYPES:
                issues.append(Issue(
                    "unknown_datatype",
                    "'%s' se declara como Datatype pero no es un xsd conocido. "
                    "Usa uno de: %s" % (n, ", ".join(sorted(XSD_TYPES)))))
            chars = d.get("characteristics") or []
            if chars and ntype not in PROPERTY_TYPES:
                issues.append(Issue(
                    "characteristics",
                    "'%s' no es una propiedad, no puede tener `characteristics`" % n))
            for c in chars if isinstance(chars, (list, tuple)) else [chars]:
                if c not in CHARACTERISTICS:
                    issues.append(Issue(
                        "characteristics",
                        "caracteristica '%s' desconocida en '%s'. Validas: %s"
                        % (c, n, ", ".join(sorted(CHARACTERISTICS)))))
            if ntype == "Class":
                literals = [k for k in d
                            if k not in RESERVED_NODE_ATTRS]
                if literals:
                    issues.append(Issue(
                        "literal_on_class",
                        "la clase '%s' lleva valores literales (%s). Los "
                        "literales son de los individuos; en el esquema se "
                        "modelan con un nodo type='DatatypeProperty'"
                        % (n, ", ".join(sorted(literals)))))

        # --- aristas -----------------------------------------------------
        obj_props = set(self.object_properties())
        for u, v, k, d in self.G.edges(keys=True, data=True):
            rel = d.get("relation")
            where = 'G.add_edge("%s", "%s", ...)' % (u, v)
            if not rel:
                issues.append(Issue(
                    "missing_relation",
                    "la arista %s -> %s no tiene atributo `relation`" % (u, v),
                    where))
                continue
            if rel not in SCHEMA_RELATIONS and rel not in obj_props:
                issues.append(Issue(
                    "unknown_relation",
                    "relation='%s' (%s -> %s) no es una relacion de esquema ni "
                    "una ObjectProperty declarada. Declarala con "
                    "G.add_node(\"%s\", type=\"ObjectProperty\") o usa una de: %s"
                    % (rel, u, v, rel, ", ".join(sorted(SCHEMA_RELATIONS))),
                    where))
                continue
            issues.extend(self._check_edge(u, v, rel, where, classes, props))

        # --- propiedades sin dominio o rango ------------------------------
        for p, ptype in sorted(props.items()):
            if not self.targets(p, "domain"):
                issues.append(Issue(
                    "no_domain", "la propiedad '%s' no tiene dominio" % p,
                    'G.add_edge("%s", "<Clase>", relation="domain")' % p))
            rng = self.targets(p, "range")
            if not rng:
                issues.append(Issue(
                    "no_range", "la propiedad '%s' no tiene rango" % p,
                    'G.add_edge("%s", "<Clase o xsd:*>", relation="range")' % p))
            elif ptype == "DatatypeProperty":
                for r in rng:
                    if r not in XSD_TYPES:
                        issues.append(Issue(
                            "bad_range",
                            "'%s' es DatatypeProperty pero su rango '%s' no es "
                            "un xsd:*. Si el rango es una clase, deberia ser "
                            "type='ObjectProperty'" % (p, r)))
            elif ptype == "ObjectProperty":
                for r in rng:
                    if r in XSD_TYPES:
                        issues.append(Issue(
                            "bad_range",
                            "'%s' es ObjectProperty pero su rango es el literal "
                            "'%s'. Deberia ser type='DatatypeProperty'" % (p, r)))

        # --- restricciones -------------------------------------------------
        for r in self.restrictions():
            if not self.targets(r, "onProperty"):
                issues.append(Issue(
                    "restriction",
                    "la restriccion '%s' no indica sobre que propiedad aplica" % r,
                    'G.add_edge("%s", "<propiedad>", relation="onProperty")' % r))
            if not any(self.targets(r, f) for f in RESTRICTION_FILLERS):
                issues.append(Issue(
                    "restriction",
                    "la restriccion '%s' no tiene relleno (someValuesFrom, "
                    "allValuesFrom o hasValue)" % r))
            if not (self.sources(r, "equivalentClass") or self.targets(r, "subClassOf")
                    or self.sources(r, "subClassOf")):
                issues.append(Issue(
                    "restriction",
                    "la restriccion '%s' no esta conectada a ninguna clase "
                    "(equivalentClass o subClassOf)" % r))

        # --- clases pivote (reificacion) ------------------------------------
        for pivot in self.pivots():
            incoming = [u for u in self.sources(pivot, "range")
                        if u in props]
            if len(incoming) < 2:
                issues.append(Issue(
                    "pivot",
                    "'%s' esta marcada como clase pivote pero solo %d "
                    "propiedad(es) la tienen como rango. Una clase de "
                    "reificacion debe ser el rango de las propiedades que "
                    "salen de cada una de sus componentes"
                    % (pivot, len(incoming))))

        # --- jerarquia ------------------------------------------------------
        H = nx.DiGraph()
        H.add_nodes_from(classes)
        for u, v, d in self.G.edges(data=True):
            if d.get("relation") == "subClassOf" and u in classes and v in classes:
                H.add_edge(u, v)
        for cycle in list(nx.simple_cycles(H))[:3]:
            issues.append(Issue(
                "hierarchy_cycle",
                "ciclo en la jerarquia de clases: %s" % " -> ".join(cycle)))

        return issues

    def _check_edge(self, u: str, v: str, rel: str, where: str,
                    classes: set[str], props: dict[str, str]) -> list[Issue]:
        issues: list[Issue] = []
        tu = self.G.nodes[u].get("type")
        tv = self.G.nodes[v].get("type")

        def expect(node: str, actual: str | None, allowed: set[str],
                   role: str) -> None:
            if actual not in allowed:
                issues.append(Issue(
                    "bad_endpoint",
                    "en relation='%s', %s '%s' es de type='%s' y deberia ser "
                    "%s" % (rel, role, node, actual, " o ".join(sorted(allowed))),
                    where))

        if rel == "subClassOf":
            expect(u, tu, {"Class", "Restriction"}, "origen")
            expect(v, tv, {"Class"}, "destino")
        elif rel == "equivalentClass":
            expect(u, tu, {"Class"}, "origen")
            expect(v, tv, {"Class", "Restriction"}, "destino")
        elif rel == "disjointWith":
            expect(u, tu, {"Class"}, "origen")
            expect(v, tv, {"Class"}, "destino")
        elif rel == "domain":
            expect(u, tu, set(PROPERTY_TYPES), "origen")
            expect(v, tv, {"Class"}, "destino")
        elif rel == "range":
            expect(u, tu, set(PROPERTY_TYPES), "origen")
            expect(v, tv, {"Class", "Datatype"}, "destino")
        elif rel == "subPropertyOf":
            expect(u, tu, set(PROPERTY_TYPES), "origen")
            expect(v, tv, set(PROPERTY_TYPES), "destino")
        elif rel == "inverseOf":
            expect(u, tu, {"ObjectProperty"}, "origen")
            expect(v, tv, {"ObjectProperty"}, "destino")
        elif rel == "onProperty":
            expect(u, tu, {"Restriction"}, "origen")
            expect(v, tv, set(PROPERTY_TYPES), "destino")
        elif rel in RESTRICTION_FILLERS:
            expect(u, tu, {"Restriction"}, "origen")
            if rel == "hasValue":
                if tv in SCHEMA_TYPES:
                    issues.append(Issue(
                        "bad_endpoint",
                        "hasValue debe apuntar a un individuo, no a '%s' "
                        "(type='%s')" % (v, tv), where))
            else:
                expect(v, tv, {"Class"}, "destino")
        else:
            # Asercion entre individuos usando una ObjectProperty declarada.
            for node, t, role in ((u, tu, "origen"), (v, tv, "destino")):
                if t in SCHEMA_TYPES:
                    issues.append(Issue(
                        "bad_endpoint",
                        "'%s' usa la propiedad '%s' como arista directa, pero "
                        "'%s' es de esquema (type='%s'). Entre clases, una "
                        "propiedad se declara como nodo con domain y range; las "
                        "aristas directas son solo entre individuos"
                        % (role, rel, node, t), where))
        return issues

    # ------------------------------------------------------------------ #
    # Comparacion entre versiones (regresion entre CQs)
    # ------------------------------------------------------------------ #

    def diff(self, previous: "OntoGraph") -> dict[str, list[str]]:
        """Que se anadio y que DESAPARECIO respecto a la version anterior.

        El footer del prompt es explicito: la salida de cada etapa debe
        conservar todo lo anterior. `removed_*` es lo que el modelo ha perdido
        por el camino, y es lo que dispara la correccion."""
        prev_nodes = set(previous.G.nodes)
        cur_nodes = set(self.G.nodes)
        prev_edges = {(u, v, d.get("relation"))
                      for u, v, d in previous.G.edges(data=True)}
        cur_edges = {(u, v, d.get("relation"))
                     for u, v, d in self.G.edges(data=True)}
        fmt = lambda e: "%s -%s-> %s" % (e[0], e[2], e[1])  # noqa: E731
        return {
            "added_nodes": sorted(cur_nodes - prev_nodes),
            "removed_nodes": sorted(prev_nodes - cur_nodes),
            "added_edges": sorted(fmt(e) for e in cur_edges - prev_edges),
            "removed_edges": sorted(fmt(e) for e in prev_edges - cur_edges),
        }

    def restore_from(self, previous: "OntoGraph") -> dict[str, list[str]]:
        """Red de seguridad: reinyecta lo que la nueva version haya perdido.

        Solo se usa si, tras agotar los intentos de correccion, el modelo sigue
        borrando parte de la ontologia. Queda registrado en el `record` de la
        CQ para que la perdida sea visible y no silenciosa."""
        lost = self.diff(previous)
        for n in lost["removed_nodes"]:
            self.G.add_node(n, **json.loads(json.dumps(previous.G.nodes[n])))
        current = {(u, v, d.get("relation"))
                   for u, v, d in self.G.edges(data=True)}
        for u, v, k, d in previous.G.edges(keys=True, data=True):
            if (u, v, d.get("relation")) not in current:
                self.G.add_edge(u, v, key=d.get("relation"),
                                **json.loads(json.dumps(d)))
        return lost

    # ------------------------------------------------------------------ #
    # Metricas y serializacion
    # ------------------------------------------------------------------ #

    def summary(self) -> dict[str, int]:
        rels = [d.get("relation") for _, _, d in self.G.edges(data=True)]
        schema_rels = set(SCHEMA_RELATIONS)
        return {
            "classes": len(self.classes()),
            "pivot_classes": len(self.pivots()),
            "object_properties": len(self.object_properties()),
            "datatype_properties": len(self.nodes_of_type("DatatypeProperty")),
            "restrictions": len(self.restrictions()),
            "subclass_axioms": rels.count("subClassOf"),
            "equivalent_axioms": rels.count("equivalentClass"),
            "disjoint_axioms": rels.count("disjointWith"),
            "subproperty_axioms": rels.count("subPropertyOf"),
            "inverse_axioms": rels.count("inverseOf"),
            "individuals": len(self.individuals()),
            "assertions": sum(1 for r in rels if r not in schema_rels),
            "nodes": self.G.number_of_nodes(),
            "edges": self.G.number_of_edges(),
        }

    def to_script(self) -> str:
        """Re-emite el grafo como script NetworkX canonico.

        Es lo que se inyecta como memoria en el prompt de la siguiente CQ:
        mismo formato que el que debe producir el modelo, con orden estable y
        sin duplicados."""
        lines = ["import networkx as nx", "", "G = nx.MultiDiGraph()", ""]

        def node_call(n: str) -> str:
            d = self.G.nodes[n]
            args = ['"%s"' % n]
            if "type" in d:
                args.append('type="%s"' % d["type"])
            for key in ("pivot", "restriction", "cardinality", "characteristics",
                        "comment"):
                if key in d and d[key] not in (None, "", False, []):
                    args.append("%s=%s" % (key, _lit(d[key])))
            for key in sorted(d):
                if key not in RESERVED_NODE_ATTRS:
                    args.append("%s=%s" % (key, _lit(d[key])))
            return "G.add_node(%s)" % ", ".join(args)

        groups = [
            ("Clases", self.classes()),
            ("Object properties", self.object_properties()),
            ("Datatype properties", self.nodes_of_type("DatatypeProperty")),
            ("Tipos de dato", self.nodes_of_type("Datatype")),
            ("Restricciones", self.restrictions()),
            ("Individuos", self.individuals()),
        ]
        for label, nodes in groups:
            if not nodes:
                continue
            lines.append("# --- %s ---" % label)
            lines.extend(node_call(n) for n in nodes)
            lines.append("")

        typed = {n for _, nodes in groups for n in nodes}
        untyped = sorted(set(self.G.nodes) - typed)
        if untyped:
            lines.append("# --- Nodos sin tipo reconocido ---")
            lines.extend(node_call(n) for n in untyped)
            lines.append("")

        edges = sorted(
            (d.get("relation") or "", u, v, k)
            for u, v, k, d in self.G.edges(keys=True, data=True))
        order = {r: i for i, r in enumerate(
            ["subClassOf", "equivalentClass", "disjointWith", "domain", "range",
             "subPropertyOf", "inverseOf", "onProperty", "someValuesFrom",
             "allValuesFrom", "hasValue"])}
        schema_edges = [e for e in edges if e[0] in order]
        schema_edges.sort(key=lambda e: (order[e[0]], e[1], e[2]))
        assertion_edges = [e for e in edges if e[0] not in order]

        if schema_edges:
            lines.append("# --- Axiomas ---")
            for rel, u, v, k in schema_edges:
                lines.append('G.add_edge("%s", "%s", relation="%s")' % (u, v, rel))
            lines.append("")
        if assertion_edges:
            lines.append("# --- Aserciones entre individuos ---")
            for rel, u, v, k in assertion_edges:
                lines.append('G.add_edge("%s", "%s", relation="%s")' % (u, v, rel))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def to_node_link(self) -> dict:
        return nx.node_link_data(self.G, edges="links")

    def write_graphml(self, path: str) -> None:
        def flat(d: dict) -> dict:
            return {k: (json.dumps(v, ensure_ascii=False)
                        if isinstance(v, (dict, list)) else v)
                    for k, v in d.items() if v is not None}
        H = nx.MultiDiGraph()
        for n, d in self.G.nodes(data=True):
            H.add_node(n, **flat(d))
        for u, v, k, d in self.G.edges(keys=True, data=True):
            H.add_edge(u, v, key=str(k), **flat(d))
        nx.write_graphml(H, path)

    def to_turtle(self, namespace: str = "http://www.example.org/test#") -> str:
        """Export opcional a Turtle/OWL.

        No forma parte del metodo (el destino aqui es NetworkX), pero permite
        comparar 1:1 con el resultado original del paper y abrir la ontologia
        en Protege / OOPS! o pasarle un razonador."""
        out = [
            "@prefix : <%s> ." % namespace,
            "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
            "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
            "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
            "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
            "",
            "<%s> a owl:Ontology ." % namespace.rstrip("#"),
            "",
        ]
        char_map = {c: "owl:%sProperty" % c for c in CHARACTERISTICS}

        def term(n: str) -> str:
            return n if n in XSD_TYPES else ":%s" % n

        def restriction_bnode(r: str) -> str:
            parts = []
            for p in self.targets(r, "onProperty"):
                parts.append("owl:onProperty :%s" % p)
            for filler in RESTRICTION_FILLERS:
                for t in self.targets(r, filler):
                    parts.append("owl:%s %s" % (filler, term(t)))
            return "[ a owl:Restriction ; %s ]" % " ; ".join(parts)

        for c in self.classes():
            d = self.G.nodes[c]
            out.append(":%s a owl:Class" % c)
            for s in self.targets(c, "subClassOf"):
                out.append("    ; rdfs:subClassOf :%s" % s)
            for e in self.targets(c, "equivalentClass"):
                out.append("    ; owl:equivalentClass %s"
                           % (restriction_bnode(e)
                              if self.G.nodes[e].get("type") == "Restriction"
                              else ":%s" % e))
            for o in self.targets(c, "disjointWith"):
                out.append("    ; owl:disjointWith :%s" % o)
            if d.get("comment"):
                out.append('    ; rdfs:comment "%s"' % _esc(d["comment"]))
            out.append("    .")
        out.append("")

        for r in self.restrictions():
            for target in self.targets(r, "subClassOf"):
                out.append("%s rdfs:subClassOf :%s ."
                           % (restriction_bnode(r), target))
        out.append("")

        for p in self.properties():
            d = self.G.nodes[p]
            kind = ("owl:ObjectProperty" if d["type"] == "ObjectProperty"
                    else "owl:DatatypeProperty")
            types = [kind] + [char_map[c] for c in (d.get("characteristics") or [])
                              if c in char_map]
            out.append(":%s a %s" % (p, ", ".join(types)))
            for dom in self.targets(p, "domain"):
                out.append("    ; rdfs:domain :%s" % dom)
            for rng in self.targets(p, "range"):
                out.append("    ; rdfs:range %s" % term(rng))
            for sup in self.targets(p, "subPropertyOf"):
                out.append("    ; rdfs:subPropertyOf :%s" % sup)
            for inv in self.targets(p, "inverseOf"):
                out.append("    ; owl:inverseOf :%s" % inv)
            out.append("    .")
        out.append("")

        dt_props = set(self.nodes_of_type("DatatypeProperty"))
        for i in self.individuals():
            d = self.G.nodes[i]
            out.append(":%s a owl:NamedIndividual, :%s ." % (i, d["type"]))
            for _, v, e in sorted(self.G.out_edges(i, data=True),
                                  key=lambda t: (t[2].get("relation") or "", t[1])):
                rel = e.get("relation")
                if rel and rel not in SCHEMA_RELATIONS:
                    out.append(":%s :%s :%s ." % (i, rel, v))
            for key in sorted(d):
                if key not in RESERVED_NODE_ATTRS:
                    lit = ('"%s"' % _esc(d[key]) if isinstance(d[key], str)
                           else '"%s"' % d[key])
                    if key in dt_props or True:
                        out.append(":%s :%s %s ." % (i, key, lit))
        return "\n".join(out) + "\n"


def _esc(text: Any) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _lit(value: Any) -> str:
    if isinstance(value, str):
        return '"%s"' % _esc(value)
    if isinstance(value, (list, tuple)):
        return "[%s]" % ", ".join(_lit(v) for v in value)
    return repr(value)
