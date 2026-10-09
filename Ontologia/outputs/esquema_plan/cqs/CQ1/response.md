```python
import networkx as nx

G = nx.MultiDiGraph()

# --- Tipos de datos ---
G.add_node("xsd:string", type="Datatype")

# --- Clases ---
G.add_node("Document", type="Class")
G.add_node("ArchitectureDecisionRecord", type="Class")
G.add_node("Repository", type="Class")
G.add_node("Decision", type="Class")

# --- Jerarquias (subClassOf) ---
G.add_edge("ArchitectureDecisionRecord", "Document", relation="subClassOf")

# --- Propiedades de objeto ---
# ADR pertenece a un Repositorio
G.add_node("belongsToRepository", type="ObjectProperty", characteristics=["Functional"])
G.add_edge("belongsToRepository", "ArchitectureDecisionRecord", relation="domain")
G.add_edge("belongsToRepository", "Repository", relation="range")

# Relacion inversa: el repositorio contiene ADRs
G.add_node("containsADR", type="ObjectProperty")
G.add_edge("containsADR", "Repository", relation="domain")
G.add_edge("containsADR", "ArchitectureDecisionRecord", relation="range")
G.add_edge("belongsToRepository", "containsADR", relation="inverseOf")

# Enlace hacia la clase Decision desde ArchitectureDecisionRecord
G.add_node("recordsDecision", type="ObjectProperty")
G.add_edge("recordsDecision", "ArchitectureDecisionRecord", relation="domain")
G.add_edge("recordsDecision", "Decision", relation="range")

# --- Propiedades de datos ---
# Propiedades de ArchitectureDecisionRecord
G.add_node("fileName", type="DatatypeProperty", characteristics=["Functional"])
G.add_edge("fileName", "ArchitectureDecisionRecord", relation="domain")
G.add_edge("fileName", "xsd:string", relation="range")

G.add_node("title", type="DatatypeProperty", characteristics=["Functional"])
G.add_edge("title", "ArchitectureDecisionRecord", relation="domain")
G.add_edge("title", "xsd:string", relation="range")

# Propiedades de Repository
G.add_node("repositoryName", type="DatatypeProperty", characteristics=["Functional"])
G.add_edge("repositoryName", "Repository", relation="domain")
G.add_edge("repositoryName", "xsd:string", relation="range")

# Propiedades de Decision
G.add_node("decisionDescription", type="DatatypeProperty")
G.add_edge("decisionDescription", "Decision", relation="domain")
G.add_edge("decisionDescription", "xsd:string", relation="range")

G.add_node("evidenceSnippet", type="DatatypeProperty")
G.add_edge("evidenceSnippet", "Decision", relation="domain")
G.add_edge("evidenceSnippet", "xsd:string", relation="range")
```