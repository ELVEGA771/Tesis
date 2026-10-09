```python
import networkx as nx

G = nx.MultiDiGraph()

# ¿Es esta CQ respondible con el grafo anterior? -> no, porque no existía la clase Alternative ni propiedades para registrar las alternativas consideradas y seleccionadas por un ADR.
# Clases pivote (reificacion) que voy a crear en esta etapa: ninguna

# --- Clases ---
G.add_node("Alternative", type="Class")
G.add_node("ArchitectureDecisionRecord", type="Class")
G.add_node("Decision", type="Class")
G.add_node("Document", type="Class")
G.add_node("Repository", type="Class")

# --- Object properties ---
G.add_node("belongsToRepository", type="ObjectProperty", characteristics=["Functional"])
G.add_node("considersAlternative", type="ObjectProperty")
G.add_node("containsADR", type="ObjectProperty")
G.add_node("recordsDecision", type="ObjectProperty")
G.add_node("selectsAlternative", type="ObjectProperty")

# --- Datatype properties ---
G.add_node("alternativeDescription", type="DatatypeProperty")
G.add_node("decisionDescription", type="DatatypeProperty")
G.add_node("evidenceSnippet", type="DatatypeProperty")
G.add_node("fileName", type="DatatypeProperty", characteristics=["Functional"])
G.add_node("repositoryName", type="DatatypeProperty", characteristics=["Functional"])
G.add_node("title", type="DatatypeProperty", characteristics=["Functional"])

# --- Tipos de dato ---
G.add_node("xsd:string", type="Datatype")

# --- Axiomas ---
G.add_edge("ArchitectureDecisionRecord", "Document", relation="subClassOf")
G.add_edge("alternativeDescription", "Alternative", relation="domain")
G.add_edge("alternativeDescription", "xsd:string", relation="range")
G.add_edge("belongsToRepository", "ArchitectureDecisionRecord", relation="domain")
G.add_edge("belongsToRepository", "Repository", relation="range")
G.add_edge("belongsToRepository", "containsADR", relation="inverseOf")
G.add_edge("considersAlternative", "ArchitectureDecisionRecord", relation="domain")
G.add_edge("considersAlternative", "Alternative", relation="range")
G.add_edge("containsADR", "Repository", relation="domain")
G.add_edge("containsADR", "ArchitectureDecisionRecord", relation="range")
G.add_edge("decisionDescription", "Decision", relation="domain")
G.add_edge("decisionDescription", "xsd:string", relation="range")
G.add_edge("evidenceSnippet", "Alternative", relation="domain")
G.add_edge("evidenceSnippet", "Decision", relation="domain")
G.add_edge("evidenceSnippet", "xsd:string", relation="range")
G.add_edge("fileName", "ArchitectureDecisionRecord", relation="domain")
G.add_edge("fileName", "xsd:string", relation="range")
G.add_edge("recordsDecision", "ArchitectureDecisionRecord", relation="domain")
G.add_edge("recordsDecision", "Decision", relation="range")
G.add_edge("repositoryName", "Repository", relation="domain")
G.add_edge("repositoryName", "xsd:string", relation="range")
G.add_edge("selectsAlternative", "ArchitectureDecisionRecord", relation="domain")
G.add_edge("selectsAlternative", "Alternative", relation="range")
G.add_edge("selectsAlternative", "considersAlternative", relation="subPropertyOf")
G.add_edge("title", "ArchitectureDecisionRecord", relation="domain")
G.add_edge("title", "xsd:string", relation="range")
```