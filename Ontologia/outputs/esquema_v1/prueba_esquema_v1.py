"""Prueba del esquema v1.0 sobre el grafo anotado (gold_anotado.json).

1. Ejecuta las 8 CQs (consultas.py) sobre lo anotado hasta ahora.
2. Comprueba que conformidad.py DETECTA errores: inyecta seis fallos tipicos
   de extraccion en una copia del grafo y verifica que se reporta cada uno.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import consultas as Q  # noqa: E402
from conformidad import cargar_json, comprobar  # noqa: E402

CORPUS = HERE.parents[2] / "Extraccion_ADR" / "corpus_tesis"
G = cargar_json(CORPUS / "anotacion" / "gold_anotado.json")

print("== CQs sobre el grafo anotado ==")
for a in Q.adrs(G):
    print(a.split("__")[-1], Q.cq1_decision(G, a)["decision"][:50])
    print("   CQ2", [(x["alternativa"][:25], x["elegida"]) for x in Q.cq2_alternativas(G, a)])
    print("   CQ3", Q.cq3_estado(G, a))
    print("   CQ4", Q.cq4_tema(G, a))
    print("   CQ7 en 2024-01-01:", Q.cq7_estado_en_fecha(G, a, "2024-01-01"))
print("CQ5", Q.cq5_reemplazos(G))
print("CQ6", Q.cq6_enmiendas(G))
for t in [n for n, d in G.nodes(data=True) if d.get("type") == "Topic"]:
    print("CQ8", Q.cq8_vigente(G, t))

print("\n== El validador detecta errores inyectados ==")
dec = next(n for n, d in G.nodes(data=True) if d.get("type") == "Decision")
adr = next(u for u, _, d in G.in_edges(dec, data=True) if d.get("relation") == "recordsDecision")
alt = next(n for n, d in G.nodes(data=True) if d.get("type") == "Alternative")
casos = {
    "no_fundamentado": lambda H: H.nodes[dec].update(evidenceSnippet="We chose Kubernetes"),
    "vocabulario": lambda H: H.add_node("status_maybe", type="NormalizedStatus"),
    "atributo": lambda H: H.nodes[adr].update(priority="high"),
    "rango": lambda H: H.add_edge(adr, dec, relation="considersAlternative"),
    "funcional": lambda H: [H.add_node("dec2", type="Decision", decisionDescription="x", evidenceSnippet="x"),
                            H.add_edge(adr, "dec2", relation="recordsDecision")],
    "sin_evidencia": lambda H: H.nodes[alt].pop("evidenceSnippet"),
}
fallos = 0
for esperado, romper in casos.items():
    H = G.copy()
    romper(H)
    tipos = {h.tipo for h in comprobar(H, CORPUS / "adr")[0] if h.severidad == "error"}
    ok = esperado in tipos
    fallos += not ok
    print("  %-16s %s" % (esperado, "detectado" if ok else "NO DETECTADO (%s)" % tipos))
print("Resultado:", "OK" if not fallos else "%d casos sin detectar" % fallos)
sys.exit(1 if fallos else 0)
