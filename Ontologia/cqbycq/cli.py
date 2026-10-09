"""Interfaz de linea de comandos.

    python -m cqbycq run      --story inputs/historia.json
    python -m cqbycq run      --story inputs/historia.json --technique zero-shot
    python -m cqbycq evaluate --run outputs/run_20260920_120000
    python -m cqbycq criteria --run outputs/run_20260920_120000
    python -m cqbycq score    --worksheet outputs/run_.../eval/cq_verification.csv
    python -m cqbycq api
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Config
from .criteria import evaluate_criteria, render_criteria
from .dsl import convention_reference, load_graph
from .evaluate import score_from_worksheet, verify_cqs
from .pipeline import CQbyCQPipeline, ZeroShotPipeline
from .story import Story


def _config_from_args(args: argparse.Namespace) -> Config:
    config = Config.load(getattr(args, "config", None))
    for attr in ("model", "effort", "max_tokens", "run_id", "memory_mode",
                 "repair_attempts", "output_dir", "llm_does_merge",
                 "thinking_budget"):
        value = getattr(args, attr, None)
        if value is not None:
            setattr(config, attr, value)
    if not config.run_id:
        config.__post_init__()
    return config


def _check_resume_story(run_dir: Path, story: Story) -> None:
    """Reanudar con otra historia mezclaria dos ontologias distintas."""
    manifest = run_dir / "manifest.json"
    if not manifest.exists():
        return
    data = json.loads(manifest.read_text(encoding="utf-8"))
    original = (data.get("story") or {}).get("id")
    if original and original != story.id:
        raise SystemExit(
            "Esa corrida es de la historia '%s' y le estas pasando '%s'. "
            "Reanudar mezclaria dos ontologias distintas." % (original, story.id))


def _story_for_run(run_dir: Path, explicit: str | None) -> Story:
    if explicit:
        return Story.load(explicit)
    manifest = run_dir / "manifest.json"
    if not manifest.exists():
        raise SystemExit("No encuentro %s; pasa --story explicitamente." % manifest)
    story_id = json.loads(manifest.read_text(encoding="utf-8"))["story"]["id"]
    for candidate in Path("inputs").glob("*.json"):
        try:
            story = Story.load(candidate)
        except Exception:
            continue
        if story.id == story_id:
            return story
    raise SystemExit(
        "No encuentro en `inputs/` la historia '%s' de esta corrida; pasa "
        "--story explicitamente." % story_id)


def cmd_run(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    story = Story.load(args.story)
    if args.technique:
        config.technique = args.technique

    resume = bool(args.resume)
    if resume:
        run_dir = Path(args.resume)
        if not run_dir.is_dir():
            raise SystemExit("No existe el directorio de corrida %s" % run_dir)
        # La corrida reanudada escribe en su propio directorio, no en uno nuevo.
        config.output_dir = str(run_dir.parent)
        config.run_id = run_dir.name
        _check_resume_story(run_dir, story)

    pipeline_cls = (ZeroShotPipeline if config.technique.lower() == "zero-shot"
                    else CQbyCQPipeline)
    if resume and pipeline_cls is ZeroShotPipeline:
        raise SystemExit(
            "--resume no aplica a zero-shot: es una sola interaccion, no hay "
            "etapas que retomar.")
    result = pipeline_cls(config, story, resume=resume).run()

    print("\n--- Criterios estructurales ---")
    syntax_clean = all(not r.issues for r in result.memory.records)
    print(render_criteria(evaluate_criteria(result.graph, syntax_clean)))
    print("\nTokens: %s" % json.dumps(result.usage))

    if args.evaluate:
        print("\n--- CQ Verification ---")
        report = verify_cqs(result.graph, story, config,
                            result.run_dir / "eval")
        print(report.render())
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    run_dir = Path(args.run)
    config = _config_from_args(args)
    story = _story_for_run(run_dir, args.story)
    graph = load_graph(run_dir / "ontology.py")
    report = verify_cqs(graph, story, config, run_dir / "eval")
    print(report.render())
    print("\nHoja de validacion humana: %s"
          % (run_dir / "eval" / "cq_verification.csv"))
    return 0


def cmd_criteria(args: argparse.Namespace) -> int:
    run_dir = Path(args.run)
    graph = load_graph(run_dir / "ontology.py")
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8")) \
        if (run_dir / "summary.json").exists() else {}
    syntax_clean = all(not cq.get("issues") for cq in summary.get("cqs", []))
    print(json.dumps(graph.summary(), indent=2))
    print(render_criteria(evaluate_criteria(graph, syntax_clean)))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    print(json.dumps(score_from_worksheet(args.worksheet), indent=2))
    return 0


def cmd_api(args: argparse.Namespace) -> int:
    print(convention_reference())
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    """Modelos Gemini disponibles para la clave actual."""
    from .llm import LLMError, available_models
    try:
        for name in available_models():
            print(name)
    except LLMError as exc:
        raise SystemExit(str(exc))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cqbycq",
        description="Generacion de ontologias CQ a CQ con LLM, sobre NetworkX.")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="JSON con la configuracion")
    common.add_argument("--model")
    common.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    common.add_argument("--max-tokens", dest="max_tokens", type=int)
    common.add_argument("--thinking-budget", dest="thinking_budget", type=int,
                        help="Tope duro de tokens de razonamiento (en Gemini "
                             "salen del mismo presupuesto que la respuesta)")
    common.add_argument("--run-id", dest="run_id")
    common.add_argument("--output-dir", dest="output_dir")

    p_run = sub.add_parser("run", parents=[common],
                           help="Genera la ontologia a partir de una historia")
    p_run.add_argument("--story", required=True, help="JSON de la historia + CQs")
    p_run.add_argument("--technique", choices=["CQbyCQ", "zero-shot"])
    p_run.add_argument("--memory-mode", dest="memory_mode",
                       choices=["canonical", "raw"])
    p_run.add_argument("--repair-attempts", dest="repair_attempts", type=int)
    p_run.add_argument("--llm-merges", dest="llm_does_merge",
                       action="store_true", default=None,
                       help="El LLM reemite la ontologia entera (por defecto)")
    p_run.add_argument("--delta-mode", dest="llm_does_merge",
                       action="store_false",
                       help="El LLM devuelve solo el delta y el pipeline fusiona")
    p_run.add_argument("--resume", metavar="RUN_DIR",
                       help="Retoma una corrida interrumpida: reutiliza las "
                            "CQs ya completadas y sigue por la primera que "
                            "falte")
    p_run.add_argument("--evaluate", action="store_true",
                       help="Lanza la CQ Verification al terminar")
    p_run.set_defaults(func=cmd_run)

    p_eval = sub.add_parser("evaluate", parents=[common],
                            help="CQ Verification sobre una corrida existente")
    p_eval.add_argument("--run", required=True)
    p_eval.add_argument("--story")
    p_eval.set_defaults(func=cmd_evaluate)

    p_crit = sub.add_parser("criteria", help="Criterios estructurales binarios")
    p_crit.add_argument("--run", required=True)
    p_crit.set_defaults(func=cmd_criteria)

    p_score = sub.add_parser(
        "score", help="Recalcula el score desde la hoja revisada a mano")
    p_score.add_argument("--worksheet", required=True)
    p_score.set_defaults(func=cmd_score)

    p_api = sub.add_parser("api", help="Imprime la convencion del grafo")
    p_api.set_defaults(func=cmd_api)

    p_models = sub.add_parser(
        "models", help="Modelos Gemini disponibles para tu clave")
    p_models.set_defaults(func=cmd_models)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from .llm import LLMError
    try:
        return args.func(args)
    except LLMError as exc:
        # Fallo de la API: el traceback no aporta nada al usuario.
        print("\nERROR: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
