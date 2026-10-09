"""Reanudar una corrida interrumpida, sin gastar API.

    python tests/test_resume.py

Escenario: la corrida muere por cuota a mitad de CQ2, con CQ1 ya completa. Al
relanzar con `--resume` no debe volver a pedirle CQ1 al modelo, y la ontologia
final tiene que salir identica a la de una corrida de una sola tirada.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cqbycq import llm as llm_module  # noqa: E402
from cqbycq import pipeline as pipeline_module  # noqa: E402
from cqbycq.config import Config  # noqa: E402
from cqbycq.dsl import load_graph  # noqa: E402
from cqbycq.llm import LLMError, LLMResponse  # noqa: E402
from cqbycq.story import Story  # noqa: E402

# Se reutilizan las respuestas prefabricadas del otro test.
from test_pipeline_mock import CANNED, STORY  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    print(("  OK   " if condition else "  FALLO") + "  " + label)
    if not condition:
        FAILURES.append(label)


class ScriptedLLM:
    """LLM simulado que puede quedarse sin cuota en una etapa concreta."""

    def __init__(self, config, transcript_path=None, server_side_fallbacks=True):
        self.config = config
        self.transcript_path = transcript_path
        self.total_usage: dict[str, int] = {}
        self.seen: list[str] = []

    fail_on: str | None = None

    def complete(self, content, system=None, tag=""):
        self.seen.append(tag)
        SEEN_GLOBAL.append(tag)
        if ScriptedLLM.fail_on and tag.startswith(ScriptedLLM.fail_on):
            raise LLMError("Cuota agotada (simulado)")
        if tag not in CANNED:
            raise AssertionError("tag inesperado: %r" % tag)
        usage = {"input_tokens": 100, "output_tokens": 50}
        for k, v in usage.items():
            self.total_usage[k] = self.total_usage.get(k, 0) + v
        return LLMResponse(text=CANNED[tag], stop_reason="STOP",
                           usage=usage, model="fake-model")


SEEN_GLOBAL: list[str] = []


def run_pipeline(config, story, resume=False):
    return pipeline_module.CQbyCQPipeline(
        config, story, verbose=False, resume=resume).run()


def main() -> int:
    llm_module.LLMClient = ScriptedLLM
    pipeline_module.LLMClient = ScriptedLLM

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        story_file = root / "historia.json"
        story_file.write_text(json.dumps(STORY), encoding="utf-8")
        story = Story.load(story_file)

        # ---- 1. Corrida de referencia, de una sola tirada ---------------
        print("\n[1] Corrida completa de referencia")
        ScriptedLLM.fail_on = None
        SEEN_GLOBAL.clear()
        ref_cfg = Config(output_dir=str(root / "out"), run_id="ref")
        ref = run_pipeline(ref_cfg, story)
        reference = ref.graph.to_script()
        check(SEEN_GLOBAL == ["CQ1", "CQ2", "CQ2/repair1", "CQ3"],
              "pide las 3 CQs + 1 correccion: %s" % SEEN_GLOBAL)
        check(ref.graph.summary()["classes"] >= 6, "la ontologia de referencia sale")

        # ---- 2. Corrida que muere por cuota en CQ2 ----------------------
        print("\n[2] La corrida se corta por cuota a mitad de CQ2")
        ScriptedLLM.fail_on = "CQ2"
        SEEN_GLOBAL.clear()
        cfg = Config(output_dir=str(root / "out"), run_id="partida")
        try:
            run_pipeline(cfg, story)
            check(False, "deberia haber fallado")
        except LLMError:
            check(True, "la corrida muere, como en la realidad")
        run_dir = root / "out" / "partida"
        check((run_dir / "cqs" / "CQ1" / "record.json").exists(),
              "CQ1 quedo guardada en disco")
        check(not (run_dir / "cqs" / "CQ2").exists(), "CQ2 no dejo artefactos")
        check(not (run_dir / "ontology.py").exists(),
              "no hay ontologia final: la corrida no termino")

        # ---- 3. Reanudar -------------------------------------------------
        print("\n[3] Se reanuda cuando vuelve la cuota")
        ScriptedLLM.fail_on = None
        SEEN_GLOBAL.clear()
        cfg2 = Config(output_dir=str(root / "out"), run_id="partida")
        resumed = run_pipeline(cfg2, story, resume=True)
        check("CQ1" not in SEEN_GLOBAL,
              "NO vuelve a pedir CQ1 al modelo: %s" % SEEN_GLOBAL)
        check(SEEN_GLOBAL == ["CQ2", "CQ2/repair1", "CQ3"],
              "sigue justo por donde iba: %s" % SEEN_GLOBAL)
        check(resumed.graph.to_script() == reference,
              "la ontologia final es identica a la de una sola tirada")

        # ---- 4. El resultado en disco esta completo ----------------------
        print("\n[4] Artefactos de la corrida reanudada")
        for name in ("ontology.py", "ontology.json", "ontology.graphml",
                     "ontology.ttl", "summary.json"):
            check((run_dir / name).exists(), "se escribe %s" % name)
        check(load_graph(run_dir / "ontology.py").to_script() == reference,
              "ontology.py releido coincide con la referencia")

        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        check([c["cq_id"] for c in summary["cqs"]] == ["CQ1", "CQ2", "CQ3"],
              "el summary lleva las 3 etapas, no solo las reanudadas")
        check(summary["usage"]["output_tokens"] == 200,
              "los tokens de CQ1 se suman a los nuevos: %s"
              % summary["usage"]["output_tokens"])

        # ---- 5. Queda traza de la reanudacion ----------------------------
        print("\n[5] Trazabilidad")
        resumes = (run_dir / "resumes.jsonl").read_text(encoding="utf-8").strip()
        entry = json.loads(resumes.splitlines()[0])
        check(entry["reused_cqs"] == ["CQ1"], "registra que CQ1 se reutilizo")
        check(entry["pending"] == 2, "y que quedaban 2 etapas")
        check("prompt_sha256" in entry, "guarda el hash de los prompts usados")
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        check(manifest["config"]["run_id"] == "partida",
              "el manifiesto original NO se sobrescribe")

        # ---- 6. Reanudar algo ya terminado no gasta nada -----------------
        print("\n[6] Reanudar una corrida ya completa")
        SEEN_GLOBAL.clear()
        again = run_pipeline(
            Config(output_dir=str(root / "out"), run_id="partida"),
            story, resume=True)
        check(SEEN_GLOBAL == [], "no hace ni una llamada: %s" % SEEN_GLOBAL)
        check(again.graph.to_script() == reference, "y la ontologia se mantiene")

        # ---- 7. Una etapa a medias se rehace entera ----------------------
        print("\n[7] Etapa incompleta en disco")
        (run_dir / "cqs" / "CQ3" / "ontology_after.py").unlink()
        SEEN_GLOBAL.clear()
        redo = run_pipeline(
            Config(output_dir=str(root / "out"), run_id="partida"),
            story, resume=True)
        check(SEEN_GLOBAL == ["CQ3"],
              "rehace solo CQ3, sin heredar un estado a medias: %s" % SEEN_GLOBAL)
        check(redo.graph.to_script() == reference, "y vuelve al mismo resultado")

    print("\n" + "=" * 60)
    if FAILURES:
        print("%d comprobacion(es) fallan." % len(FAILURES))
        return 1
    print("La reanudacion funciona.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
