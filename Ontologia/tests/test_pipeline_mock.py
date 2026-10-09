"""Prueba del pipeline completo con un LLM simulado.

    python tests/test_pipeline_mock.py

Sustituye `LLMClient` por respuestas prefabricadas: comprueba el bucle CQ a CQ,
la memoria, los tres niveles de validacion, la deteccion de regresion y los
artefactos que se escriben en disco, sin gastar una sola llamada a la API.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cqbycq import llm as llm_module  # noqa: E402
from cqbycq.config import Config  # noqa: E402
from cqbycq.evaluate import score_from_worksheet  # noqa: E402
from cqbycq.llm import LLMResponse  # noqa: E402
from cqbycq.pipeline import CQbyCQPipeline  # noqa: E402
from cqbycq.story import Story  # noqa: E402

FAILURES: list[str] = []

HEAD = "import networkx as nx\n\nG = nx.MultiDiGraph()\n"


def check(condition: bool, label: str) -> None:
    print(("  OK   " if condition else "  FALLO") + "  " + label)
    if not condition:
        FAILURES.append(label)


def block(body: str) -> str:
    return "```python\n" + HEAD + body + "```"


CQ1 = '''
G.add_node("Artefacto", type="Class")
G.add_node("ADR", type="Class")
G.add_node("Ticket", type="Class")
G.add_edge("ADR", "Artefacto", relation="subClassOf")
G.add_edge("Ticket", "Artefacto", relation="subClassOf")
G.add_node("xsd:integer", type="Datatype")
G.add_node("xsd:string", type="Datatype")
G.add_node("identificador", type="DatatypeProperty")
G.add_edge("identificador", "ADR", relation="domain")
G.add_edge("identificador", "xsd:integer", relation="range")
G.add_node("titulo", type="DatatypeProperty")
G.add_edge("titulo", "ADR", relation="domain")
G.add_edge("titulo", "xsd:string", relation="range")
G.add_node("vinculadoATicket", type="ObjectProperty")
G.add_edge("vinculadoATicket", "ADR", relation="domain")
G.add_edge("vinculadoATicket", "Ticket", relation="range")
'''

# CQ2 olvida reproducir 'Ticket' (regresion) y deja una propiedad sin rango.
CQ2_MALA = CQ1.replace('G.add_node("Ticket", type="Class")\n', "") \
              .replace('G.add_edge("Ticket", "Artefacto", relation="subClassOf")\n', "") \
              .replace('G.add_edge("vinculadoATicket", "Ticket", relation="range")\n', "") + '''
G.add_node("Alternativa", type="Class")
G.add_node("selectsAlternative", type="ObjectProperty")
G.add_edge("selectsAlternative", "ADR", relation="domain")
'''

CQ2_BUENA = CQ1 + '''
G.add_node("Alternativa", type="Class")
G.add_node("Contexto", type="Class")
G.add_node("selectsAlternative", type="ObjectProperty", characteristics=["Functional"])
G.add_edge("selectsAlternative", "ADR", relation="domain")
G.add_edge("selectsAlternative", "Alternativa", relation="range")
G.add_node("planteaContexto", type="ObjectProperty")
G.add_edge("planteaContexto", "ADR", relation="domain")
G.add_edge("planteaContexto", "Contexto", relation="range")
'''

CQ3 = CQ2_BUENA + '''
G.add_node("Argumento", type="Class")
G.add_node("EvaluacionAlternativa", type="Class", pivot=True)
G.add_node("evaluacionDeADR", type="ObjectProperty")
G.add_edge("evaluacionDeADR", "ADR", relation="domain")
G.add_edge("evaluacionDeADR", "EvaluacionAlternativa", relation="range")
G.add_node("evaluacionDeAlternativa", type="ObjectProperty")
G.add_edge("evaluacionDeAlternativa", "Alternativa", relation="domain")
G.add_edge("evaluacionDeAlternativa", "EvaluacionAlternativa", relation="range")
G.add_node("evaluacionConArgumento", type="ObjectProperty")
G.add_edge("evaluacionConArgumento", "Argumento", relation="domain")
G.add_edge("evaluacionConArgumento", "EvaluacionAlternativa", relation="range")
'''

CANNED = {
    "CQ1": block(CQ1),
    "CQ2": "Claro, aqui tienes la ontologia:\n" + block(CQ2_MALA),
    "CQ2/repair1": block(CQ2_BUENA),
    "CQ3": block(CQ3),
}

SEEN: list[str] = []


class FakeLLM:
    """Doble de prueba de LLMClient."""

    def __init__(self, config, transcript_path=None, server_side_fallbacks=True):
        self.config = config
        self.transcript_path = transcript_path
        self.total_usage = {}
        self.prompts: dict[str, str] = {}

    def complete(self, content, system=None, tag=""):
        SEEN.append(tag)
        self.prompts[tag] = "\n".join(
            b["text"] for b in content) if isinstance(content, list) else content
        if tag not in CANNED:
            raise AssertionError("tag inesperado: %r" % tag)
        usage = {"input_tokens": 100, "output_tokens": 50}
        for k, v in usage.items():
            self.total_usage[k] = self.total_usage.get(k, 0) + v
        return LLMResponse(text=CANNED[tag], stop_reason="end_turn",
                           usage=usage, model="fake-model")


STORY = {
    "id": "adr-mini",
    "title": "ADR (mini)",
    "story": "Un ADR tiene identificador y titulo, puede ir ligado a un ticket, "
             "plantea un contexto y evalua alternativas con pros y contras.",
    "competency_questions": [
        {"id": "CQ1", "text": "Identificador, titulo y ticket de un ADR?"},
        {"id": "CQ2", "text": "Contexto y alternativa elegida de un ADR?"},
        {"id": "CQ3", "text": "Pros y contras de cada alternativa de un ADR?"},
    ],
}


def main() -> int:
    llm_module.LLMClient = FakeLLM
    import cqbycq.pipeline as pipeline_module
    pipeline_module.LLMClient = FakeLLM

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        story_path = tmp_path / "story.json"
        story_path.write_text(json.dumps(STORY, ensure_ascii=False), encoding="utf-8")
        story = Story.load(story_path)

        config = Config(output_dir=str(tmp_path / "out"), run_id="run_test")
        pipeline = CQbyCQPipeline(config, story, verbose=False)
        result = pipeline.run()
        run_dir = result.run_dir

        print("\n[1] Bucle CQ a CQ")
        check(SEEN == ["CQ1", "CQ2", "CQ2/repair1", "CQ3"],
              "orden de etapas y correccion: %s" % SEEN)
        check(len(result.memory.records) == 3, "3 etapas registradas")

        print("\n[2] Deteccion de regresion y correccion")
        rec2 = result.memory.records[1]
        check(rec2.repair_rounds == 1, "CQ2 dispara una ronda de correccion")
        check(not rec2.issues, "CQ2 queda limpia tras la correccion")
        check(not rec2.restored,
              "tras corregir no hizo falta restaurar nada")
        repair_prompt = pipeline.llm.prompts["CQ2/repair1"]
        check("has perdido el nodo 'Ticket'" in repair_prompt,
              "el prompt de correccion le dice que perdio 'Ticket'")
        check("no_range" in repair_prompt or "no tiene rango" in repair_prompt,
              "el prompt de correccion incluye el error de convencion")

        print("\n[3] Memoria entre etapas")
        prompts = pipeline.llm.prompts
        check("el grafo esta vacio" in prompts["CQ1"],
              "en CQ1 la memoria va vacia")
        check('G.add_node("ADR", type="Class")' in prompts["CQ2"],
              "en CQ2 la memoria contiene lo de CQ1")
        check('G.add_node("Alternativa", type="Class")' in prompts["CQ3"],
              "en CQ3 la memoria contiene lo corregido de CQ2")
        check("va a sustituirlo" in prompts["CQ3"],
              "la memoria avisa de que la salida sustituye a la anterior")

        print("\n[4] Ontologia final")
        s = result.graph.summary()
        check(s["classes"] == 7, "7 clases (%d)" % s["classes"])
        check(s["object_properties"] == 6,
              "6 propiedades de objeto (%d)" % s["object_properties"])
        check(s["datatype_properties"] == 2, "2 propiedades de datos")
        check(s["pivot_classes"] == 1, "1 clase pivote")
        check(s["subclass_axioms"] == 2, "2 axiomas subClassOf")
        final = (run_dir / "ontology.py").read_text(encoding="utf-8")
        check("Ticket" in final, "'Ticket' sobrevive hasta la ontologia final")
        check("Claro, aqui tienes" not in final, "la prosa del modelo no entra")

        print("\n[5] Artefactos en disco")
        for name in ("manifest.json", "ontology.py", "ontology.json",
                     "ontology.graphml", "ontology.ttl", "summary.json"):
            check((run_dir / name).exists() and (run_dir / name).stat().st_size > 0,
                  "se escribe %s" % name)
        for cq in ("CQ1", "CQ2", "CQ3"):
            base = run_dir / "cqs" / cq
            check(all((base / f).exists() for f in
                      ("response.md", "code.py", "ontology_after.py",
                       "record.json")), "artefactos de %s" % cq)

        record2 = json.loads(
            (run_dir / "cqs" / "CQ2" / "record.json").read_text(encoding="utf-8"))
        check(record2["added"]["nodes"] == ["Alternativa", "Contexto",
                                            "planteaContexto", "selectsAlternative"],
              "el record de CQ2 lista lo que anadio: %s" % record2["added"]["nodes"])
        check(record2["lost"]["nodes"] == [],
              "el record de CQ2 no reporta perdidas tras la correccion")

        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        check(len(manifest["prompt_sha256"]) == 5,
              "el manifiesto guarda el hash de los 5 prompts")
        check(manifest["config"]["llm_does_merge"] is True,
              "el manifiesto registra que el LLM reemite la ontologia entera")

        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        check(summary["usage"]["output_tokens"] == 200,
              "se acumulan los tokens de las 4 llamadas")

        print("\n[6] Red de seguridad cuando la correccion no basta")
        SEEN.clear()
        CANNED["CQ2/repair1"] = block(CQ2_MALA)   # el modelo insiste en el error
        config2 = Config(output_dir=str(tmp_path / "out2"), run_id="run_test2",
                         repair_attempts=1)
        pipeline2 = CQbyCQPipeline(config2, story, verbose=False)
        CANNED["CQ3"] = block(CQ2_MALA)
        CANNED["CQ3/repair1"] = block(CQ2_MALA)
        result2 = pipeline2.run()
        rec2b = result2.memory.records[1]
        check(rec2b.restored, "el pipeline restaura lo perdido tras agotar intentos")
        check("Ticket" in result2.graph.G,
              "'Ticket' se recupera pese a que el modelo lo borro")
        check(bool(rec2b.lost["nodes"]),
              "la perdida queda registrada y no es silenciosa: %s"
              % rec2b.lost["nodes"])

        print("\n[7] Hoja de evaluacion humana")
        eval_dir = run_dir / "eval"
        eval_dir.mkdir(exist_ok=True)
        worksheet = eval_dir / "cq_verification.csv"
        worksheet.write_text(
            "cq_id;cq;llm_answerable;llm_minor_issue;llm_missing_terms;"
            "llm_justification;humano_answerable(0/1);humano_minor_issue(0/1);"
            "humano_nota\n"
            "CQ1;a;1;0;;;1;0;\n"
            "CQ2;b;1;0;;;0;1;falta una propiedad\n"
            "CQ3;c;0;0;;;0;0;\n",
            encoding="utf-8-sig")
        scores = score_from_worksheet(worksheet)
        check(abs(scores["score_strict"] - 1 / 3) < 1e-4,
              "score estricto 0.333 desde la hoja humana")
        check(abs(scores["score_lenient"] - 2 / 3) < 1e-4,
              "score sin menores 0.667 desde la hoja humana")
        check(abs(scores["acuerdo_llm_humano"] - 2 / 3) < 1e-4,
              "acuerdo LLM/humano 0.667")

    print("\n" + "=" * 60)
    if FAILURES:
        print("FALLOS (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("El pipeline completo funciona con LLM simulado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
