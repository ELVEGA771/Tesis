#!/usr/bin/env python3
"""
Descubre repositorios ANTIGUOS que probablemente contengan ADR.

Por qué existe: la búsqueda de CÓDIGO de GitHub no soporta filtrar por fecha
(confirmado por GitHub: su backend deduplica contenido y Git no guarda fecha
de modificación a nivel de blob). Pero la búsqueda de REPOSITORIOS sí acepta
`created:` y `pushed:`. Este script usa esa vía para armar una lista de repos
viejos ANTES de gastar resultados, en vez de traer 100 repos cualesquiera y
descartarlos después por fecha.

Limitación importante: /search/repositories mira nombre, descripción, README y
topics — NO el contenido de los archivos. Encuentra repos *dedicados* a ADR o
que los mencionan en su descripción, no cualquier proyecto con una carpeta
docs/adr enterrada. Por eso es COMPLEMENTO de adr_harvester.py, no reemplazo:
las dos redes de arrastre capturan cosas distintas.

Uso:
    $env:GITHUB_TOKEN = "..."
    python discover_old_repos.py --created-before 2023-01-01 --out repos_viejos.txt
    python adr_repo_harvester.py --repos repos_viejos.txt --out ./corpus --before 2024-01-01
"""

import argparse
import os
import sys
import time

import requests

GITHUB_API = "https://api.github.com"

# Consultas sobre nombre/descripción/README/topics. Se combinan con el
# filtro de fecha en tiempo de ejecución.
BASE_QUERIES = [
    "architecture decision records",
    "architecture decision record",
    "topic:adr",
    "topic:architecture-decision-records",
    "adr in:name",
    "decision records in:description",
    "log4brains",
    "adr-tools",
]


def gh_session(token):
    s = requests.Session()
    s.headers.update({
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "adr-old-repo-discovery",
    })
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


def search_repos(session, query, max_results):
    out = []
    page = 1
    attempts = 0
    while len(out) < max_results and len(out) < 1000:
        try:
            resp = session.get(
                f"{GITHUB_API}/search/repositories",
                params={"q": query, "per_page": 100, "page": page, "sort": "stars", "order": "desc"},
                timeout=20,
            )
        except requests.exceptions.RequestException as e:
            print(f"  error de red: {e}", file=sys.stderr)
            break
        if resp.status_code == 422:
            print(f"  query rechazada: {query!r}", file=sys.stderr)
            break
        if resp.status_code == 403:
            attempts += 1
            if attempts > 2:
                print("  403 persistente; ¿configuraste GITHUB_TOKEN?", file=sys.stderr)
                break
            reset = resp.headers.get("X-RateLimit-Reset")
            wait = max(0, int(reset) - int(time.time())) + 2 if reset else 15
            if wait > 120:
                print(f"  rate limit se resetea en {wait}s, abortando esta query", file=sys.stderr)
                break
            print(f"  rate limit: esperando {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            break
        out.extend(items)
        if len(items) < 100:
            break
        page += 1
        attempts = 0
        time.sleep(2)
    return out[:max_results]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--created-before", default="2023-01-01", metavar="YYYY-MM-DD",
                   help="solo repos creados antes de esta fecha (default 2023-01-01)")
    p.add_argument("--pushed-after", default=None, metavar="YYYY-MM-DD",
                   help="opcional: solo repos con actividad después de esta fecha "
                        "(útil para excluir proyectos abandonados)")
    p.add_argument("--max-per-query", type=int, default=100)
    p.add_argument("--out", default="repos_viejos.txt")
    args = p.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ADVERTENCIA: sin GITHUB_TOKEN el límite es 10 búsquedas/min.\n", file=sys.stderr)

    session = gh_session(token)

    date_filter = f" created:<{args.created_before}"
    if args.pushed_after:
        date_filter += f" pushed:>{args.pushed_after}"

    seen = {}
    for base in BASE_QUERIES:
        query = base + date_filter
        print(f"Buscando: {query!r}")
        items = search_repos(session, query, args.max_per_query)
        nuevos = 0
        for it in items:
            full = it["full_name"]
            if full in seen:
                continue
            seen[full] = {
                # OJO: .get(k, "") NO protege de un valor null explícito en el
                # JSON (repos vacíos devuelven "pushed_at": null), así que hay
                # que usar `or ""` para cubrir ambos casos.
                "created_at": (it.get("created_at") or "")[:10],
                "pushed_at": (it.get("pushed_at") or "")[:10],
                "stars": it.get("stargazers_count") or 0,
            }
            nuevos += 1
        print(f"  -> {len(items)} resultados ({nuevos} repos nuevos)")
        time.sleep(1)

    # Los repos sin created_at (raro, pero posible) van al final en vez de
    # romper la comparación o aparecer como "los más antiguos".
    orden = sorted(seen.items(), key=lambda kv: kv[1]["created_at"] or "9999-99-99")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(f"# repos creados antes de {args.created_before}\n")
        f.write("# formato: owner/name  (creado / ultimo push / stars)\n")
        for full, meta in orden:
            f.write(f"{full}\n")

    con_fecha = [(f, m) for f, m in orden if m["created_at"]]

    print(f"\n=== Resumen ===")
    print(f"Repos únicos encontrados: {len(seen)}")
    if con_fecha:
        print(f"Más antiguo: {con_fecha[0][0]} (creado {con_fecha[0][1]['created_at']})")
        print(f"Más reciente: {con_fecha[-1][0]} (creado {con_fecha[-1][1]['created_at']})")
        print("\nPrimeros 10 (por antigüedad):")
        for full, meta in con_fecha[:10]:
            print(f"  {meta['created_at']} | {meta['stars']:5d} ★ | {full}")
    print(f"\nLista guardada en: {args.out}")
    print("Siguiente paso:")
    print(f"  python adr_repo_harvester.py --repos {args.out} --out ./corpus_viejos --before 2024-01-01")


if __name__ == "__main__":
    main()