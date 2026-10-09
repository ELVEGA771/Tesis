import networkx as nx

G = nx.MultiDiGraph()

# ¿Es esta CQ respondible con el grafo anterior? -> no, porque el grafo no contiene clases ni propiedades para representar las alternativas consideradas ni la alternativa seleccionada.
# Clases pivote (reificacion) que voy a crear en esta etapa: ninguna

# --- Clases ---
G.add_node("ADR", type="Class")
G.add_node("Alternative", type="Class")
G.add_node("DecisionStatement", type="Class")
G.add_node("Document", type="Class")
G.add_node("Repository", type="Class")
G.add_node("Statement", type="Class")

# --- Object properties ---
G.add_node("belongsToRepository", type="ObjectProperty")
G.add_node("considersAlternative", type="ObjectProperty")
G.add_node("hasDecisionStatement", type="ObjectProperty")
G.add_node("selectsAlternative", type="ObjectProperty")

# --- Datatype properties ---
G.add_node("alternativeName", type="DatatypeProperty")
G.add_node("decisionSummary", type="DatatypeProperty")
G.add_node("evidenceText", type="DatatypeProperty")
G.add_node("fileName", type="DatatypeProperty", characteristics=["Functional"])
G.add_node("repositoryName", type="DatatypeProperty", characteristics=["Functional"])
G.add_node("title", type="DatatypeProperty")

# --- Tipos de dato ---
G.add_node("xsd:string", type="Datatype")

# --- Restricciones ---
G.add_node("R_adr_belongsToRepository", type="Restriction")
G.add_node("R_adr_hasDecision", type="Restriction")

# --- Axiomas ---
G.add_edge("ADR", "Document", relation="subClassOf")
G.add_edge("Alternative", "Statement", relation="subClassOf")
G.add_edge("DecisionStatement", "Statement", relation="subClassOf")
G.add_edge("R_adr_belongsToRepository", "ADR", relation="subClassOf")
G.add_edge("R_adr_hasDecision", "ADR", relation="subClassOf")
G.add_edge("selectsAlternative", "considersAlternative", relation="subPropertyOf")

# Dominios y rangos
G.add_edge("belongsToRepository", "ADR", relation="domain")
G.add_edge("belongsToRepository", "Repository", relation="range")

G.add_edge("considersAlternative", "ADR", relation="domain")
G.add_edge("considersAlternative", "Alternative", relation="range")

G.add_edge("hasDecisionStatement", "ADR", relation="domain")
G.add_edge("hasDecisionStatement", "DecisionStatement", relation="range")

G.add_edge("selectsAlternative", "ADR", relation="domain")
G.add_edge("selectsAlternative", "Alternative", relation="range")

G.add_edge("alternativeName", "Alternative", relation="domain")
G.add_edge("alternativeName", "xsd:string", relation="range")

G.add_edge("decisionSummary", "DecisionStatement", relation="domain")
G.add_edge("decisionSummary", "xsd:string", relation="range")

G.add_edge("evidenceText", "Statement", relation="domain")
G.add_edge("evidenceText", "xsd:string", relation="range")

G.add_edge("fileName", "ADR", relation="domain")
G.add_edge("fileName", "xsd:string", relation="range")

G.add_edge("repositoryName", "Repository", relation="domain")
G.add_edge("repositoryName", "xsd:string", relation="range")

G.add_edge("title", "ADR", relation="domain")
G.add_edge("title", "xsd:string", relation="range")

# Restricciones
G.add_edge("R_adr_belongsToRepository", "belongsToRepository", relation="onProperty")
G.add_edge("R_adr_belongsToRepository", "Repository", relation="someValuesFrom")
G.add_edge("R_adr_hasDecision", "hasDecisionStatement", relation="onProperty")
G.add_edge("R_adr_hasDecision", "DecisionStatement", relation="someValuesFrom")