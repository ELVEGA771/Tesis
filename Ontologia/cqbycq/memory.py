"""Modulo de memoria externa.

El paper: "LLMs require a memory to handle a context larger than their allowed
context size. An external memory module can store information, such as
previously generated code and past communications."

Para CQbyCQ, el prompt original cierra con "Here is the last RDF: {RDF}", y
dice que la salida de cada etapa es una ontologia independiente que sustituye
a la anterior. Aqui la memoria guarda ese grafo acumulado y lo devuelve en el
formato que el modelo tiene que producir.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from .ontograph import Issue, OntoGraph


@dataclass
class CQRecord:
    """Lo que produjo una iteracion (una CQ)."""
    cq_id: str
    cq_text: str
    raw_response: str = ""
    code: str = ""
    applied: int = 0
    issues: list[Issue] = field(default_factory=list)
    repair_rounds: int = 0
    usage: dict = field(default_factory=dict)
    delta: dict = field(default_factory=dict)      # cuanto crecio la ontologia
    added: dict = field(default_factory=dict)      # que nodos/aristas se anadieron
    lost: dict = field(default_factory=dict)       # que se perdio y hubo que restaurar
    restored: bool = False

    def to_dict(self) -> dict:
        return {
            "cq_id": self.cq_id,
            "cq_text": self.cq_text,
            "code": self.code,
            "applied_calls": self.applied,
            "repair_rounds": self.repair_rounds,
            "issues": [i.render() for i in self.issues],
            "usage": self.usage,
            "delta": self.delta,
            "added": self.added,
            "lost": self.lost,
            "restored_by_pipeline": self.restored,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CQRecord":
        """Reconstruye un registro guardado, para reanudar una corrida.

        `issues` se guardo ya renderizado ("(kind) mensaje"), asi que se
        deshace ese formato para no re-envolverlo al escribirlo de nuevo."""
        return cls(
            cq_id=data["cq_id"],
            cq_text=data.get("cq_text", ""),
            code=data.get("code", ""),
            applied=data.get("applied_calls", 0),
            issues=[_parse_issue(text) for text in data.get("issues") or []],
            repair_rounds=data.get("repair_rounds", 0),
            usage=data.get("usage") or {},
            delta=data.get("delta") or {},
            added=data.get("added") or {},
            lost=data.get("lost") or {},
            restored=data.get("restored_by_pipeline", False),
        )


def _parse_issue(text: str) -> Issue:
    """Inversa de `Issue.render()`: "(kind) mensaje -> donde"."""
    kind, _, rest = text.partition(") ")
    if not text.startswith("(") or not rest:
        return Issue("unknown", text)
    message, sep, where = rest.rpartition(" -> ")
    if not sep:
        message, where = rest, ""
    return Issue(kind[1:], message, where)


class ExternalMemory:
    """Grafo acumulado + historial por CQ."""

    EMPTY = ("import networkx as nx\n\nG = nx.MultiDiGraph()\n\n"
             "# (el grafo esta vacio: esta es la primera etapa)\n")

    def __init__(self, mode: str = "canonical") -> None:
        if mode not in {"canonical", "raw"}:
            raise ValueError("memory_mode debe ser 'canonical' o 'raw'.")
        self.mode = mode
        self.graph = OntoGraph(nx.MultiDiGraph())
        self.records: list[CQRecord] = []

    # ------------------------------------------------------------------ #

    def commit(self, new_graph: OntoGraph, record: CQRecord,
               restore_lost: bool = True) -> CQRecord:
        """Fija el resultado de una etapa como nuevo estado de la ontologia.

        `new_graph` es la ontologia COMPLETA que devolvio el modelo, no un
        delta: sustituye a la anterior. Antes de aceptarla se compara con la
        version previa para detectar regresiones (nodos o aristas que el modelo
        haya perdido por el camino), que es justo lo que el footer del prompt
        le pide no hacer."""
        previous = self.graph
        diff = new_graph.diff(previous)
        record.added = {"nodes": diff["added_nodes"], "edges": diff["added_edges"]}
        record.lost = {"nodes": diff["removed_nodes"], "edges": diff["removed_edges"]}

        if (diff["removed_nodes"] or diff["removed_edges"]) and restore_lost:
            new_graph.restore_from(previous)
            record.restored = True

        before = previous.summary()
        after = new_graph.summary()
        record.delta = {k: after[k] - before[k] for k in after if after[k] != before[k]}

        self.graph = new_graph
        self.records.append(record)
        return record

    def query(self) -> str:
        """Recupera de memoria el grafo de la etapa anterior.

        canonical: se re-serializa el grafo acumulado -> orden estable, sin
                   duplicados y sin la prosa que el modelo haya podido colar.
        raw:       se pasa el script tal cual lo emitio el modelo -> mas fiel a
                   la implementacion original, pero arrastra su ruido.
        """
        if not self.records:
            return self.EMPTY
        if self.mode == "canonical":
            return self.graph.to_script()
        return self.records[-1].code.strip() or self.EMPTY

    @property
    def modelled_cqs(self) -> list[str]:
        return [r.cq_id for r in self.records]
