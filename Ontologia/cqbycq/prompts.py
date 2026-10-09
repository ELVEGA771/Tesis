"""Ensamblado del prompt de 4 secciones descrito en el paper.

  header  -> introduce la tarea: que CQ toca, y que la salida es un script
             independiente y completo que sustituye al anterior
  helper  -> conceptos de modelado + la convencion del grafo NetworkX
             (en el metodo original, la sintaxis Turtle)
  story   -> la historia y el conjunto completo de requisitos
  footer  -> los errores recurrentes que el modelo debe evitar

En CQbyCQ se anaden dos secciones mas:
  task    -> la CQ concreta de esta iteracion
  memory  -> el grafo generado hasta ahora, que en el prompt original va al
             final ("Here is the last RDF")

El apendice del paper justifica que helper y footer NO son opcionales: sin
helper el modelo se salta las reificaciones y coloca mal las propiedades; sin
footer aparecen los fallos tipicos (salida vacia, prosa en vez de codigo,
ausencia de taxonomia). Tampoco se incluyen ejemplos del dominio (few-shot /
ODPs): el paper documenta que degradan el resultado por distraccion.
"""

from __future__ import annotations

import re

from .config import Config
from .dsl import convention_reference
from .story import CompetencyQuestion, Story

_TOKEN_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# Secciones cuyo texto es identico en todas las iteraciones de una corrida.
# Determinan donde se coloca el breakpoint de cache de prompt.
CONSTANT_SECTIONS = {"header", "helper", "story", "footer"}


def render(template: str, context: dict[str, str]) -> str:
    """Sustituye `{{token}}`. Las llaves simples quedan literales, para que los
    ejemplos de codigo dentro de los prompts no rompan el formateo."""
    missing: list[str] = []

    def sub(match: re.Match) -> str:
        key = match.group(1)
        if key not in context:
            missing.append(key)
            return match.group(0)
        return context[key]

    out = _TOKEN_RE.sub(sub, template)
    if missing:
        raise KeyError("Placeholders sin valor en el prompt: %s" % sorted(set(missing)))
    return out


class PromptBuilder:
    def __init__(self, config: Config, story: Story) -> None:
        self.config = config
        self.story = story
        self.templates = {
            "header": self._read("header_file"),
            "helper": self._read("helper_file"),
            "footer": self._read("footer_file"),
            "repair": self._read("repair_file"),
            "verify": self._read("verify_file"),
        }

    def _read(self, attr: str) -> str:
        path = self.config.prompt_path(attr)
        if not path.exists():
            raise FileNotFoundError(
                "Falta la plantilla de prompt: %s\n"
                "Los ficheros de `prompts/` son el esqueleto editable del "
                "metodo; no se generan solos." % path)
        return path.read_text(encoding="utf-8").strip()

    # ------------------------------------------------------------------ #

    def _context(self, cq: CompetencyQuestion, index: int, total: int) -> dict:
        return {
            "cq_id": cq.id,
            "cq_text": cq.text,
            "cq_index": str(index),
            "cq_total": str(total),
            "story_title": self.story.title,
            "story_text": self.story.text,
            "all_requirements": self.story.all_requirements(),
            "convention_reference": convention_reference(),
            "modelled_so_far": ", ".join(
                c.id for c in self.story.competency_questions[:index - 1]) or "ninguna",
        }

    def _task_section(self, cq: CompetencyQuestion, index: int, total: int) -> str:
        return (
            "## TAREA — PREGUNTA DE COMPETENCIA DE ESTA ETAPA\n\n"
            "Etapa %d de %d. La pregunta de competencia que debes resolver "
            "ahora, y solo esta, es:\n\n"
            "**%s: %s**\n\n"
            "Preguntas ya modeladas en etapas anteriores: %s.\n"
            "No toques las posteriores."
            % (index, total, cq.id, cq.text,
               ", ".join(c.id for c in self.story.competency_questions[:index - 1])
               or "ninguna"))

    def _memory_section(self, memory_text: str) -> str:
        if self.config.llm_does_merge:
            instruccion = (
                "Este es el grafo de la etapa anterior. Tu salida tiene que "
                "reproducirlo entero y anadirle lo que necesite la pregunta de "
                "competencia de esta etapa: va a sustituirlo, asi que lo que no "
                "copies se pierde.")
        else:
            instruccion = (
                "Este es el grafo de la etapa anterior. Devuelve solo los nodos "
                "y aristas NUEVOS que hagan falta para la pregunta de "
                "competencia de esta etapa: el pipeline los fusiona con esto "
                "automaticamente. Reutiliza las clases y propiedades que ya "
                "existan en vez de crear sinonimos.")
        return ("## GRAFO ACTUAL (memoria de la etapa anterior)\n\n%s\n\n"
                "```python\n%s\n```" % (instruccion, memory_text))

    def _sections(self, cq: CompetencyQuestion, memory_text: str,
                  index: int, total: int) -> dict[str, str]:
        ctx = self._context(cq, index, total)
        return {
            "header": render(self.templates["header"], ctx),
            "helper": render(self.templates["helper"], ctx),
            "task": self._task_section(cq, index, total),
            "story": (
                "## HISTORIA Y REQUISITOS COMPLETOS\n\n### %s\n\n%s"
                % (self.story.title, self.story.all_requirements())),
            "memory": self._memory_section(memory_text),
            "footer": render(self.templates["footer"], ctx),
        }

    def build_cq_prompt(self, cq: CompetencyQuestion, memory_text: str,
                        index: int, total: int) -> list[dict]:
        """Devuelve los content blocks del mensaje de usuario.

        Se envia como un unico mensaje, igual que en el metodo original. Se
        parte en dos bloques solo para poner un breakpoint de cache tras el
        prefijo constante (header/helper/story), que se repite identico en
        todas las iteraciones de la historia. Por eso el texto del header no
        lleva la CQ: va en la seccion `task`, que es la que varia."""
        sections = self._sections(cq, memory_text, index, total)
        order = self.config.section_order

        prefix_len = 0
        for name in order:
            if name in CONSTANT_SECTIONS:
                prefix_len += 1
            else:
                break

        prefix = "\n\n---\n\n".join(sections[n] for n in order[:prefix_len])
        suffix = "\n\n---\n\n".join(sections[n] for n in order[prefix_len:])

        blocks: list[dict] = []
        if prefix:
            blocks.append({"type": "text", "text": prefix,
                           "cache_control": {"type": "ephemeral"}})
        if suffix:
            blocks.append({"type": "text", "text": suffix})
        return blocks

    def build_repair_prompt(self, cq: CompetencyQuestion, bad_code: str,
                            errors: str, previous_graph: str) -> list[dict]:
        """Bucle de validacion/correccion.

        Equivale al "syntax validation and correction process" que el metodo
        original aplica sobre el Turtle antes de evaluar."""
        text = render(self.templates["repair"], {
            "cq_id": cq.id,
            "cq_text": cq.text,
            "code": bad_code,
            "errors": errors,
            "previous_graph": previous_graph,
            "convention_reference": convention_reference(),
        })
        return [{"type": "text", "text": text}]

    def build_verification_prompt(self, cq: CompetencyQuestion,
                                  ontology_script: str) -> list[dict]:
        """Prompt de CQ Verification (ver `evaluate.py`)."""
        text = render(self.templates["verify"], {
            "cq_id": cq.id,
            "cq_text": cq.text,
            "story_text": self.story.text,
            "ontology": ontology_script,
            "convention_reference": convention_reference(),
        })
        return [{"type": "text", "text": text}]

    def build_zero_shot_prompt(self) -> list[dict]:
        """Linea base zero-shot: header/helper/story/footer, una sola
        interaccion, sin memoria. Sirve de control frente a CQbyCQ."""
        cq = CompetencyQuestion(id="ALL", text="(todas)")
        sections = self._sections(cq, "", 1, len(self.story.competency_questions))
        text = "\n\n---\n\n".join(
            sections[n] for n in ("header", "helper", "story", "footer"))
        text += ("\n\n---\n\n## TAREA\n\nResuelve de una sola vez **todas** las "
                 "preguntas de competencia de la historia. El grafo de partida "
                 "esta vacio.")
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
