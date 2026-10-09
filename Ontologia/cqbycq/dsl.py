"""
Validacion y ejecucion segura del script NetworkX que devuelve el LLM.

En el metodo original el LLM emite Turtle y el framework aplica un paso de
"syntax validation and correction". Aqui el destino es Python/NetworkX, asi
que el equivalente tiene dos niveles:

  NIVEL 1 - sintaxis y lista blanca (este modulo)
      Se parsea con `ast` y solo se admiten:
        import networkx as nx
        G = nx.MultiDiGraph()        (o nx.DiGraph(), que se promociona)
        G.add_node(<literal>, **<literales>)
        G.add_edge(<literal>, <literal>, **<literales>)
      El codigo NO se ejecuta con `exec`: se interpreta llamada a llamada.

  NIVEL 2 - convencion de la ontologia (`ontograph.OntoGraph.validate`)
      Nodos sin `type`, aristas sin `relation`, propiedades sin dominio o
      rango, clases pivote mal conectadas, etc.

Los dos niveles producen la misma clase de `Issue`, y ambos alimentan el
mismo bucle de correccion.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from .ontograph import (CHARACTERISTICS, RESTRICTION_FILLERS, SCHEMA_RELATIONS,
                        SCHEMA_TYPES, XSD_TYPES, Issue, OntoGraph,
                        OntoGraphError)

_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

GRAPH_VAR = "G"
GRAPH_FACTORIES = {"MultiDiGraph", "DiGraph"}


@dataclass
class CodeIssue(Issue):
    """Issue con numero de linea (nivel 1)."""

    def __init__(self, line: int, kind: str, message: str, source: str = "") -> None:
        super().__init__(kind, message, source)
        self.line = line

    def render(self) -> str:
        src = (" -> `%s`" % self.where) if self.where else ""
        return "[linea %d] (%s) %s%s" % (self.line, self.kind, self.message, src)


@dataclass
class ExecutionResult:
    graph: OntoGraph
    applied: int = 0
    syntax_issues: list[Issue] = field(default_factory=list)
    convention_issues: list[Issue] = field(default_factory=list)
    code: str = ""

    @property
    def issues(self) -> list[Issue]:
        return self.syntax_issues + self.convention_issues

    @property
    def ok(self) -> bool:
        return not self.issues

    def error_report(self) -> str:
        blocks = []
        if self.syntax_issues:
            blocks.append("Errores de sintaxis / codigo no permitido:\n"
                          + "\n".join("  " + i.render() for i in self.syntax_issues))
        if self.convention_issues:
            blocks.append("Incumplimientos de la convencion de la ontologia:\n"
                          + "\n".join("  " + i.render()
                                      for i in self.convention_issues))
        return "\n\n".join(blocks)


def extract_code(response_text: str) -> str:
    """Saca el bloque de codigo de la respuesta del LLM.

    Si no hay fences se filtran las lineas de prosa evidente: devolver
    explicaciones en vez de codigo es uno de los errores que el propio footer
    del prompt enumera."""
    fences = _FENCE_RE.findall(response_text)
    if fences:
        return "\n".join(block.strip() for block in fences).strip()
    keep = [ln for ln in response_text.splitlines()
            if ln.strip().startswith(("G.", "G ", "import ", "from ", "#"))
            or not ln.strip()]
    return "\n".join(keep).strip()


def _literal(node: ast.AST) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        raise OntoGraphError(
            "solo se admiten valores literales (cadenas, numeros, True/False, "
            "None, listas de literales) como argumentos") from None


def validate_and_execute(code: str) -> ExecutionResult:
    """Interpreta el script y devuelve el grafo resultante.

    El grafo se construye desde cero en cada llamada: el prompt pide una salida
    independiente y completa, asi que cada etapa produce una version entera de
    la ontologia, no un delta.
    """
    graph = OntoGraph(nx.MultiDiGraph())
    result = ExecutionResult(graph=graph, code=code)

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        result.syntax_issues.append(CodeIssue(
            exc.lineno or 0, "syntax",
            "error de sintaxis de Python: %s" % exc.msg,
            (exc.text or "").strip()))
        return result

    src_lines = code.splitlines()

    def src_of(node: ast.AST) -> str:
        idx = getattr(node, "lineno", 0) - 1
        return src_lines[idx].strip() if 0 <= idx < len(src_lines) else ""

    for stmt in tree.body:
        lineno = getattr(stmt, "lineno", 0)

        # import networkx as nx  -> permitido y sin efecto
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in getattr(stmt, "names", [])]
            module = getattr(stmt, "module", None)
            if all(n == "networkx" for n in names) or module == "networkx":
                continue
            result.syntax_issues.append(CodeIssue(
                lineno, "not_allowed",
                "solo se admite `import networkx as nx`", src_of(stmt)))
            continue

        # G = nx.MultiDiGraph()  -> inicializacion del grafo
        if isinstance(stmt, ast.Assign):
            issue = _check_graph_init(stmt, lineno, src_of(stmt))
            if issue:
                result.syntax_issues.append(issue)
            continue

        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
            result.syntax_issues.append(CodeIssue(
                lineno, "not_allowed",
                "solo se admiten `G.add_node(...)`, `G.add_edge(...)`, "
                "`import networkx as nx` y `G = nx.MultiDiGraph()`; no se "
                "permiten bucles, condicionales, funciones ni otras "
                "asignaciones", src_of(stmt)))
            continue

        call = stmt.value
        func = call.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == GRAPH_VAR):
            result.syntax_issues.append(CodeIssue(
                lineno, "not_allowed",
                "la llamada debe ser sobre el grafo `%s`" % GRAPH_VAR,
                src_of(stmt)))
            continue

        method = func.attr
        if method not in {"add_node", "add_edge"}:
            result.syntax_issues.append(CodeIssue(
                lineno, "not_allowed",
                "`%s.%s` no esta permitido: usa solo add_node y add_edge"
                % (GRAPH_VAR, method), src_of(stmt)))
            continue

        try:
            args = [_literal(a) for a in call.args]
            kwargs = {}
            for kw in call.keywords:
                if kw.arg is None:
                    raise OntoGraphError("no se admite `**kwargs`")
                kwargs[kw.arg] = _literal(kw.value)
        except OntoGraphError as exc:
            result.syntax_issues.append(
                CodeIssue(lineno, "signature", str(exc), src_of(stmt)))
            continue

        issue = _apply(graph, method, args, kwargs, lineno, src_of(stmt))
        if issue:
            result.syntax_issues.append(issue)
        else:
            result.applied += 1

    if not result.syntax_issues or result.applied:
        result.convention_issues = graph.validate()
    return result


def _check_graph_init(stmt: ast.Assign, lineno: int, src: str) -> CodeIssue | None:
    targets = stmt.targets
    bad = CodeIssue(
        lineno, "not_allowed",
        "la unica asignacion permitida es `%s = nx.MultiDiGraph()`" % GRAPH_VAR,
        src)
    if len(targets) != 1 or not isinstance(targets[0], ast.Name) \
            or targets[0].id != GRAPH_VAR:
        return bad
    value = stmt.value
    if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id == "nx"
            and value.func.attr in GRAPH_FACTORIES):
        return bad
    return None


def _apply(graph: OntoGraph, method: str, args: list, kwargs: dict,
           lineno: int, src: str) -> CodeIssue | None:
    if method == "add_node":
        if len(args) != 1 or not isinstance(args[0], str):
            return CodeIssue(
                lineno, "signature",
                "add_node necesita el nombre del nodo como primer argumento, "
                "una cadena", src)
        name = args[0]
        existing = dict(graph.G.nodes[name]) if name in graph.G else {}
        existing.update({k: v for k, v in kwargs.items() if v is not None})
        graph.G.add_node(name, **existing)
        return None

    if len(args) != 2 or not all(isinstance(a, str) for a in args):
        return CodeIssue(
            lineno, "signature",
            "add_edge necesita origen y destino como dos cadenas", src)
    u, v = args
    relation = kwargs.get("relation")
    if relation is not None and not isinstance(relation, str):
        return CodeIssue(lineno, "signature",
                         "`relation` debe ser una cadena", src)
    # La clave de la arista es la relacion: repetir la misma arista es
    # idempotente en vez de crear duplicados.
    for node in (u, v):
        if node not in graph.G:
            graph.G.add_node(node)
    graph.G.add_edge(u, v, key=relation, **kwargs)
    return None


def load_graph(path: str | Path) -> OntoGraph:
    """Reconstruye una ontologia desde un `ontology.py` ya generado."""
    result = validate_and_execute(Path(path).read_text(encoding="utf-8"))
    if result.syntax_issues:
        raise OntoGraphError(
            "El fichero %s no es un script valido:\n%s"
            % (path, result.error_report()))
    return result.graph


def convention_reference() -> str:
    """Resumen de la convencion, generado desde el propio codigo para que el
    prompt y el validador no puedan desincronizarse."""
    return "\n".join([
        "TIPOS DE NODO (atributo `type`, obligatorio en todos los nodos)",
        "  Esquema:   " + ", ".join(sorted(SCHEMA_TYPES)),
        "  Instancia: el nombre de una clase ya declarada",
        "",
        "RELACIONES DE ESQUEMA (atributo `relation`, obligatorio en todas las aristas)",
        "  " + ", ".join(sorted(SCHEMA_RELATIONS)),
        "  Rellenos de restriccion: " + ", ".join(sorted(RESTRICTION_FILLERS)),
        "",
        "CARACTERISTICAS DE PROPIEDAD (atributo `characteristics`, lista)",
        "  " + ", ".join(sorted(CHARACTERISTICS)),
        "",
        "TIPOS DE DATO admitidos como rango de una DatatypeProperty",
        "  " + ", ".join(sorted(XSD_TYPES)),
    ])
