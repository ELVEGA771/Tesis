"""Pruebas sin API: validador, convencion, regresion y exportaciones.

    python tests/test_offline.py

No llama al LLM: simula sus salidas para poder comprobar el resto del pipeline
de forma determinista.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cqbycq.config import Config  # noqa: E402
from cqbycq.criteria import criteria_score, evaluate_criteria  # noqa: E402
from cqbycq.dsl import extract_code, load_graph, validate_and_execute  # noqa: E402
from cqbycq.prompts import PromptBuilder, render  # noqa: E402
from cqbycq.story import Story  # noqa: E402

FAILURES: list[str] = []

HEAD = "import networkx as nx\n\nG = nx.MultiDiGraph()\n"


def check(condition: bool, label: str) -> None:
    print(("  OK   " if condition else "  FALLO") + "  " + label)
    if not condition:
        FAILURES.append(label)


def kinds(result) -> set[str]:
    return {i.kind for i in result.issues}


# --------------------------------------------------------------------- #

def test_level1_syntax() -> None:
    print("\n[1] Nivel 1: sintaxis y lista blanca")

    good = HEAD + '''
# ¿Es esta CQ respondible con el grafo anterior? -> no
G.add_node("ADR", type="Class")
G.add_node("Ticket", type="Class")
G.add_node("vinculadoA", type="ObjectProperty")
G.add_edge("vinculadoA", "ADR", relation="domain")
G.add_edge("vinculadoA", "Ticket", relation="range")
G.add_node("identificador", type="DatatypeProperty")
G.add_edge("identificador", "ADR", relation="domain")
G.add_edge("identificador", "xsd:integer", relation="range")
G.add_node("xsd:integer", type="Datatype")
'''
    res = validate_and_execute(good)
    check(not res.syntax_issues, "script valido: sin errores de nivel 1")
    check(res.applied == 9, "9 llamadas aplicadas (%d)" % res.applied)
    check(not res.convention_issues,
          "script valido: sin errores de convencion -> %s"
          % [i.render() for i in res.convention_issues][:2])

    bad = {
        "import os": "import no permitido",
        "x = 1": "asignacion arbitraria rechazada",
        "for i in range(3): pass": "bucle rechazado",
        "print('hola')": "llamada fuera de G rechazada",
        'G.remove_node("A")': "metodo de G no permitido rechazado",
        'G.add_node(nombre, type="Class")': "argumento no literal rechazado",
        'G.add_node("A"': "error de sintaxis Python detectado",
        'G.add_edge("A")': "add_edge con un solo extremo rechazado",
    }
    for code, label in bad.items():
        r = validate_and_execute(HEAD + code)
        check(bool(r.syntax_issues), label)

    check(not validate_and_execute(HEAD).syntax_issues,
          "se admite `import networkx as nx` y `G = nx.MultiDiGraph()`")
    check(not validate_and_execute(
        "import networkx as nx\nG = nx.DiGraph()\n").syntax_issues,
        "se admite nx.DiGraph() y se promociona a MultiDiGraph")

    check(extract_code("bla\n```python\nG.add_node(\"A\")\n```\nfin")
          == 'G.add_node("A")', "extraccion del bloque de codigo")
    check('G.add_node("A")' in extract_code(
        'Aqui tienes:\nG.add_node("A")'),
        "extraccion sin fences filtrando la prosa")


def test_level2_convention() -> None:
    print("\n[2] Nivel 2: convencion de la ontologia")

    cases = [
        ('G.add_node("ADR")', "missing_type", "nodo sin `type`"),
        ('G.add_node("A", type="Class")\nG.add_node("B", type="Class")\n'
         'G.add_edge("A", "B")', "missing_relation", "arista sin `relation`"),
        ('G.add_node("ADR-001", type="ADR")', "unknown_type",
         "instancia de una clase no declarada"),
        ('G.add_node("A", type="Class")\nG.add_node("B", type="Class")\n'
         'G.add_edge("A", "B", relation="inventada")', "unknown_relation",
         "relacion inventada rechazada"),
        ('G.add_node("A", type="Class")\nG.add_node("p", type="ObjectProperty")\n'
         'G.add_edge("p", "A", relation="domain")', "no_range",
         "propiedad sin rango"),
        ('G.add_node("A", type="Class")\nG.add_node("p", type="ObjectProperty")\n'
         'G.add_edge("p", "A", relation="range")', "no_domain",
         "propiedad sin dominio"),
        ('G.add_node("A", type="Class")\nG.add_node("B", type="Class")\n'
         'G.add_node("p", type="DatatypeProperty")\n'
         'G.add_edge("p", "A", relation="domain")\n'
         'G.add_edge("p", "B", relation="range")', "bad_range",
         "DatatypeProperty con rango de clase"),
        ('G.add_node("A", type="Class")\nG.add_node("B", type="Class")\n'
         'G.add_edge("A", "B", relation="subClassOf")\n'
         'G.add_edge("B", "A", relation="subClassOf")', "hierarchy_cycle",
         "ciclo en la jerarquia"),
        ('G.add_node("Piv", type="Class", pivot=True)', "pivot",
         "clase pivote sin componentes que la apunten"),
        ('G.add_node("A", type="Class")\nG.add_node("R", type="Restriction")',
         "restriction", "restriccion incompleta"),
        ('G.add_node("A", type="Class", titulo="Hola")', "literal_on_class",
         "literal colgado de una clase"),
        ('G.add_node("A", type="Class")\nG.add_node("B", type="Class")\n'
         'G.add_node("p", type="ObjectProperty")\n'
         'G.add_edge("p", "A", relation="domain")\n'
         'G.add_edge("p", "B", relation="range")\n'
         'G.add_edge("A", "B", relation="p")', "bad_endpoint",
         "propiedad usada como arista directa entre clases"),
    ]
    for code, kind, label in cases:
        r = validate_and_execute(HEAD + code)
        check(kind in kinds(r), "%s (esperaba '%s', salio %s)"
              % (label, kind, sorted(kinds(r))))


def reference_script() -> str:
    """Ontologia ADR de referencia, escrita en la convencion del prompt."""
    return HEAD + '''
# --- Clases ---
G.add_node("Artefacto", type="Class")
G.add_node("ADR", type="Class", comment="Registro de decision arquitectonica")
G.add_node("Ticket", type="Class")
G.add_node("Alternativa", type="Class")
G.add_node("Argumento", type="Class")
G.add_node("Pro", type="Class")
G.add_node("Contra", type="Class")
G.add_node("Estado", type="Class")
G.add_node("EvaluacionAlternativa", type="Class", pivot=True)
G.add_node("EntradaDeEstado", type="Class", pivot=True)

# --- Taxonomia ---
G.add_edge("ADR", "Artefacto", relation="subClassOf")
G.add_edge("Ticket", "Artefacto", relation="subClassOf")
G.add_edge("Pro", "Argumento", relation="subClassOf")
G.add_edge("Contra", "Argumento", relation="subClassOf")
G.add_edge("Pro", "Contra", relation="disjointWith")

# --- Propiedades de objeto ---
G.add_node("vinculadoATicket", type="ObjectProperty")
G.add_edge("vinculadoATicket", "ADR", relation="domain")
G.add_edge("vinculadoATicket", "Ticket", relation="range")
G.add_node("selectsAlternative", type="ObjectProperty", characteristics=["Functional"])
G.add_edge("selectsAlternative", "ADR", relation="domain")
G.add_edge("selectsAlternative", "Alternativa", relation="range")
G.add_node("evaluacionDeADR", type="ObjectProperty")
G.add_edge("evaluacionDeADR", "ADR", relation="domain")
G.add_edge("evaluacionDeADR", "EvaluacionAlternativa", relation="range")
G.add_node("evaluacionDeAlternativa", type="ObjectProperty")
G.add_edge("evaluacionDeAlternativa", "Alternativa", relation="domain")
G.add_edge("evaluacionDeAlternativa", "EvaluacionAlternativa", relation="range")
G.add_node("evaluacionConArgumento", type="ObjectProperty")
G.add_edge("evaluacionConArgumento", "Argumento", relation="domain")
G.add_edge("evaluacionConArgumento", "EvaluacionAlternativa", relation="range")
G.add_node("entradaDeADR", type="ObjectProperty")
G.add_edge("entradaDeADR", "ADR", relation="domain")
G.add_edge("entradaDeADR", "EntradaDeEstado", relation="range")
G.add_node("entradaConEstado", type="ObjectProperty")
G.add_edge("entradaConEstado", "Estado", relation="domain")
G.add_edge("entradaConEstado", "EntradaDeEstado", relation="range")
G.add_node("supersededBy", type="ObjectProperty")
G.add_edge("supersededBy", "ADR", relation="domain")
G.add_edge("supersededBy", "ADR", relation="range")

# --- Propiedades de datos ---
G.add_node("identificador", type="DatatypeProperty", characteristics=["Functional"])
G.add_edge("identificador", "ADR", relation="domain")
G.add_edge("identificador", "xsd:integer", relation="range")
G.add_node("titulo", type="DatatypeProperty")
G.add_edge("titulo", "ADR", relation="domain")
G.add_edge("titulo", "xsd:string", relation="range")
G.add_node("fechaDeEstado", type="DatatypeProperty")
G.add_edge("fechaDeEstado", "EntradaDeEstado", relation="domain")
G.add_edge("fechaDeEstado", "xsd:dateTime", relation="range")
G.add_node("xsd:integer", type="Datatype")
G.add_node("xsd:string", type="Datatype")
G.add_node("xsd:dateTime", type="Datatype")

# --- Restricciones ---
G.add_node("R_decidido", type="Restriction")
G.add_edge("R_decidido", "selectsAlternative", relation="onProperty")
G.add_edge("R_decidido", "Alternativa", relation="someValuesFrom")
G.add_node("ADRDecidido", type="Class")
G.add_edge("ADRDecidido", "R_decidido", relation="equivalentClass")
G.add_edge("ADRDecidido", "ADR", relation="subClassOf")

# --- Individuos ---
G.add_node("ADR-001", type="ADR", titulo="Usar PostgreSQL", identificador=1)
G.add_node("Alt-01", type="Alternativa")
G.add_edge("ADR-001", "Alt-01", relation="selectsAlternative")
'''


def test_reference_and_roundtrip() -> None:
    print("\n[3] Ontologia de referencia, round-trip y exportaciones")
    res = validate_and_execute(reference_script())
    check(not res.syntax_issues, "referencia: nivel 1 limpio")
    check(not res.convention_issues,
          "referencia: convencion limpia -> %s"
          % [i.render() for i in res.convention_issues][:3])
    g = res.graph

    s = g.summary()
    check(s["classes"] == 11, "11 clases (%d)" % s["classes"])
    check(s["pivot_classes"] == 2, "2 clases pivote")
    check(s["object_properties"] == 8, "8 propiedades de objeto (%d)"
          % s["object_properties"])
    check(s["datatype_properties"] == 3, "3 propiedades de datos")
    check(s["individuals"] == 2, "2 individuos")
    check(s["assertions"] == 1, "1 asercion entre individuos")

    script = g.to_script()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ontology.py"
        path.write_text(script, encoding="utf-8")
        back = load_graph(path)
        check(back.summary() == s, "round-trip grafo -> script -> grafo")
        check(back.to_script() == script, "el script canonico es estable")
        g.write_graphml(str(Path(tmp) / "o.graphml"))
        check((Path(tmp) / "o.graphml").stat().st_size > 0, "export GraphML")

    ttl = g.to_turtle()
    for needle, label in [
        ("owl:Restriction", "Turtle con restricciones"),
        ("owl:equivalentClass", "Turtle con equivalencias"),
        ("rdfs:subClassOf :Artefacto", "Turtle con taxonomia"),
        ("owl:FunctionalProperty", "Turtle con caracteristicas de propiedad"),
        ("rdfs:range xsd:integer", "Turtle con rangos xsd"),
        ("owl:NamedIndividual", "Turtle con individuos"),
    ]:
        check(needle in ttl, label)

    check(isinstance(g.to_node_link(), dict), "export node-link JSON")


def test_regression_detection() -> None:
    print("\n[4] Nivel 3: regresion entre etapas")
    previous = validate_and_execute(reference_script()).graph
    trimmed = validate_and_execute(
        reference_script().replace(
            'G.add_node("Ticket", type="Class")\n', "").replace(
            'G.add_edge("Ticket", "Artefacto", relation="subClassOf")\n', "").replace(
            'G.add_edge("vinculadoATicket", "Ticket", relation="range")\n', "")
    ).graph

    diff = trimmed.diff(previous)
    check("Ticket" in diff["removed_nodes"], "se detecta el nodo perdido")
    check(any("Ticket" in e for e in diff["removed_edges"]),
          "se detectan las aristas perdidas")
    check(not diff["added_nodes"], "no se inventan nodos anadidos")

    lost = trimmed.restore_from(previous)
    check(lost["removed_nodes"] == ["Ticket"], "restore_from informa de lo perdido")
    check(trimmed.summary() == previous.summary(),
          "tras restaurar, el grafo vuelve a estar completo")
    check(not trimmed.diff(previous)["removed_nodes"],
          "tras restaurar no queda nada perdido")


def test_criteria() -> None:
    print("\n[5] Criterios estructurales")
    g = validate_and_execute(reference_script()).graph
    criteria = {c.key: c for c in evaluate_criteria(g, syntax_clean=True)}
    for key in ("equivalent_class", "reification", "syntax", "domain_range",
                "hierarchy", "datatype_property", "instances"):
        check(criteria[key].passed, "criterio '%s' se cumple" % key)
    check(criteria["hierarchy_coherence"].needs_human,
          "la coherencia de la jerarquia queda marcada para revision humana")
    check(criteria_score(list(criteria.values())) == 1.0,
          "score estructural 1.00 en la ontologia de referencia")

    flat = validate_and_execute(
        HEAD + 'G.add_node("A", type="Class")\nG.add_node("B", type="Class")').graph
    flat_criteria = {c.key: c for c in evaluate_criteria(flat, True)}
    check(not flat_criteria["hierarchy"].passed,
          "sin taxonomia falla el criterio de jerarquia")
    check(not flat_criteria["reification"].passed,
          "sin clases pivote falla el criterio de reificacion")


def test_prompts() -> None:
    print("\n[6] Ensamblado del prompt")
    story = Story.load(Path(__file__).parent.parent / "inputs" / "historia_adr.json")
    check(len(story.competency_questions) == 7, "se cargan las 7 CQs del ADR")

    builder = PromptBuilder(Config(), story)
    blocks = builder.build_cq_prompt(story.competency_questions[2], HEAD, 3, 7)
    check(len(blocks) == 2, "el prompt se parte en prefijo cacheado + sufijo")
    check(blocks[0].get("cache_control", {}).get("type") == "ephemeral",
          "el prefijo constante lleva breakpoint de cache")
    full = "\n".join(b["text"] for b in blocks)

    for needle, label in [
        ("ENCABEZADO", "seccion header presente"),
        ("AYUDANTE", "seccion helper presente"),
        ("HISTORIA Y REQUISITOS COMPLETOS", "seccion story presente"),
        ("TAREA", "seccion task presente"),
        ("PIE DE PAGINA", "seccion footer presente"),
        ("GRAFO ACTUAL", "seccion memoria presente"),
        ("nx.MultiDiGraph()", "el esqueleto del script va en el prompt"),
        ("UsoDeRecurso", "el ejemplo de reificacion va en el helper"),
        ("subClassOf", "la jerarquia se explica en el helper"),
        ("xsd:dateTime", "los tipos de dato van en el helper"),
        ("allValuesFrom", "las restricciones se explican en el helper"),
        ("InverseFunctional", "las caracteristicas de propiedad van en el helper"),
        ("respondible", "el chequeo de respondibilidad va en el header"),
        (story.competency_questions[2].text, "la CQ de la etapa va en el prompt"),
        (story.competency_questions[5].text, "el resto de CQs van como contexto"),
        ("no elimines nada", "el footer insiste en no perder lo anterior"),
    ]:
        check(needle in full, label)

    check(full.index("# ENCABEZADO") < full.index("# AYUDANTE")
          < full.index("## HISTORIA Y REQUISITOS") < full.index("## TAREA")
          < full.index("# PIE DE PAGINA") < full.index("## GRAFO ACTUAL"),
          "orden de secciones: header, helper, story, task, footer, memoria")
    otros = builder.build_cq_prompt(story.competency_questions[5], HEAD, 6, 7)
    check(blocks[0]["text"] == otros[0]["text"],
          "el prefijo cacheado es identico entre etapas (la CQ va en el sufijo)")
    check(blocks[1]["text"] != otros[1]["text"],
          "el sufijo cambia con la CQ de la etapa")

    cfg = Config()
    cfg.llm_does_merge = False
    delta_prompt = "\n".join(
        b["text"] for b in PromptBuilder(cfg, story).build_cq_prompt(
            story.competency_questions[0], HEAD, 1, 7))
    check("solo los nodos" in delta_prompt,
          "en modo delta la memoria pide solo lo nuevo")

    try:
        render("hola {{falta}}", {})
        check(False, "un placeholder sin valor deberia fallar")
    except KeyError:
        check(True, "un placeholder sin valor falla de forma explicita")


def main() -> int:
    test_level1_syntax()
    test_level2_convention()
    test_reference_and_roundtrip()
    test_regression_detection()
    test_criteria()
    test_prompts()
    print("\n" + "=" * 60)
    if FAILURES:
        print("FALLOS (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("Todas las comprobaciones offline pasan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
