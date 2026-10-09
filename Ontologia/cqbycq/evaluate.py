"""Evaluacion: CQ Verification y score de la ontologia.

El paper puntua una ontologia por la proporcion de CQs que puede responder:

    score = (1/n) * SUM_i f(O, CQ_i),   f(O, CQ_i) in {0, 1}

donde f vale 1 si se puede escribir una consulta que recupere los datos que
pide la CQ usando los terminos de la ontologia (CQ Verification). El paper
reporta dos variantes, con y sin "minor issues", donde un problema menor es
(1) un unico error de sintaxis o (2) una unica propiedad que falta y que
impedia formular la consulta por un solo patron.

Aqui la consulta no es SPARQL sino una funcion sobre el MultiDiGraph.

IMPORTANTE: en el paper la evaluacion la hacen evaluadores humanos. Este
modulo produce un borrador asistido por LLM mas una hoja de trabajo CSV para
que lo confirmes o lo corrijas a mano. Para la tesis, el numero que debes
reportar es el humano; el automatico sirve para pre-clasificar y para medir
el acuerdo entre ambos.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .llm import LLMClient, LLMError
from .ontograph import OntoGraph
from .prompts import PromptBuilder
from .story import Story

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class CQVerdict:
    cq_id: str
    cq_text: str
    answerable: bool = False
    minor_issue: bool = False
    minor_issue_kind: str | None = None
    query: str = ""
    used_terms: list[str] = field(default_factory=list)
    missing_terms: list[str] = field(default_factory=list)
    justification: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "cq_id": self.cq_id, "cq_text": self.cq_text,
            "answerable": self.answerable, "minor_issue": self.minor_issue,
            "minor_issue_kind": self.minor_issue_kind, "query": self.query,
            "used_terms": self.used_terms, "missing_terms": self.missing_terms,
            "justification": self.justification, "error": self.error,
        }


@dataclass
class EvaluationReport:
    story_id: str
    verdicts: list[CQVerdict]

    @property
    def n(self) -> int:
        return len(self.verdicts)

    @property
    def score_strict(self) -> float:
        """Sin tolerar problemas menores."""
        if not self.n:
            return 0.0
        return sum(1 for v in self.verdicts if v.answerable) / self.n

    @property
    def score_lenient(self) -> float:
        """Ignorando los problemas menores, como la segunda variante del paper."""
        if not self.n:
            return 0.0
        return sum(1 for v in self.verdicts
                   if v.answerable or v.minor_issue) / self.n

    def to_dict(self) -> dict:
        return {
            "story": self.story_id,
            "n_cqs": self.n,
            "score_strict": round(self.score_strict, 4),
            "score_lenient": round(self.score_lenient, 4),
            "verdicts": [v.to_dict() for v in self.verdicts],
            "nota": ("Borrador asistido por LLM. Confirma cada veredicto en "
                     "cq_verification.csv antes de reportarlo."),
        }

    def render(self) -> str:
        lines = ["CQ Verification (%d CQs)" % self.n]
        for v in self.verdicts:
            if v.error:
                mark = "ERR "
            elif v.answerable:
                mark = "SI  "
            elif v.minor_issue:
                mark = "MEN "
            else:
                mark = "NO  "
            lines.append("  [%s] %-6s %s" % (mark, v.cq_id, v.cq_text[:80]))
            if v.missing_terms:
                lines.append("           falta: %s" % ", ".join(v.missing_terms))
        lines.append("  score (estricto)  = %.3f" % self.score_strict)
        lines.append("  score (sin menores) = %.3f" % self.score_lenient)
        return "\n".join(lines)


def _parse_verdict(text: str) -> dict:
    match = _JSON_RE.search(text)
    if not match:
        raise ValueError("la respuesta del evaluador no contiene JSON")
    return json.loads(match.group(0))


def verify_cqs(graph: OntoGraph, story: Story, config: Config,
               out_dir: Path, verbose: bool = True) -> EvaluationReport:
    """Ejecuta la CQ Verification sobre la ontologia final."""
    out_dir.mkdir(parents=True, exist_ok=True)
    builder = PromptBuilder(config, story)
    llm = LLMClient(config, transcript_path=out_dir / "verification.jsonl")
    ontology_src = graph.to_script()

    verdicts: list[CQVerdict] = []
    for cq in story.competency_questions:
        verdict = CQVerdict(cq_id=cq.id, cq_text=cq.text)
        try:
            response = llm.complete(
                builder.build_verification_prompt(cq, ontology_src),
                tag="verify/%s" % cq.id)
            data = _parse_verdict(response.text)
            verdict.answerable = bool(data.get("answerable"))
            verdict.minor_issue = bool(data.get("minor_issue"))
            verdict.minor_issue_kind = data.get("minor_issue_kind")
            verdict.query = str(data.get("query") or "")
            verdict.used_terms = list(data.get("used_terms") or [])
            verdict.missing_terms = list(data.get("missing_terms") or [])
            verdict.justification = str(data.get("justification") or "")
        except (LLMError, ValueError, json.JSONDecodeError) as exc:
            verdict.error = "%s: %s" % (type(exc).__name__, exc)
        if verbose:
            print("  %-6s %s" % (cq.id, "OK" if verdict.answerable else
                                 ("menor" if verdict.minor_issue else "NO")),
                  flush=True)
        verdicts.append(verdict)

    report = EvaluationReport(story.id, verdicts)
    (out_dir / "cq_verification.json").write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    _write_worksheet(report, out_dir / "cq_verification.csv")
    return report


def _write_worksheet(report: EvaluationReport, path: Path) -> None:
    """Hoja de trabajo para la validacion humana.

    Las columnas `humano_*` van vacias a proposito: las rellenas tu y luego
    `score_from_worksheet` recalcula el score con tus veredictos."""
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow([
            "cq_id", "cq", "llm_answerable", "llm_minor_issue",
            "llm_missing_terms", "llm_justification",
            "humano_answerable(0/1)", "humano_minor_issue(0/1)", "humano_nota",
        ])
        for v in report.verdicts:
            writer.writerow([
                v.cq_id, v.cq_text, int(v.answerable), int(v.minor_issue),
                ", ".join(v.missing_terms), v.justification, "", "", "",
            ])


def score_from_worksheet(path: str | Path) -> dict:
    """Recalcula el score a partir de la hoja ya revisada por un humano."""
    rows = []
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            rows.append(row)
    graded = [r for r in rows if str(r.get("humano_answerable(0/1)", "")).strip() != ""]
    if not graded:
        raise ValueError(
            "La hoja %s no tiene ningun veredicto humano. Rellena la columna "
            "`humano_answerable(0/1)`." % path)

    def flag(row: dict, col: str) -> bool:
        return str(row.get(col, "")).strip() in {"1", "true", "True", "si", "SI"}

    n = len(graded)
    strict = sum(1 for r in graded if flag(r, "humano_answerable(0/1)")) / n
    lenient = sum(1 for r in graded
                  if flag(r, "humano_answerable(0/1)")
                  or flag(r, "humano_minor_issue(0/1)")) / n
    agree = sum(1 for r in graded
                if flag(r, "humano_answerable(0/1)") == (r["llm_answerable"] == "1"))
    return {
        "n_cqs_evaluadas": n,
        "n_cqs_totales": len(rows),
        "score_strict": round(strict, 4),
        "score_lenient": round(lenient, 4),
        "acuerdo_llm_humano": round(agree / n, 4),
    }
