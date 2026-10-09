"""Pipeline CQbyCQ.

Replica el "Sub-task Decomposed Prompting - Competency Question by Competency
Question" de Saeedizade & Blomqvist (ESWC 2024), con Python/NetworkX como
lenguaje destino en lugar de Turtle/OWL.

Bucle por cada CQ_i:

    memoria ──► prompt(header, helper, historia, CQ_i, footer, grafo anterior)
                     │
                     ▼
                    LLM ──► script NetworkX completo
                                 │
                                 ▼
                    validacion nivel 1: sintaxis + lista blanca
                    validacion nivel 2: convencion de la ontologia
                    validacion nivel 3: regresion contra la etapa anterior
                                 │ (falla)
                                 ├──► prompt de correccion ──┐
                                 │ (ok)                      │
                                 ▼                           │
                    nueva version de la ontologia ◄──────────┘
                                 │
                                 ▼
                          memoria actualizada

Como el prompt pide una salida independiente y completa, la ontologia de cada
etapa SUSTITUYE a la anterior. La comprobacion de regresion existe porque el
apendice del paper documenta el fallo tipico: los modelos olvidan clases y
propiedades de las etapas previas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from datetime import datetime, timezone

from .config import Config
from .dsl import extract_code, load_graph, validate_and_execute
from .llm import LLMClient, LLMError
from .memory import CQRecord, ExternalMemory
from .ontograph import Issue, OntoGraph
from .prompts import PromptBuilder
from .story import CompetencyQuestion, Story


@dataclass
class RunResult:
    story: Story
    config: Config
    memory: ExternalMemory
    run_dir: Path
    usage: dict

    @property
    def graph(self) -> OntoGraph:
        return self.memory.graph


class CQbyCQPipeline:
    def __init__(self, config: Config, story: Story,
                 verbose: bool = True, resume: bool = False) -> None:
        self.config = config
        self.story = story
        self.verbose = verbose
        self.resume = resume
        self.run_dir = config.run_dir()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "cqs").mkdir(exist_ok=True)

        self.builder = PromptBuilder(config, story)
        self.llm = LLMClient(config, transcript_path=self.run_dir / "transcript.jsonl")
        self.memory = ExternalMemory(config.memory_mode)
        self.reused: list[str] = []

    # ------------------------------------------------------------------ #

    def _restore(self, units: list[CompetencyQuestion]) -> int:
        """Rehidrata la memoria desde los artefactos ya guardados.

        Devuelve cuantas etapas se dan por hechas. Se para en el primer hueco:
        una etapa solo cuenta si tiene `record.json` **y** `ontology_after.py`,
        porque el grafo acumulado se reconstruye de este ultimo. Asi una etapa
        interrumpida a medias se rehace entera en vez de heredar un estado
        incoherente."""
        done = 0
        for cq in units:
            base = self.run_dir / "cqs" / cq.id
            record_file, graph_file = base / "record.json", base / "ontology_after.py"
            if not (record_file.exists() and graph_file.exists()):
                break
            try:
                data = json.loads(record_file.read_text(encoding="utf-8"))
                graph = load_graph(graph_file)
            except Exception as exc:
                self.log("      AVISO: no puedo reutilizar %s (%s); se rehace "
                         "desde aqui" % (cq.id, exc))
                break
            self.memory.graph = graph
            self.memory.records.append(CQRecord.from_dict(data))
            # Los tokens ya gastados cuentan: si no, el `summary.json` de una
            # corrida reanudada mentiria sobre lo que costo la ontologia.
            for key, value in (data.get("usage") or {}).items():
                if isinstance(value, int):
                    self.llm.total_usage[key] = (
                        self.llm.total_usage.get(key, 0) + value)
            self.reused.append(cq.id)
            done += 1
        return done

    def _log_resume(self, done: int, total: int) -> None:
        """Deja constancia de la reanudacion sin tocar el manifiesto original.

        El `manifest.json` guarda los hashes de los prompts de la corrida
        original; sobrescribirlo destruiria justo el dato que sirve para
        distinguir "cambie el prompt" de "el modelo no fue determinista"."""
        entry = {
            "resumed_utc": datetime.now(timezone.utc).isoformat(),
            "reused_cqs": list(self.reused),
            "pending": total - done,
            "config": self.config.manifest()["config"],
            "prompt_sha256": self.config.manifest()["prompt_sha256"],
        }
        with (self.run_dir / "resumes.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ #

    def log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    def run(self) -> RunResult:
        units = list(self.story.competency_questions)
        if self.config.model_additional_statement and self.story.additional_statement:
            units.append(CompetencyQuestion(
                id="ADD", text=self.story.additional_statement))
        total = len(units)

        if self.resume:
            done = self._restore(units)
            self._log_resume(done, total)
        else:
            done = 0
            manifest = self.config.manifest({
                "story": {"id": self.story.id, "title": self.story.title,
                          "n_cqs": len(self.story.competency_questions),
                          "has_additional_statement":
                              bool(self.story.additional_statement)},
            })
            (self.run_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False),
                encoding="utf-8")

        self.log("Historia: %s  |  %d etapas  |  modelo %s (effort=%s)"
                 % (self.story.title, total, self.config.model, self.config.effort))
        if self.resume:
            if done:
                self.log("Reanudando en %s: %d etapa(s) ya hechas (%s), "
                         "quedan %d"
                         % (self.run_dir, done, ", ".join(self.reused),
                            total - done))
                self.log("Ontologia heredada: %s"
                         % json.dumps(self.memory.graph.summary()))
            else:
                self.log("Reanudando en %s: no hay ninguna etapa completa "
                         "todavia, se empieza desde el principio" % self.run_dir)
            if done == total:
                self.log("\nNo queda nada pendiente; solo se reescriben las "
                         "salidas finales.")

        for index, cq in enumerate(units, start=1):
            if index <= done:
                continue
            self.log("\n[%d/%d] %s  %s" % (index, total, cq.id, cq.text[:88]))
            record = self._model_cq(cq, index, total)
            self.log("      nodos/aristas=%d  correcciones=%d  delta=%s"
                     % (record.applied, record.repair_rounds,
                        record.delta if record.delta else "(sin cambios)"))
            if record.restored:
                self.log("      AVISO: el modelo perdio %d nodo(s) y %d arista(s) "
                         "de la etapa anterior; el pipeline los ha restaurado"
                         % (len(record.lost["nodes"]), len(record.lost["edges"])))
            self._save_cq_artifacts(record)

        self._save_final()
        self.log("\nOntologia final: %s" % json.dumps(self.memory.graph.summary()))
        self.log("Resultados en: %s" % self.run_dir)
        return RunResult(self.story, self.config, self.memory, self.run_dir,
                         self.llm.total_usage)

    # ------------------------------------------------------------------ #

    def _model_cq(self, cq: CompetencyQuestion, index: int,
                  total: int) -> CQRecord:
        """Una etapa: prompt -> script -> validacion/correccion -> commit."""
        previous_script = self.memory.query()
        prompt = self.builder.build_cq_prompt(cq, previous_script, index, total)

        try:
            response = self.llm.complete(prompt, tag=cq.id)
        except LLMError as exc:
            self.log("      ERROR de API: %s" % exc)
            raise

        record = CQRecord(cq_id=cq.id, cq_text=cq.text,
                          raw_response=response.text, usage=dict(response.usage))

        result = self._build_graph(response.text)
        issues = self._all_issues(result)

        rounds = 0
        while issues and rounds < self.config.repair_attempts:
            rounds += 1
            self.log("      correccion %d/%d (%d problemas)"
                     % (rounds, self.config.repair_attempts, len(issues)))
            repair_prompt = self.builder.build_repair_prompt(
                cq, result.code, _render_issues(result, issues), previous_script)
            try:
                response = self.llm.complete(
                    repair_prompt, tag="%s/repair%d" % (cq.id, rounds))
            except LLMError as exc:
                self.log("      ERROR de API durante la correccion: %s" % exc)
                break
            for k, v in (response.usage or {}).items():
                if isinstance(v, int):
                    record.usage[k] = record.usage.get(k, 0) + v
            candidate = self._build_graph(response.text)
            candidate_issues = self._all_issues(candidate)
            if len(candidate_issues) <= len(issues):
                result, issues = candidate, candidate_issues
            if not issues:
                break

        if issues:
            self.log("      AVISO: quedan %d problemas sin resolver tras %d "
                     "intento(s) de correccion" % (len(issues), rounds))

        record.code = result.code
        record.applied = result.applied
        record.issues = issues
        record.repair_rounds = rounds
        record.raw_response = response.text

        self.memory.commit(result.graph, record,
                           restore_lost=self.config.restore_lost)
        return record

    def _build_graph(self, response_text: str):
        result = validate_and_execute(extract_code(response_text))
        return result

    def _all_issues(self, result) -> list[Issue]:
        """Los tres niveles de validacion, en un solo informe.

        El tercero solo aplica cuando el modelo reemite la ontologia entera:
        si devuelve solo el delta, no hay regresion que comprobar."""
        issues = list(result.issues)
        if self.config.llm_does_merge and self.memory.records:
            lost = result.graph.diff(self.memory.graph)
            for n in lost["removed_nodes"][:15]:
                issues.append(Issue(
                    "regression",
                    "has perdido el nodo '%s', que existia en la etapa anterior; "
                    "tu salida debe contener todo lo previo" % n))
            for e in lost["removed_edges"][:15]:
                issues.append(Issue(
                    "regression",
                    "has perdido la arista '%s', que existia en la etapa "
                    "anterior" % e))
        return issues

    # ------------------------------------------------------------------ #

    def _save_cq_artifacts(self, record: CQRecord) -> None:
        base = self.run_dir / "cqs" / record.cq_id
        base.mkdir(parents=True, exist_ok=True)
        (base / "response.md").write_text(record.raw_response, encoding="utf-8")
        (base / "code.py").write_text(record.code, encoding="utf-8")
        (base / "ontology_after.py").write_text(
            self.memory.graph.to_script(), encoding="utf-8")
        (base / "record.json").write_text(
            json.dumps(record.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8")

    def _save_final(self) -> None:
        g = self.memory.graph
        (self.run_dir / "ontology.py").write_text(g.to_script(), encoding="utf-8")
        (self.run_dir / "ontology.json").write_text(
            json.dumps(g.to_node_link(), indent=2, ensure_ascii=False),
            encoding="utf-8")
        g.write_graphml(str(self.run_dir / "ontology.graphml"))
        (self.run_dir / "ontology.ttl").write_text(
            g.to_turtle(self.config.namespace), encoding="utf-8")
        (self.run_dir / "summary.json").write_text(
            json.dumps({
                "story": self.story.id,
                "summary": g.summary(),
                "usage": self.llm.total_usage,
                "cqs": [r.to_dict() for r in self.memory.records],
            }, indent=2, ensure_ascii=False), encoding="utf-8")


def _render_issues(result, issues: list[Issue]) -> str:
    regressions = [i for i in issues if i.kind == "regression"]
    base = result.error_report()
    if regressions:
        base += ("\n\nRegresion respecto a la etapa anterior:\n"
                 + "\n".join("  " + i.render() for i in regressions))
    return base.strip() or "(sin detalle)"


# ---------------------------------------------------------------------- #
# Linea base zero-shot (control experimental)
# ---------------------------------------------------------------------- #

class ZeroShotPipeline(CQbyCQPipeline):
    """Una sola interaccion, sin memoria ni descomposicion.

    El paper usa zero-shot como tecnica de contraste; tenerla aqui permite
    medir cuanto aporta la descomposicion CQ a CQ sobre la misma historia, el
    mismo modelo y el mismo lenguaje destino."""

    def run(self) -> RunResult:
        (self.run_dir / "manifest.json").write_text(
            json.dumps(self.config.manifest({"technique": "zero-shot"}),
                       indent=2, ensure_ascii=False), encoding="utf-8")
        self.log("Zero-shot sobre '%s' (%d CQs en una sola interaccion)"
                 % (self.story.title, len(self.story.competency_questions)))

        cq = CompetencyQuestion("ALL", "(todas las CQs)")
        response = self.llm.complete(self.builder.build_zero_shot_prompt(),
                                     tag="zero-shot")
        record = CQRecord(cq_id="ALL", cq_text=cq.text,
                          raw_response=response.text, usage=dict(response.usage))
        result = self._build_graph(response.text)
        issues = list(result.issues)

        rounds = 0
        while issues and rounds < self.config.repair_attempts:
            rounds += 1
            repair = self.builder.build_repair_prompt(
                cq, result.code, result.error_report(), self.memory.EMPTY)
            response = self.llm.complete(repair, tag="zero-shot/repair%d" % rounds)
            candidate = self._build_graph(response.text)
            if len(candidate.issues) <= len(issues):
                result, issues = candidate, list(candidate.issues)
            if not issues:
                break

        record.code = result.code
        record.applied = result.applied
        record.issues = issues
        record.repair_rounds = rounds
        record.raw_response = response.text
        self.memory.commit(result.graph, record, restore_lost=False)

        self._save_cq_artifacts(record)
        self._save_final()
        self.log("Ontologia final: %s" % json.dumps(self.memory.graph.summary()))
        return RunResult(self.story, self.config, self.memory, self.run_dir,
                         self.llm.total_usage)
