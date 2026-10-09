#!/usr/bin/env python3
"""
Recolector de ADR por REPOSITORIO COMPLETO (complemento de adr_harvester.py).

Por qué existe: la búsqueda de código de GitHub devuelve archivos sueltos por
relevancia. Una cadena de reemplazo ("superseded by") solo sirve para el
corpus si AMBOS extremos están presentes — el ADR viejo y el que lo
reemplaza. Bajando el repo entero garantizamos eso y podemos VERIFICAR que
el target de cada relación existe realmente.

Entrada: una lista de repos (owner/name), uno por línea, o el manifest.csv
que produce adr_harvester.py (usa la columna 'repo', deduplicada).

Salida:
    <out>/raw/<owner>__<repo>/<archivo>.md   ADR agrupados por repo
    <out>/repo_report.csv                    ranking de repos por # cadenas COMPLETAS
    <out>/chains.csv                         cada relación, con flag de si el target existe

Uso:
    python adr_repo_harvester.py --from-manifest ./test_run/manifest.csv --out ./corpus_repos
    python adr_repo_harvester.py --repos repos.txt --out ./corpus_repos

No necesita token: usa el tarball público de codeload (1 request por repo,
fuera del rate limit de la API REST).
"""

import argparse
import csv
import io
import re
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

import requests

CODELOAD = "https://codeload.github.com/{repo}/tar.gz/refs/heads/{branch}"
BRANCHES = ("main", "master", "develop", "trunk")

# Un componente de ruta cuenta como carpeta de ADR si es "adr"/"adrs"
# (como palabra completa o separada por - _ .) o si contiene "decision".
# Se evalúa por COMPONENTE y no como substring suelto para no matchear
# palabras como "quadrant" o "padrino".
# Cubre: adr/, docs/adr/, decisions/, decision-records/,
#        architecture-decision-records/ (caso opendatahub), adr-records/
ADR_DIR_COMPONENT = re.compile(r"(^|[-_.])adrs?([-_.]|$)|decision", re.IGNORECASE)


def is_adr_dir(parent: str) -> bool:
    if parent in (".", "", "/"):
        return False
    return any(ADR_DIR_COMPONENT.search(part) for part in parent.split("/"))

EXCLUDED_BASENAME_KEYWORDS = ("readme", "template", "index", "contributing", "changelog")

# Mismos patrones validados en adr_harvester.py
SUPERSEDED_BY_RE = re.compile(
    r"superseded[\s_-]*by\s*[:|]?\s*\|?\s*\[?[\"'*_]*([^\]\n,)(|\"']{1,80})", re.IGNORECASE
)
SUPERSEDES_RE = re.compile(
    r"supersedes\s*[:|]?\s*\|?\s*\[?[\"'*_]*([^\]\n,)(|\"']{1,80})", re.IGNORECASE
)
NEGATIVE_VALUES = {"n/a", "na", "none", "-", "", "null", "todo", "nan"}

# Para resolver el target de una relación: extraemos el número de ADR
# ("ADR-0005", "0005-algo.md", "#5") y lo comparamos contra los números de
# los archivos del mismo repo.
ADR_NUMBER_RE = re.compile(r"(\d{1,4})")

# Fecha declarada dentro del ADR. MADR usa "- Date: 2022-05-19"; algunos
# repos la ponen en tabla ("| Date | 2022-05-19 |") o en el nombre del
# archivo ("2022-09-decouple-from-ietf.md"). Sirve como proxy de cuándo se
# tomó la decisión, que es más preciso que la fecha de creación del repo.
ADR_DATE_RE = re.compile(r"date\s*[:|]\s*\|?\s*\*?\*?\s*(\d{4})-(\d{2})-(\d{2})", re.IGNORECASE)
FILENAME_DATE_RE = re.compile(r"(\d{4})-(\d{2})(?:-(\d{2}))?")


def adr_date(content: str, filename: str = ""):
    """Devuelve 'YYYY-MM-DD' de la fecha declarada, o None si no la declara."""
    m = ADR_DATE_RE.search(content)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = FILENAME_DATE_RE.match(filename)
    if m and 1990 <= int(m.group(1)) <= 2100:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3) or '01'}"
    return None


def is_excluded_basename(name: str) -> bool:
    stem = name[:-3] if name.lower().endswith(".md") else name
    stem = stem.lower().lstrip("_-")
    return any(kw in stem for kw in EXCLUDED_BASENAME_KEYWORDS)


def adr_number(text: str):
    """Extrae el primer número que parezca un identificador de ADR."""
    m = ADR_NUMBER_RE.search(text or "")
    return int(m.group(1)) if m else None


def extract_relation(content: str, pattern):
    m = pattern.search(content)
    if not m:
        return None
    value = m.group(1).strip().strip("*_ ").strip()
    return None if value.lower() in NEGATIVE_VALUES else value


def download_repo_adrs(repo: str, timeout: int = 60, max_mb: int = 150):
    """Descarga el tarball del repo y devuelve {path_relativo: contenido} de los ADR.

    max_mb evita gastar minutos bajando repos enormes (un monorepo puede pesar
    cientos de MB para 10 archivos markdown). Se consulta Content-Length antes
    de descargar el cuerpo; si el servidor no lo informa, se descarga igual
    pero cortando al superar el límite.
    """
    for branch in BRANCHES:
        url = CODELOAD.format(repo=repo, branch=branch)
        try:
            resp = requests.get(url, timeout=timeout, stream=True)
        except requests.exceptions.RequestException as e:
            print(f"    error de red: {e}", file=sys.stderr)
            return None
        if resp.status_code != 200:
            resp.close()
            continue

        declared = resp.headers.get("Content-Length")
        if declared and int(declared) > max_mb * 1024 * 1024:
            print(f"    omitido: {int(declared) // (1024*1024)} MB > limite", file=sys.stderr)
            resp.close()
            return None

        buf = io.BytesIO()
        limit = max_mb * 1024 * 1024
        too_big = False
        for chunk in resp.iter_content(chunk_size=1 << 20):
            buf.write(chunk)
            if buf.tell() > limit:
                too_big = True
                break
        resp.close()
        if too_big:
            print(f"    omitido: supera {max_mb} MB durante la descarga", file=sys.stderr)
            return None

        buf.seek(0)
        files = {}
        try:
            with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                for member in tar.getmembers():
                    if not member.isfile() or not member.name.lower().endswith(".md"):
                        continue
                    # quitar el prefijo <repo>-<branch>/ que agrega el tarball
                    rel = member.name.split("/", 1)[1] if "/" in member.name else member.name
                    if not is_adr_dir(str(Path(rel).parent)):
                        continue
                    basename = Path(rel).name
                    if is_excluded_basename(basename):
                        continue
                    f = tar.extractfile(member)
                    if f is None:
                        continue
                    try:
                        files[rel] = f.read().decode("utf-8", errors="replace")
                    except Exception:
                        continue
        except tarfile.TarError as e:
            print(f"    error al descomprimir: {e}", file=sys.stderr)
            return None
        return files
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-manifest", help="manifest.csv producido por adr_harvester.py")
    src.add_argument("--repos", help="archivo de texto con un repo owner/name por línea")
    parser.add_argument("--out", default="./corpus_repos")
    parser.add_argument("--min-adrs", type=int, default=3,
                        help="ignorar repos con menos de N ADR (default 3)")
    parser.add_argument("--max-repos", type=int, default=None, help="límite para pruebas")
    parser.add_argument("--max-tarball-mb", type=int, default=150,
                        help="omitir repos cuyo tarball supere N MB (default 150)")
    parser.add_argument("--before", metavar="YYYY-MM-DD", default=None,
                        help="conservar solo ADR cuya fecha declarada sea anterior a esta "
                             "(ej. 2023-01-01 para un corte pre-IA generativa masiva)")
    parser.add_argument("--require-date", action="store_true",
                        help="con --before, descartar también los ADR que no declaran fecha "
                             "(por defecto se conservan y se marcan con fecha vacía)")
    args = parser.parse_args()

    if args.from_manifest:
        with open(args.from_manifest, encoding="utf-8") as f:
            repos = list(dict.fromkeys(row["repo"] for row in csv.DictReader(f)))
    else:
        with open(args.repos, encoding="utf-8") as f:
            repos = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]

    if args.max_repos:
        repos = repos[: args.max_repos]

    out_dir = Path(args.out)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    repo_rows = []
    chain_rows = []
    total_adrs = 0

    for i, repo in enumerate(repos, 1):
        print(f"[{i}/{len(repos)}] {repo}", end=" ", flush=True)
        files = download_repo_adrs(repo, max_mb=args.max_tarball_mb)
        if not files:
            print("-> sin ADR detectables")
            continue

        # Corte temporal por fecha declarada dentro del ADR.
        dates = {rel: adr_date(content, Path(rel).name) for rel, content in files.items()}
        if args.before:
            kept = {}
            for rel, content in files.items():
                d = dates[rel]
                if d is None:
                    if not args.require_date:
                        kept[rel] = content
                    continue
                if d < args.before:
                    kept[rel] = content
            dropped = len(files) - len(kept)
            files = kept
            if dropped:
                print(f"[-{dropped} por fecha]", end=" ")
            if not files:
                print("-> todos los ADR quedaron fuera del corte temporal")
                continue

        if len(files) < args.min_adrs:
            print(f"-> solo {len(files)} ADR (< --min-adrs), omitido")
            continue

        # Índice de números de ADR presentes en ESTE repo, para verificar targets
        num_index = defaultdict(list)
        for rel in files:
            n = adr_number(Path(rel).name)
            if n is not None:
                num_index[n].append(rel)

        repo_slug = repo.replace("/", "__")
        repo_out = raw_dir / repo_slug
        repo_out.mkdir(parents=True, exist_ok=True)

        complete = 0
        dangling = 0
        for rel, content in files.items():
            (repo_out / Path(rel).name).write_text(content, encoding="utf-8")

            for kind, pattern in (("superseded_by", SUPERSEDED_BY_RE), ("supersedes", SUPERSEDES_RE)):
                raw_val = extract_relation(content, pattern)
                if not raw_val:
                    continue
                target_n = adr_number(raw_val)
                target_files = num_index.get(target_n, []) if target_n is not None else []
                resolved = bool(target_files)
                if resolved:
                    complete += 1
                else:
                    dangling += 1
                chain_rows.append({
                    "repo": repo,
                    "source_file": rel,
                    "source_date": dates.get(rel) or "",
                    "relation": kind,
                    "raw_value": raw_val,
                    "target_number": target_n if target_n is not None else "",
                    "target_file_in_corpus": target_files[0] if target_files else "",
                    "chain_complete": resolved,
                })

        total_adrs += len(files)
        fechas = sorted(d for d in dates.values() if d and d in [dates[r] for r in files])
        repo_rows.append({
            "repo": repo,
            "adr_count": len(files),
            "chains_complete": complete,
            "chains_dangling": dangling,
            "date_min": fechas[0] if fechas else "",
            "date_max": fechas[-1] if fechas else "",
            "local_dir": str(repo_out),
        })
        print(f"-> {len(files)} ADR | {complete} cadenas completas | {dangling} sin target en el repo")

    repo_rows.sort(key=lambda r: (-r["chains_complete"], -r["adr_count"]))

    with open(out_dir / "repo_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["repo", "adr_count", "chains_complete", "chains_dangling",
                                          "date_min", "date_max", "local_dir"])
        w.writeheader()
        w.writerows(repo_rows)

    with open(out_dir / "chains.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["repo", "source_file", "source_date", "relation", "raw_value",
                                          "target_number", "target_file_in_corpus", "chain_complete"])
        w.writeheader()
        w.writerows(chain_rows)

    complete_total = sum(r["chains_complete"] for r in repo_rows)
    dangling_total = sum(r["chains_dangling"] for r in repo_rows)

    print("\n=== Resumen ===")
    print(f"Repos con ADR utilizables:        {len(repo_rows)}")
    print(f"ADR totales descargados:          {total_adrs}")
    print(f"Cadenas COMPLETAS (target existe): {complete_total}")
    print(f"Relaciones sin target en el repo:  {dangling_total}")
    print(f"\nRanking de repos: {out_dir / 'repo_report.csv'}")
    print(f"Detalle de cadenas: {out_dir / 'chains.csv'}")
    if repo_rows:
        print("\nTop repos por cadenas completas:")
        for r in repo_rows[:10]:
            print(f"  {r['chains_complete']:3d} cadenas | {r['adr_count']:3d} ADR | {r['repo']}")


if __name__ == "__main__":
    main()