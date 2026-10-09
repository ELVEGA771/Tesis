"""Carga de la historia de usuario y sus Competency Questions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CompetencyQuestion:
    id: str
    text: str

    def render(self) -> str:
        return "%s: %s" % (self.id, self.text)


@dataclass
class Story:
    """Una historia de usuario con sus requisitos.

    En el dataset del paper cada historia va acompanada de 14 CQs y una
    "additional statement" (un requisito en prosa que no esta formulado como
    pregunta). El pipeline la trata como una iteracion mas al final.
    """
    id: str
    title: str
    text: str
    competency_questions: list[CompetencyQuestion] = field(default_factory=list)
    additional_statement: str = ""

    @classmethod
    def load(cls, path: str | Path) -> "Story":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))

        raw_cqs = data.get("competency_questions") or data.get("cqs") or []
        cqs: list[CompetencyQuestion] = []
        for i, item in enumerate(raw_cqs, start=1):
            if isinstance(item, str):
                cqs.append(CompetencyQuestion(id="CQ%d" % i, text=item.strip()))
            else:
                cqs.append(CompetencyQuestion(
                    id=str(item.get("id") or "CQ%d" % i),
                    text=str(item["text"]).strip()))
        if not cqs:
            raise ValueError("La historia '%s' no tiene competency questions." % path)

        text = data.get("story") or data.get("text")
        if not text:
            raise ValueError("La historia '%s' no tiene campo `story`." % path)

        return cls(
            id=str(data.get("id") or path.stem),
            title=str(data.get("title") or path.stem),
            text=text.strip(),
            competency_questions=cqs,
            additional_statement=str(data.get("additional_statement") or "").strip(),
        )

    def all_requirements(self) -> str:
        """La historia + el conjunto COMPLETO de requisitos.

        En CQbyCQ el prompt lleva siempre la historia entera y todas las CQs
        (no solo la que se esta modelando): la CQ concreta acota la tarea, pero
        el contexto global evita que el modelo cree terminos incoherentes con
        las CQs que aun no ha visto."""
        parts = [self.text, "", "Competency Questions (conjunto completo):"]
        parts += ["  %s" % cq.render() for cq in self.competency_questions]
        if self.additional_statement:
            parts += ["", "Requisito adicional:", "  " + self.additional_statement]
        return "\n".join(parts)
