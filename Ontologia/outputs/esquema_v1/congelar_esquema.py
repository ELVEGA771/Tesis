"""Congela el esquema v1.0 a partir de la corrida CQbyCQ `esquema_plan`.

La corrida `esquema_plan` (CQs derivadas de las metricas de la planificacion)
se probo con cadenas reales del corpus. De esa prueba salieron cuatro ajustes
de ingenieria que no cambian lo que el esquema responde, pero si su
correccion formal y su uso por un extractor:

  A1. `evidenceSnippet` tenia 5 dominios. En OWL varios dominios son una
      INTERSECCION (todo lo que tuviera evidencia seria a la vez Decision,
      Alternative, ...). Se crea la superclase `GroundedElement` y pasa a ser
      el unico dominio. `Topic` queda fuera: el tema se asigna, no se extrae.
  A2. `NormalizedStatus` no tenia valores. Se fijan 6 individuos de
      vocabulario (se anade `rejected`, que aparece en el corpus y faltaba).
  A3. `relationType` era texto libre. Pasa a ObjectProperty hacia la clase
      `RelationType`, con dos individuos: supersedes y amends.
  A4. `recordsDecision` y `selectsAlternative` pasan a Functional: un ADR
      registra una decision y adopta como mucho una alternativa.

Ademas se documentan en `comment` las convenciones de uso (direccion de
las relaciones, origen de la fecha, identificadores).

La corrida original no se modifica. Ejecutar de nuevo regenera esta carpeta.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from cqbycq.dsl import load_graph

SRC = ROOT / "outputs" / "esquema_plan"
NAMESPACE = "http://example.org/adr-decision-graph#"

o = load_graph(SRC / "ontology.py")
G = o.G
antes = o.summary()
cambios = []


def log(msg):
    cambios.append(msg)


def quitar_arista(u, v, rel):
    for k, d in list(G.get_edge_data(u, v, default={}).items()):
        if d.get("relation") == rel:
            G.remove_edge(u, v, k)
            return
    sys.exit("ABORTADO: no existe la arista %s -%s-> %s" % (u, rel, v))


# --- A1: GroundedElement ----------------------------------------------------
G.add_node("GroundedElement", type="Class",
           comment="Elemento extraido del texto de un ADR. Toda instancia debe "
                   "llevar evidenceSnippet con un fragmento literal de su ADR de "
                   "origen; si el fragmento no aparece en el fichero, el nodo es "
                   "no fundamentado.")
for c in ("Decision", "Alternative", "StatusDeclaration", "RelationDeclaration"):
    G.add_edge(c, "GroundedElement", relation="subClassOf")
for c in ("Alternative", "Decision", "RelationDeclaration", "StatusDeclaration", "Topic"):
    quitar_arista("evidenceSnippet", c, "domain")
G.add_edge("evidenceSnippet", "GroundedElement", relation="domain")
log("A1: GroundedElement como unico dominio de evidenceSnippet (antes 5 dominios); Topic excluido")

# --- A2: vocabulario de estados ----------------------------------------------
ESTADOS = ["proposed", "accepted", "rejected", "deprecated", "superseded", "amended"]
for e in ESTADOS:
    G.add_node("status_" + e, type="NormalizedStatus")
log("A2: NormalizedStatus con 6 individuos fijos: " + ", ".join(ESTADOS))

# --- A3: relationType controlado ----------------------------------------------
quitar_arista("relationType", "xsd:string", "range")
G.nodes["relationType"]["type"] = "ObjectProperty"
G.add_node("RelationType", type="Class",
           comment="Vocabulario cerrado de tipos de relacion entre ADR.")
G.add_edge("relationType", "RelationType", relation="range")
G.add_node("reltype_supersedes", type="RelationType",
           comment="El ADR origen deja sin vigencia al ADR destino "
                   "(superseded by / supersedes / replaces).")
G.add_node("reltype_amends", type="RelationType",
           comment="El ADR origen modifica al ADR destino, que sigue vigente "
                   "(amends / amended by).")
log("A3: relationType pasa de DatatypeProperty (xsd:string) a ObjectProperty -> RelationType {supersedes, amends}")

# --- A4: funcionalidad ------------------------------------------------------
for p in ("recordsDecision", "selectsAlternative"):
    ch = list(G.nodes[p].get("characteristics") or [])
    if "Functional" not in ch:
        G.nodes[p]["characteristics"] = ch + ["Functional"]
log("A4: recordsDecision y selectsAlternative pasan a Functional")

# --- Convenciones documentadas en el propio esquema ----------------------------
COMENTARIOS = {
    "ArchitectureDecisionRecord": "Identidad = repositorio + fileName; el numero declarado no es clave. "
                                  "El id de nodo es un slug de ambos.",
    "RelationDeclaration": "Una por cada documento que afirma la relacion. Si los dos ADR la declaran, hay dos.",
    "sourceADR": "Va desde el ADR NUEVO (el que reemplaza o enmienda) hacia la declaracion.",
    "targetADR": "Va desde el ADR AFECTADO (el reemplazado o enmendado) hacia la declaracion.",
    "declaresRelation": "Va desde el documento que contiene la afirmacion hacia la declaracion.",
    "recordDate": "Se toma de los metadatos del corpus (corpus_manifest.csv / git), no la extrae el LLM.",
    "Topic": "Se asigna, no se extrae: los ADR de una misma cadena de reemplazo comparten el mismo nodo Topic.",
    "selectsAlternative": "Si la opcion elegida no figura entre las listadas, se crea como Alternative "
                          "con la frase de la eleccion como evidencia.",
}
for n, c in COMENTARIOS.items():
    G.nodes[n]["comment"] = c
log("Convenciones documentadas como comment en: " + ", ".join(COMENTARIOS))

# --- Validacion y escritura -----------------------------------------------------
problemas = o.validate()
if problemas:
    sys.exit("ABORTADO: el esquema no valida:\n" + "\n".join("  " + p.render() for p in problemas))
despues = o.summary()

(HERE / "ontology.py").write_text(o.to_script(), encoding="utf-8")
(HERE / "ontology.json").write_text(json.dumps(o.to_node_link(), indent=2, ensure_ascii=False), encoding="utf-8")
o.write_graphml(str(HERE / "ontology.graphml"))
(HERE / "ontology.ttl").write_text(o.to_turtle(NAMESPACE), encoding="utf-8")
(HERE / "summary.json").write_text(json.dumps({
    "version": "1.0",
    "derivada_de": "outputs/esquema_plan (CQbyCQ, historia inputs/historia_adr_plan.json)",
    "cambios": cambios,
    "summary_antes": antes,
    "summary": despues,
}, indent=2, ensure_ascii=False), encoding="utf-8")

print("Esquema v1.0 congelado. Validacion: OK")
for c in cambios:
    print("  -", c)
for k in ("classes", "object_properties", "datatype_properties", "subclass_axioms", "individuals", "nodes", "edges"):
    print("  %-20s %3d -> %3d" % (k, antes[k], despues[k]))
