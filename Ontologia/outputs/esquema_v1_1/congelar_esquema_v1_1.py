"""Congela el esquema v1.1 a partir de la corrida CQbyCQ `esquema_madr`.

La corrida (historia inputs/historia_adr_v1_1.json: CQ1-CQ8 de la v1.0 + CQ9-CQ17,
una por elemento de MADR 4.0.0) cubre todos los elementos de MADR. Igual que en
la v1.0, se aplican ajustes de ingenieria que no cambian lo que el esquema
responde, pero si su correccion formal y su uso por un extractor:

  C1. Estados normalizados. La corrida los modelo como SUBCLASES de
      NormalizedStatus (AcceptedStatus, ...), sin `rejected`. Pasan a ser 6
      individuos fijos de vocabulario (mismo ajuste que A2 en la v1.0).
  C2. relationType era texto libre. Pasa a ObjectProperty hacia la clase
      RelationType, con tres individuos: supersedes, amends y relatesTo. El
      tercero lo pide la CQ17 ("relacionado sin reemplazar ni enmendar"), que
      la corrida resolvio solo como texto libre.
  C3. Funcionalidad: hasDecisionStatement, selectsAlternative,
      hasStatusStatement, hasNormalizedStatus, belongsToRepository,
      relationType y title pasan a Functional (mismo criterio que A4).
  C4. Polaridad y rol duplicados. La corrida modelo la polaridad de
      consecuencias y argumentos, y el rol de los participantes, dos veces:
      como subclases disjuntas y como texto libre. Se quitan
      consequencePolarity, argumentPolarity y participantRole; mandan las
      subclases.
  C5. La restriccion "todo ADR tiene estado" contradice la regla 5 de la
      historia (solo la decision es obligatoria). Se quita R_adr_hasStatus.

Ademas se documentan en `comment` las convenciones de uso. La corrida
original no se modifica. Ejecutar de nuevo regenera esta carpeta.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from cqbycq.dsl import load_graph

SRC = ROOT / "outputs" / "esquema_madr" / "run_20260928_014410"
NAMESPACE = "http://example.org/adr-decision-graph#"

o = load_graph(SRC / "ontology.py")
G = o.G
antes = o.summary()
cambios = []


def quitar_nodo(n):
    if n not in G:
        sys.exit("ABORTADO: no existe el nodo %s" % n)
    G.remove_node(n)


def funcional(p):
    ch = list(G.nodes[p].get("characteristics") or [])
    if "Functional" not in ch:
        G.nodes[p]["characteristics"] = ch + ["Functional"]


# --- C1: vocabulario de estados --------------------------------------------------
for c in ("AcceptedStatus", "AmendedStatus", "DeprecatedStatus", "ProposedStatus", "SupersededStatus"):
    quitar_nodo(c)
ESTADOS = ["proposed", "accepted", "rejected", "deprecated", "superseded", "amended"]
for e in ESTADOS:
    G.add_node("status_" + e, type="NormalizedStatus")
cambios.append("C1: NormalizedStatus pasa de 5 subclases a 6 individuos fijos: " + ", ".join(ESTADOS))

# --- C2: relationType controlado ---------------------------------------------------
for k, d in list(G.get_edge_data("relationType", "xsd:string", default={}).items()):
    if d.get("relation") == "range":
        G.remove_edge("relationType", "xsd:string", k)
G.nodes["relationType"]["type"] = "ObjectProperty"
G.add_node("RelationType", type="Class", comment="Vocabulario cerrado de tipos de relacion entre ADR.")
G.add_edge("relationType", "RelationType", relation="range")
G.add_node("reltype_supersedes", type="RelationType",
           comment="El ADR origen deja sin vigencia al ADR destino (superseded by / supersedes / replaces).")
G.add_node("reltype_amends", type="RelationType",
           comment="El ADR origen modifica al ADR destino, que sigue vigente (amends / amended by).")
G.add_node("reltype_relatesTo", type="RelationType",
           comment="El ADR origen menciona al destino como relacionado, sin reemplazarlo ni enmendarlo.")
cambios.append("C2: relationType pasa a ObjectProperty -> RelationType {supersedes, amends, relatesTo}")

# --- C3: funcionalidad -------------------------------------------------------------
FUNC = ["hasDecisionStatement", "selectsAlternative", "hasStatusStatement", "hasNormalizedStatus",
        "belongsToRepository", "relationType", "title"]
for p in FUNC:
    funcional(p)
cambios.append("C3: Functional en " + ", ".join(FUNC))

# --- C4: polaridad y rol duplicados ---------------------------------------------------
for p in ("consequencePolarity", "argumentPolarity", "participantRole"):
    quitar_nodo(p)
cambios.append("C4: se quitan consequencePolarity, argumentPolarity y participantRole (duplican las subclases)")

# --- C5: estado opcional ----------------------------------------------------------------
quitar_nodo("R_adr_hasStatus")
cambios.append("C5: se quita R_adr_hasStatus (regla 5: solo la decision es obligatoria)")

# --- Convenciones -----------------------------------------------------------------------
COMENTARIOS = {
    "ADR": "Identidad = repositorio + fileName; el numero declarado no es clave. El id de nodo es un slug de ambos.",
    "Statement": "Elemento extraido del texto de un ADR. Toda instancia lleva evidenceText con un fragmento "
                 "literal de su ADR; si el fragmento no aparece en el fichero, el nodo es no fundamentado.",
    "RelationshipStatement": "Una por cada documento que afirma la relacion. Si los dos ADR la declaran, hay dos.",
    "sourceADR": "Va desde el ADR NUEVO (el que reemplaza, enmienda o menciona) hacia la declaracion.",
    "targetADR": "Va desde el ADR AFECTADO (el reemplazado, enmendado o mencionado) hacia la declaracion.",
    "declaringADR": "Va desde el documento que contiene la afirmacion hacia la declaracion.",
    "registrationDate": "Fecha de registro: sale del historial de git (regla temporal E1/E2), no del texto.",
    "lastUpdateDate": "Fecha que declara el propio ADR (campo date de MADR): su ultima actualizacion.",
    "Topic": "Se asigna, no se extrae: los ADR de una misma cadena de reemplazo comparten el mismo nodo Topic.",
    "selectsAlternative": "Si la opcion elegida no figura entre las listadas, se crea como Alternative "
                          "con la frase de la eleccion como evidencia.",
    "argumentAlternative": "Va desde la alternativa hacia cada argumento que se da sobre ella.",
}
for n, c in COMENTARIOS.items():
    G.nodes[n]["comment"] = c

problemas = o.validate()
if problemas:
    sys.exit("ABORTADO: el esquema no valida:\n" + "\n".join("  " + p.render() for p in problemas))
despues = o.summary()

(HERE / "ontology.py").write_text(o.to_script(), encoding="utf-8")
(HERE / "ontology.json").write_text(json.dumps(o.to_node_link(), indent=2, ensure_ascii=False), encoding="utf-8")
o.write_graphml(str(HERE / "ontology.graphml"))
(HERE / "ontology.ttl").write_text(o.to_turtle(NAMESPACE), encoding="utf-8")
(HERE / "summary.json").write_text(json.dumps({
    "version": "1.1",
    "derivada_de": "outputs/esquema_madr/run_20260928_014410 (CQbyCQ, historia inputs/historia_adr_v1_1.json)",
    "cambios": cambios,
    "summary_antes": antes,
    "summary": despues,
}, indent=2, ensure_ascii=False), encoding="utf-8")

print("Esquema v1.1 congelado. Validacion: OK")
for c in cambios:
    print("  -", c)
for k in ("classes", "object_properties", "datatype_properties", "subclass_axioms", "individuals", "nodes", "edges"):
    print("  %-20s %3d -> %3d" % (k, antes[k], despues[k]))
