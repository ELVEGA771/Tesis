#!/usr/bin/env python3
"""
Consolida los ADR ya descargados y selecciona un top-N para depuración manual.

Toma una o varias carpetas producidas por adr_repo_harvester.py (estructura
<out>/raw/<owner>__<repo>/*.md), deduplica, extrae fechas y relaciones entre
decisiones, resuelve qué relaciones forman pares completos dentro del mismo
repo, y selecciona N archivos con estas prioridades:

  1. ADR que participan en una relación COMPLETA (origen o destino).
     Son el insumo escaso del bloque de contradicciones: entran siempre.
  2. El resto, ordenado por antigüedad (los más viejos primero), hasta
     completar N.

Además aplica --max-per-repo para que ningún repositorio domine la muestra
(el caso poplanguage/pop: 184 ADR de un solo proyecto sesgarían el corpus).

Relaciones reconocidas: superseded by / supersedes, replaced by / replaces,
amends / amended by, obsoletes. Se registran POR SEPARADO en el CSV para que
la decisión de cuáles aceptar como "reemplazo" sea tuya y del tutor, no del
script. Nada se descarta por tipo de relación.

Uso:
    python build_corpus.py --from ./corpus_viejos ./corpus_pre2024 --target 300 --out ./corpus_final
    python build_corpus.py --from ./corpus_viejos --target 300 --max-per-repo 25 --out ./corpus_final

Salida:
    <out>/selected/<owner>__<repo>/*.md   los N seleccionados
    <out>/selection.csv                   una fila por ADR seleccionado
    <out>/relations.csv                   todas las relaciones halladas
    <out>/rejected.csv                    los no seleccionados y por qué
"""

import argparse
import csv
import hashlib
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ---------- extracción (misma lógica validada en adr_repo_harvester.py) ----------

DATE_LABELS = r"(?:date|created|updated|last[\s_-]*updated|decided|accepted|revised|modified)"
LABELED_DATE_RE = re.compile(
    rf"{DATE_LABELS}\s*[:|]\s*\|?\s*\*?\*?\s*(\d{{4}})-(\d{{2}})-(\d{{2}})", re.IGNORECASE
)
ANY_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
FILENAME_DATE_RE = re.compile(r"(\d{4})-(\d{2})(?:-(\d{2}))?")
ADR_NUMBER_RE = re.compile(r"(\d{1,4})")

# Un nombre de archivo tipo fecha (20260425-algo.md) NO tiene número de ADR:
# asignarle 2026 hace que todos los archivos del repo colisionen en el mismo
# índice y que cualquier mención a una fecha resuelva como cadena.
FILENAME_IS_DATE_RE = re.compile(r"^\d{8}[-_.]")

# El destino de una relación debe PARECER una referencia a un ADR, no un
# número cualquiera dentro de una frase: "ADR 0015", "ADR-15", "#15",
# "0015-titulo.md" o un número al inicio ("15. Titulo"). Así se evita que
# "OAuth2.0" resuelva a 2 o que "2026-04-21" resuelva a 2026.
TARGET_REF_RES = [
    re.compile(r"\badr[\s\-_#]*(\d{1,4})\b", re.IGNORECASE),   # ADR 0015 / ADR-15
    re.compile(r"^#\s*(\d{1,4})\b"),                            # #15
    re.compile(r"^(\d{1,4})\s*[-–—.:)]"),                       # 0015-titulo / 15. Titulo
    re.compile(r"^(\d{3,4})\b"),                                # 0015 suelto al inicio
    re.compile(r"^decision[\s\-_#]*(\d{1,4})\b", re.IGNORECASE), # decision-0043
]


def file_number(filename):
    """Número de ADR del NOMBRE DE ARCHIVO, o None si es un nombre-fecha."""
    if FILENAME_IS_DATE_RE.match(filename):
        return None
    m = ADR_NUMBER_RE.search(filename)
    return int(m.group(1)) if m else None


def target_number(raw_value):
    """Número del ADR destino, solo si el texto tiene forma de referencia."""
    v = (raw_value or "").strip()
    for rx in TARGET_REF_RES:
        m = rx.search(v)
        if m:
            return int(m.group(1))
    return None


def adr_number(text):
    m = ADR_NUMBER_RE.search(text or "")
    return int(m.group(1)) if m else None

NEGATIVE_VALUES = {"n/a", "na", "none", "-", "", "null", "todo", "nan", "the", "this"}

# Cada relación con su patrón.
# - "super[sc]eded": acepta el typo "superceded"/"supercedes" (real en
#   NERC-CEH/datalab, konflux-ci/architecture, defenseunicorns/pepr).
# - Entre las dos palabras se toleran corchetes: "[superseded] by [ADR-004]"
#   (formato de CivicDataLab/up-fiscal-data).
RELATION_PATTERNS = {
    "superseded_by": r"super[sc]eded[\s_\-\[\]]*by",
    "supersedes": r"\bsuper[sc]edes\b",
    "replaced_by": r"replaced[\s_\-\[\]]*by",
    "replaces": r"\breplaces\b",
    "amended_by": r"amended[\s_\-\[\]]*by",
    "amends": r"\bamends\b",
    "obsoletes": r"\bobsoletes\b",
}
RELATION_RES = {name: re.compile(pat, re.IGNORECASE) for name, pat in RELATION_PATTERNS.items()}

# Lo que sigue a la palabra clave, en la misma línea. Tres formas:
#   1. lista YAML en línea:  supersedes: ["0008", "0016", "0028"]
#   2. valor simple:         Superseded by [14. Use Traefik](0014-...md)
#   3. nada, y una lista debajo (konflux-ci/architecture 0032):
#        Supercedes:
#
#        - [ADR 08. Environment Provisioning](0008-...)
YAML_LIST_RE = re.compile(r"^[ \t]*[:|]?[ \t]*\[([^\]\n]*)\](?!\()")
VALUE_RE = re.compile(r"^[ \t:|\[\]\"'*_]*([^\]\n,)(|\"']{0,80})")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+\[?[\"'*_]*([^\]\n(|\"']{1,80})")

# Plantilla MADR sin rellenar: textos de relleno literales de la plantilla.
MADR_PLACEHOLDERS = [
    "short title of solved problem", "short title, representative of solved problem",
    "describe the context and problem statement", "[option 1]", "{option 1}",
    "[driver 1", "{decision driver 1", "proposed | rejected | accepted",
    "justification. e.g., only option", "{justification}",
    "e.g., improvement of quality attribute", "[list everyone involved",
    "yyyy-mm-dd when the decision was last updated", "0005-example.md",
]


def is_madr_template(content, minimo=3):
    low = content.lower()
    return sum(1 for s in MADR_PLACEHOLDERS if s in low) >= minimo

# Relaciones que implican que la decisión origen YA NO está vigente.
# amends/amended_by no entra aquí: una enmienda modifica pero no invalida.
INVALIDATING = {"superseded_by", "supersedes", "replaced_by", "replaces", "obsoletes"}

MADR_SIGNALS = ["context and problem statement", "considered options",
                "decision outcome", "decision drivers", "chosen option"]


def adr_date(content, filename=""):
    cands = [f"{y}-{mo}-{d}" for y, mo, d in LABELED_DATE_RE.findall(content)]
    m = FILENAME_DATE_RE.match(filename)
    if m and 1990 <= int(m.group(1)) <= 2100:
        cands.append(f"{m.group(1)}-{m.group(2)}-{m.group(3) or '01'}")
    if not cands:
        cands = [f"{y}-{mo}-{d}" for y, mo, d in ANY_ISO_DATE_RE.findall(content)
                 if 1990 <= int(y) <= 2100]
    return max(cands) if cands else None


def _limpiar(val):
    return (val or "").strip().strip("*_ ").strip()


def extract_relations(content):
    """Devuelve [(tipo, valor_crudo)] de TODAS las relaciones halladas.

    Un archivo puede declarar varias relaciones del mismo tipo (NERC-CEH 0041
    "Supercedes" 28 y 29), así que se recorren todas las ocurrencias.
    """
    out = []
    vistos = set()
    lineas = content.splitlines()
    for name, rx in RELATION_RES.items():
        for m in rx.finditer(content):
            fin_linea = content.find("\n", m.end())
            resto = content[m.end(): fin_linea if fin_linea != -1 else len(content)]
            valores = []
            yl = YAML_LIST_RE.match(resto)
            vm = VALUE_RE.match(resto)
            val = _limpiar(vm.group(1)) if vm else ""
            if yl and ("," in yl.group(1) or '"' in yl.group(1) or "'" in yl.group(1)):
                valores = [_limpiar(x.strip().strip("\"'")) for x in yl.group(1).split(",")]
                valores = [x for x in valores if x]
            elif val and val.lower() not in NEGATIVE_VALUES and len(val) >= 2:
                valores.append(val)
            elif not val and not yl:
                # nada en la misma línea: ¿viene una lista debajo?
                idx = content.count("\n", 0, m.end())
                j = idx + 1
                while j < len(lineas) and not lineas[j].strip():
                    j += 1
                while j < len(lineas):
                    li = LIST_ITEM_RE.match(lineas[j])
                    if not li:
                        break
                    valores.append(_limpiar(li.group(1)))
                    j += 1
            for v in valores:
                # misma relación en frontmatter y en el cuerpo = una sola
                tn = target_number(v)
                clave = (name, tn if tn is not None else v)
                if clave not in vistos:
                    vistos.add(clave)
                    out.append((name, v))
    return out


def madr_score(content):
    low = content.lower()
    return sum(1 for s in MADR_SIGNALS if s in low)


# ---------- carga ----------

def load_corpora(roots):
    """Devuelve {(repo, filename): {...}} deduplicado por hash de contenido."""
    registros = {}
    hashes = {}
    plantillas = []
    for root in roots:
        raw = Path(root) / "raw"
        if not raw.is_dir():
            print(f"  aviso: {raw} no existe, se omite", file=sys.stderr)
            continue
        for repo_dir in sorted(raw.iterdir()):
            if not repo_dir.is_dir():
                continue
            repo = repo_dir.name.replace("__", "/", 1)
            for f in sorted(repo_dir.glob("*.md")):
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                h = hashlib.sha1(content.encode("utf-8", "replace")).hexdigest()
                if h in hashes:
                    continue
                hashes[h] = True
                if is_madr_template(content):
                    # su "superseded by [ADR-0005](0005-example.md)" de ejemplo
                    # resolvería como cadena falsa contra un 005 real del repo
                    plantillas.append((repo, f.name))
                    continue
                registros[(repo, f.name)] = {
                    "repo": repo,
                    "filename": f.name,
                    "path": str(f),
                    "content": content,
                    "date": adr_date(content, f.name),
                    "number": file_number(f.name),
                    "madr": madr_score(content),
                    "words": len(content.split()),
                }
    if plantillas:
        print(f"  {len(plantillas)} plantillas MADR sin rellenar descartadas:")
        for repo, fn in plantillas:
            print(f"    {repo}/{fn}")
    return registros


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="roots", nargs="+", required=True,
                   help="carpetas de salida de adr_repo_harvester.py (una o varias)")
    p.add_argument("--target", type=int, default=300, help="cuántos ADR seleccionar (default 300)")
    p.add_argument("--max-per-repo", type=int, default=30,
                   help="tope de ADR por repositorio para evitar que uno domine (default 30)")
    p.add_argument("--min-words", type=int, default=60,
                   help="descartar archivos más cortos que N palabras (default 60)")
    p.add_argument("--strict-cap", action="store_true",
                   help="aplicar --max-per-repo TAMBIÉN a los ADR con relación completa. "
                        "Por defecto esos entran siempre (son el insumo escaso), lo que "
                        "puede hacer que un repo rico en cadenas domine la muestra.")
    p.add_argument("--out", default="./corpus_final")
    args = p.parse_args()

    print("Cargando corpus...")
    regs = load_corpora(args.roots)
    print(f"  {len(regs)} ADR únicos (deduplicados por contenido)\n")
    if not regs:
        print("No se encontró nada. ¿Apuntaste a la carpeta que contiene raw/?", file=sys.stderr)
        return

    # índice de números por repo, para resolver destinos
    num_index = defaultdict(lambda: defaultdict(list))
    for (repo, fn), r in regs.items():
        if r["number"] is not None:
            num_index[repo][r["number"]].append(fn)

    # relaciones
    rel_rows = []
    en_cadena = set()          # (repo, filename) que participan en relación completa
    en_cadena_invalidante = set()
    for (repo, fn), r in regs.items():
        for tipo, val in extract_relations(r["content"]):
            tn = target_number(val)
            destinos = num_index[repo].get(tn, []) if tn is not None else []
            destinos = [d for d in destinos if d != fn]   # un ADR no se reemplaza a sí mismo
            completa = bool(destinos)
            rel_rows.append({
                "repo": repo, "source_file": fn, "source_date": r["date"] or "",
                "relation": tipo, "raw_value": val,
                "target_number": tn if tn is not None else "",
                "target_file": destinos[0] if destinos else "",
                "complete": completa,
                "invalidating": tipo in INVALIDATING,
            })
            if completa:
                en_cadena.add((repo, fn))
                en_cadena.add((repo, destinos[0]))
                if tipo in INVALIDATING:
                    en_cadena_invalidante.add((repo, fn))
                    en_cadena_invalidante.add((repo, destinos[0]))

    # ---------- selección ----------
    # Prioridad 1: participan en relación completa (nunca se descartan por cuota)
    # Prioridad 2: resto por antigüedad ascendente
    def clave_fecha(item):
        r = item[1]
        return (r["date"] or "9999-99-99", r["repo"], r["filename"])

    # Los ADR en relación completa quedan exentos de --min-words: son el insumo
    # escaso y algunos extremos de cadena reales son muy breves
    # (CivicDataLab/up-fiscal-data 002-koshvani.md tiene 58 palabras).
    candidatos = [(k, v) for k, v in regs.items()
                  if v["words"] >= args.min_words or k in en_cadena]
    descartados_cortos = len(regs) - len(candidatos)

    prio = [(k, v) for k, v in candidatos if k in en_cadena]
    resto = sorted([(k, v) for k, v in candidatos if k not in en_cadena], key=clave_fecha)

    seleccion = []
    por_repo = Counter()
    rechazados = []

    # Las de prioridad 1 se ordenan por antigüedad también, para que si hay
    # tope estricto se conserven las más viejas de cada repo.
    for k, v in sorted(prio, key=clave_fecha):
        if args.strict_cap and por_repo[v["repo"]] >= args.max_per_repo:
            rechazados.append((k, v, f"tope estricto de {args.max_per_repo} por repo"))
            continue
        if args.strict_cap and len(seleccion) >= args.target:
            rechazados.append((k, v, "cupo lleno"))
            continue
        seleccion.append((k, v, "relación completa"))
        por_repo[v["repo"]] += 1

    for k, v in resto:
        if len(seleccion) >= args.target:
            rechazados.append((k, v, "cupo lleno"))
            continue
        if por_repo[v["repo"]] >= args.max_per_repo:
            rechazados.append((k, v, f"tope de {args.max_per_repo} por repo"))
            continue
        seleccion.append((k, v, "antigüedad"))
        por_repo[v["repo"]] += 1

    # ---------- salida ----------
    out = Path(args.out)
    sel_dir = out / "selected"
    if sel_dir.exists():
        shutil.rmtree(sel_dir)
    sel_dir.mkdir(parents=True)

    for (repo, fn), v, motivo in seleccion:
        d = sel_dir / repo.replace("/", "__")
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(v["path"], d / fn)

    with open(out / "selection.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["repo", "filename", "date", "number", "madr_score",
                                          "words", "in_relation", "invalidating_relation", "motivo"])
        w.writeheader()
        for (repo, fn), v, motivo in seleccion:
            w.writerow({"repo": repo, "filename": fn, "date": v["date"] or "",
                        "number": v["number"] if v["number"] is not None else "",
                        "madr_score": v["madr"], "words": v["words"],
                        "in_relation": (repo, fn) in en_cadena,
                        "invalidating_relation": (repo, fn) in en_cadena_invalidante,
                        "motivo": motivo})

    with open(out / "relations.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["repo", "source_file", "source_date", "relation",
                                          "raw_value", "target_number", "target_file",
                                          "complete", "invalidating"])
        w.writeheader()
        w.writerows(rel_rows)

    with open(out / "rejected.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["repo", "filename", "date", "motivo"])
        w.writeheader()
        for (repo, fn), v, motivo in rechazados:
            w.writerow({"repo": repo, "filename": fn, "date": v["date"] or "", "motivo": motivo})

    # ---------- resumen ----------
    tipos = Counter(r["relation"] for r in rel_rows if r["complete"])
    años = Counter((v["date"] or "sin fecha")[:4] for _, v, _ in seleccion)
    pares_inval = sum(1 for r in rel_rows if r["complete"] and r["invalidating"])

    print("=== Selección ===")
    print(f"ADR seleccionados:            {len(seleccion)} (objetivo {args.target})")
    print(f"  por relación completa:      {sum(1 for _,_,m in seleccion if m=='relación completa')}")
    print(f"  por antigüedad:             {sum(1 for _,_,m in seleccion if m=='antigüedad')}")
    print(f"Descartados por ser cortos:   {descartados_cortos}")
    print(f"No seleccionados:             {len(rechazados)}")

    print(f"\n=== Relaciones completas por tipo ===")
    if tipos:
        for t, n in tipos.most_common():
            marca = "  (invalida la decisión previa)" if t in INVALIDATING else "  (solo enmienda)"
            print(f"  {n:4d}  {t:15}{marca}")
        print(f"\n  Total que invalidan una decisión previa: {pares_inval}")
    else:
        print("  ninguna")

    print(f"\n=== Distribución temporal ===")
    for a in sorted(años):
        print(f"  {a}: {años[a]:4d}  {'#' * min(40, años[a])}")

    print(f"\n=== Top repos en la selección ===")
    for repo, n in por_repo.most_common(10):
        print(f"  {n:4d}  {repo}")

    print(f"\nArchivos: {sel_dir}")
    print(f"Para depurar a mano: {out/'selection.csv'} y {out/'relations.csv'}")


if __name__ == "__main__":
    main()