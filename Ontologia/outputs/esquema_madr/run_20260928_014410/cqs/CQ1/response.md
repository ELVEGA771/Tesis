```python
import networkx as nx

G = nx.MultiDiGraph()

# ¿Es esta CQ respondible con el grafo anterior? -> no, el grafo estaba vacio

# --- Tipos de Datos ---
G.add_node("xsd:string", type="Datatype")

# --- Clases principales y jerarquia ---
G.add_node("Repository", type="Class")
G.add_node("Document", type="Class")
G.add_node("ADR", type="Class")
G.add_edge("ADR", "Document", relation="subClassOf")

G.add_node("Statement", type="Class")
G.add_node("DecisionStatement", type="Class")
G.add_edge("DecisionStatement", "Statement", relation="subClassOf")

# --- Propiedades de Objeto ---
# Un ADR pertenece a un Repositorio
G.add_node("belongsToRepository", type="ObjectProperty")
G.add_edge("belongsToRepository", "ADR", relation="domain")
G.add_edge("belongsToRepository", "Repository", relation="range")

# ADR y la afirmacion de decision que registra
G.add_node("hasDecisionStatement", type="ObjectProperty")
G.add_edge("hasDecisionStatement", "ADR", relation="domain")
G.add_edge("hasDecisionStatement", "DecisionStatement", relation="range")

# --- Propiedades de Datos ---
# Atributos de ADR y Repository
G.add_node("repositoryName", type="DatatypeProperty", characteristics=["Functional"])
G.add_edge("repositoryName", "Repository", relation="domain")
G.add_edge("repositoryName", "xsd:string", relation="range")

G.add_node("fileName", type="DatatypeProperty", characteristics=["Functional"])
G.add_edge("fileName", "ADR", relation="domain")
G.add_edge("fileName", "xsd:string", relation="range")

G.add_node("title", type="DatatypeProperty")
G.add_edge("title", "ADR", relation="domain")
G.add_edge("title", "xsd:string", relation="range")

# Atributos de la declaracion de decision (DecisionStatement)
G.add_node("decisionSummary", type="DatatypeProperty")
G.add_edge("decisionSummary", "DecisionStatement", relation="domain")
G.add_edge("decisionSummary", "xsd:string", relation="range")

# Requisito adicional: fragmento literal de evidencia
G.add_node("evidenceText", type="DatatypeProperty")
G.add_edge("evidenceText", "Statement", relation="domain")
G.add_edge("evidenceText", "xsd:string", relation="range")

# --- Restricciones ---
# Todo ADR debe pertenecer a al menos un repositorio
G.add_node("R_adr_belongsToRepository", type="Restriction")
G.add_edge("R_adr_belongsToRepository", "belongsToRepository", relation="onProperty")
G.add_edge("R_adr_belongsToRepository", "Repository", relation="someValuesFrom")
G.add_edge("R_adr_belongsToRepository", "ADR", relation="subClassOf")

# Todo ADR registra al menos una DecisionStatement
G.add_node("R_adr_hasDecision", type="Restriction")
G.add_edge("R_adr_hasDecision", "hasDecisionStatement", relation="onProperty")
G.add_edge("R_adr_hasDecision", "DecisionStatement", relation="someValuesFrom")
G.add_edge("R_adr_hasDecision", "ADR", relation="subClassOf")
```