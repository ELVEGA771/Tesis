#!/usr/bin/env python3
"""
Recolector de ADR (Architecture Decision Records) desde GitHub.

Busca archivos Markdown candidatos a ADR en repositorios públicos usando la
API REST de búsqueda de código de GitHub, descarga su contenido y detecta
relaciones "Supersedes" / "Superseded by" para ayudar a priorizar qué repos
vale la pena curar a mano para el corpus de la tesis.

Requiere un GitHub Personal Access Token (sin scopes especiales, solo
lectura pública) en la variable de entorno GITHUB_TOKEN para tener un
límite razonable de requests (5000/h en vez de 60/h, y 30 búsquedas/min
en vez de 10/min).

Uso:
    export GITHUB_TOKEN=ghp_xxxxxxxxxxxx
    python adr_harvester.py --out ./adr_corpus --max-per-query 200

Para una prueba rápida (pocos resultados, para no gastar rate limit):
    python adr_harvester.py --out ./test_run --max-per-query 5 --limit-queries 1 --max-repos 2

Salida:
    <out>/raw/<owner>__<repo>__<path-sanitizado>.md   (contenido crudo de cada candidato)
    <out>/manifest.csv                                  (metadata de todos los candidatos)
    <out>/superseded_chains.csv                         (solo los que tienen relación detectada)
"""

import argparse
import base64
import csv
import os
import re
import sys
import time
from pathlib import Path

import requests

GITHUB_API = "https://api.github.com"

# Queries por RUTA: alta confianza. Si un archivo vive en una carpeta
# literalmente llamada adr/decisions/decision-records, es casi seguro
# intencional, sin importar qué formato interno use.
PATH_QUERIES = [
    "extension:md path:adr",
    "extension:md path:decisions",
    "extension:md path:docs/adr",
    "extension:md path:docs/decisions",
    "extension:md path:architecture/decisions",
    "extension:md path:doc/adr",
    "extension:md path:decision-records",
]

# Queries DIRIGIDAS al cuello de botella: ADR que además contienen una
# relación de reemplazo. Combinan ruta confiable + palabra clave, así que
# son de alta confianza igual que PATH_QUERIES, pero apuntan directo a lo
# escaso (las cadenas) en vez de traer ADR genéricos que después hay que
# filtrar. Correr estas es mucho más eficiente que subir --max-per-query
# a ciegas sobre las queries generales.
SUPERSESSION_QUERIES = [
    'extension:md path:adr "superseded by"',
    'extension:md path:decisions "superseded by"',
    'extension:md path:adr "Supersedes"',
    'extension:md path:decisions "Supersedes"',
    'extension:md path:adr "Status: superseded"',
    'extension:md path:docs/adr "superseded by"',
]

# Queries por CONTENIDO: red de seguridad para repos que documentan ADR
# en carpetas con nombres no convencionales. Requieren DOS frases
# específicas de MADR a la vez (no una palabra suelta) para no traer
# CLAs, changelogs o docs de planificación que solo mencionan "superseded"
# en prosa normal.
CONTENT_QUERIES = [
    'extension:md "Context and Problem Statement" "Decision Outcome"',
    'extension:md "Considered Options" "Decision Outcome"',
    'extension:md "Decision Drivers" "Considered Options"',
]

# Patrones para detectar campos de reemplazo dentro del contenido.
# El formato MADR estándar mete el valor directo después de "superseded by"
# SIN dos puntos obligatorios (ej: "Status: superseded by [ADR-0005](...)")
# y a veces en la línea siguiente ("## Status\nSuperseded by ADR-031").
# También cubre variantes con tabla ("| Superseded by | ADR-0005 |") y
# campos en negrita ("**Superseded by:** ADR-NNN"). Esto es una ayuda para
# PRIORIZAR candidatos, no un extractor 100% confiable — el valor
# capturado puede venir sucio (texto de más) en formatos no estándar;
# la verificación real es manual, como ya prevé el paso de anotación
# del subconjunto de 40-50 ADR.
SUPERSEDED_BY_PATTERNS = [
    re.compile(
        r"superseded[\s_-]*by\s*[:|]?\s*\|?\s*\[?[\"'*_]*([^\]\n,)(|\"']{1,80})",
        re.IGNORECASE,
    ),
]
SUPERSEDES_PATTERNS = [
    re.compile(
        r"supersedes\s*[:|]?\s*\|?\s*\[?[\"'*_]*([^\]\n,)(|\"']{1,80})",
        re.IGNORECASE,
    ),
]

NEGATIVE_VALUES = {"n/a", "na", "none", "-", "", "null", "todo"}

# Archivos "índice" que típicamente viven en las mismas carpetas que los ADR
# reales pero no son una decisión en sí (y a veces contienen placeholders que
# hacen que la heurística looks_like_adr() les pegue un falso positivo).
EXCLUDED_BASENAME_KEYWORDS = ("readme", "template", "index", "contributing", "changelog")


def is_excluded_filename(path: str) -> bool:
    basename = path.rsplit("/", 1)[-1].lower()
    stem = basename[:-3] if basename.endswith(".md") else basename
    stem = stem.lstrip("_-")  # para casos como "_readme" o "-template"
    return any(kw in stem for kw in EXCLUDED_BASENAME_KEYWORDS)


def gh_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "adr-harvester-thesis-script",
    })
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


MAX_403_RETRIES = 2  # tope duro: nunca reintentar indefinidamente un rate limit


def rate_limit_wait(resp: requests.Response):
    """Si estamos por agotar el límite (pero la request SÍ tuvo éxito), duerme hasta que se resetee."""
    remaining = resp.headers.get("X-RateLimit-Remaining")
    reset = resp.headers.get("X-RateLimit-Reset")
    if remaining is not None and int(remaining) <= 1 and reset:
        wait_s = max(0, int(reset) - int(time.time())) + 2
        print(f"  [rate limit] esperando {wait_s}s...", file=sys.stderr)
        time.sleep(min(wait_s, 120))


def handle_403(resp: requests.Response, attempt: int) -> bool:
    """Decide si vale la pena reintentar un 403. Devuelve True si hay que reintentar,
    False si hay que rendirse (para NUNCA quedar en un loop infinito)."""
    if attempt >= MAX_403_RETRIES:
        print(f"  [error] 403 persistente tras {attempt} intentos. Abortando esta query.", file=sys.stderr)
        try:
            print(f"  [detalle] {resp.json().get('message')}", file=sys.stderr)
        except Exception:
            pass
        return False

    retry_after = resp.headers.get("Retry-After")
    reset = resp.headers.get("X-RateLimit-Reset")
    remaining = resp.headers.get("X-RateLimit-Remaining")

    if retry_after:
        wait_s = int(retry_after) + 1
    elif remaining == "0" and reset:
        wait_s = max(0, int(reset) - int(time.time())) + 2
    else:
        wait_s = 15  # abuse detection genérico sin headers claros

    if wait_s > 120:
        print(f"  [error] el rate limit se resetea en {wait_s}s, demasiado para esperar aquí. "
              f"Si no tenés GITHUB_TOKEN configurado, es probablemente la causa — "
              f"generá uno en https://github.com/settings/tokens.", file=sys.stderr)
        return False

    print(f"  [rate limit / abuse detection] esperando {wait_s}s (intento {attempt + 1}/{MAX_403_RETRIES})...", file=sys.stderr)
    time.sleep(wait_s)
    return True


def search_code(session: requests.Session, query: str, max_results: int):
    """Pagina /search/code para una query dada, hasta max_results o el tope de GitHub (1000)."""
    results = []
    page = 1
    per_page = 100
    attempt = 0
    while len(results) < max_results and len(results) < 1000:
        params = {"q": query, "per_page": per_page, "page": page}
        try:
            resp = session.get(f"{GITHUB_API}/search/code", params=params, timeout=20)
        except requests.exceptions.RequestException as e:
            print(f"  [error de red] {e}. Abortando esta query.", file=sys.stderr)
            break
        if resp.status_code == 422:
            print(f"  [warning] query inválida para la API: {query!r} -> {resp.json().get('message')}", file=sys.stderr)
            break
        if resp.status_code == 403:
            attempt += 1
            if handle_403(resp, attempt):
                continue
            break
        resp.raise_for_status()
        rate_limit_wait(resp)
        data = resp.json()
        items = data.get("items", [])
        if not items:
            break
        results.extend(items)
        if len(items) < per_page:
            break
        page += 1
        attempt = 0
        time.sleep(2)  # /search tiene límite propio de 30 req/min autenticado
    return results[:max_results]


def fetch_file_content(session: requests.Session, contents_url: str):
    """Descarga y decodifica el contenido de un archivo vía la Contents API."""
    attempt = 0
    while True:
        try:
            resp = session.get(contents_url, timeout=20)
        except requests.exceptions.RequestException:
            return None
        if resp.status_code == 403:
            attempt += 1
            if handle_403(resp, attempt):
                continue
            return None
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("encoding") != "base64":
            return None
        try:
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except Exception:
            return None


def extract_relation(content: str, patterns):
    for pattern in patterns:
        m = pattern.search(content)
        if m:
            value = m.group(1).strip().strip("*_ ").strip()
            if value.lower() not in NEGATIVE_VALUES:
                return value
    return None


def looks_like_adr(content: str) -> bool:
    """Compatibilidad: wrapper sobre madr_signal_count() con el umbral usado
    para candidatos ya confirmados por ruta (ver madr_signal_count)."""
    return madr_signal_count(content) >= 2


# Frases específicas de la estructura MADR (Context and Problem Statement,
# Decision Drivers, Considered Options, Decision Outcome, Chosen option).
# Deliberadamente NO incluye señales genéricas como "## Decision" o
# "superseded by" sueltas: esas aparecen en cualquier changelog, CLA o
# documento de planificación y generan falsos positivos (ver caso
# TanStack/charts/PLAN.md, que las disparó sin ser un ADR).
MADR_SIGNALS = [
    "context and problem statement",
    "considered options",
    "decision outcome",
    "decision drivers",
    "chosen option",
]


def madr_signal_count(content: str) -> int:
    low = content.lower()
    return sum(1 for s in MADR_SIGNALS if s in low)


def sanitize(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", s)[:150]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="./adr_corpus", help="Carpeta de salida")
    parser.add_argument("--max-per-query", type=int, default=100, help="Máximo de resultados a traer por query de búsqueda")
    parser.add_argument("--max-repos", type=int, default=None, help="Límite opcional de repos distintos a procesar (útil para pruebas rápidas)")
    parser.add_argument("--limit-queries", type=int, default=None, help="Usar solo las primeras N queries de la lista (útil para pruebas rápidas)")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ADVERTENCIA: no se encontró GITHUB_TOKEN en el entorno.", file=sys.stderr)
        print("Sin token el límite es 10 búsquedas/min y 60 requests/hora — vas a tardar mucho y probablemente te bloquee.", file=sys.stderr)
        print("Generá uno en https://github.com/settings/tokens (sin scopes, solo lectura pública) y hacé:", file=sys.stderr)
        print("    export GITHUB_TOKEN=ghp_xxxxxxxxxxxx\n", file=sys.stderr)

    session = gh_session(token)

    out_dir = Path(args.out)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    queries_super = SUPERSESSION_QUERIES[: args.limit_queries] if args.limit_queries else SUPERSESSION_QUERIES
    queries_path = PATH_QUERIES[: args.limit_queries] if args.limit_queries else PATH_QUERIES
    queries_content = CONTENT_QUERIES[: args.limit_queries] if args.limit_queries else CONTENT_QUERIES

    seen = {}  # (repo, path) -> metadata, para deduplicar entre queries
    repos_seen = set()
    excluded_count = 0

    # Primero las queries de ruta (confiables): todo lo que encuentren queda
    # marcado found_via="path". Después las de contenido, pero solo se agregan
    # si el archivo NO fue encontrado ya por ruta (evita duplicar found_via).
    for found_via, query_list in (("supersession", queries_super),
                                  ("path", queries_path),
                                  ("content", queries_content)):
        for query in query_list:
            print(f"Buscando: {query!r}")
            items = search_code(session, query, args.max_per_query)
            print(f"  -> {len(items)} resultados")
            for item in items:
                repo_full = item["repository"]["full_name"]
                path = item["path"]
                key = (repo_full, path)
                if key in seen:
                    continue
                if is_excluded_filename(path):
                    excluded_count += 1
                    continue
                if args.max_repos and len(repos_seen) >= args.max_repos and repo_full not in repos_seen:
                    continue
                repos_seen.add(repo_full)
                seen[key] = {
                    "repo": repo_full,
                    "path": path,
                    "contents_url": item["url"],
                    "html_url": item["html_url"],
                    "matched_query": query,
                    "found_via": found_via,
                }
            time.sleep(1)

    print(f"\nCandidatos únicos encontrados: {len(seen)} en {len(repos_seen)} repos distintos "
          f"({excluded_count} descartados por nombre: template/readme/index/etc.).")
    print("Descargando contenido y analizando relaciones de reemplazo...\n")

    manifest_rows = []
    chain_rows = []

    for i, (key, meta) in enumerate(seen.items(), 1):
        print(f"  [{i}/{len(seen)}] {meta['repo']}/{meta['path']}", end=" ")
        content = fetch_file_content(session, meta["contents_url"])
        if content is None:
            print("-> no se pudo descargar")
            continue

        madr_score = madr_signal_count(content)
        superseded_by = extract_relation(content, SUPERSEDED_BY_PATTERNS)
        supersedes = extract_relation(content, SUPERSEDES_PATTERNS)

        if meta["found_via"] == "content" and madr_score < 2:
            # Encontrado solo por una frase de contenido y no confirma un
            # segundo indicio de formato MADR real: descartarlo directo en
            # vez de guardarlo como candidato dudoso (este es el caso que
            # traía CLA.md, PLAN.md, LOG.md).
            print("-> descartado (match de contenido débil, no parece ADR real)")
            continue

        if meta["found_via"] == "path":
            confidence = "alta (carpeta adr/decisions)" if madr_score >= 2 else "alta (ubicación) pero formato no-MADR — revisar mapeo de secciones a mano"
        elif meta["found_via"] == "supersession":
            confidence = "alta (carpeta adr/decisions + mención de reemplazo)"
        else:
            confidence = "media (solo por contenido, formato MADR confirmado)"

        status = f"guardado | confianza: {confidence}"
        if superseded_by or supersedes:
            status += " | CADENA DETECTADA"
        print(f"-> {status}")

        filename = sanitize(f"{meta['repo']}__{meta['path']}")
        if not filename.endswith(".md"):
            filename += ".md"
        (raw_dir / filename).write_text(content, encoding="utf-8")

        row = {
            "repo": meta["repo"],
            "path": meta["path"],
            "html_url": meta["html_url"],
            "local_file": str(raw_dir / filename),
            "found_via": meta["found_via"],
            "matched_query": meta["matched_query"],
            "madr_signal_score": madr_score,
            "confidence": confidence,
            "word_count": len(content.split()),
            "supersedes": supersedes or "",
            "superseded_by": superseded_by or "",
        }
        manifest_rows.append(row)
        if superseded_by or supersedes:
            chain_rows.append(row)

        time.sleep(0.3)  # cortesía con la Contents API

    fieldnames = ["repo", "path", "html_url", "local_file", "found_via", "matched_query",
                  "madr_signal_score", "confidence", "word_count", "supersedes", "superseded_by"]

    manifest_path = out_dir / "manifest.csv"
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(manifest_rows)

    chains_path = out_dir / "superseded_chains.csv"
    with open(chains_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(chain_rows)

    from_path = sum(1 for r in manifest_rows if r["found_via"] == "path")
    from_super = sum(1 for r in manifest_rows if r["found_via"] == "supersession")
    strict_madr = sum(1 for r in manifest_rows if r["madr_signal_score"] >= 2)
    repos_super = len({r["repo"] for r in manifest_rows if r["found_via"] == "supersession"})
    print("\n=== Resumen ===")
    print(f"Archivos guardados:                            {len(manifest_rows)}")
    print(f"  por query dirigida a supersesión:            {from_super}  (en {repos_super} repos)")
    print(f"  por ruta (adr/decisions):                    {from_path}")
    print(f"  solo por contenido (verificados):            {len(manifest_rows) - from_path - from_super}")
    print(f"Con formato MADR confirmado (>=2 frases):      {strict_madr}")
    print(f"Con relación superseded/supersedes detectada:  {len(chain_rows)}")
    print(f"Repos distintos:                               {len(repos_seen)}")
    print(f"Descartados por nombre (template/readme/etc.): {excluded_count}")
    if repos_super:
        print(f"\nLos {repos_super} repos hallados por query de supersesión son los candidatos")
        print("más valiosos: pasalos primero a adr_repo_harvester.py.")
    print(f"\nManifest completo: {manifest_path}")
    print(f"Solo cadenas detectadas: {chains_path}")


if __name__ == "__main__":
    main()