"""Reconstruye corpus_tesis/adr/ desde GitHub (observacion O3).

    python descargar_adr.py [--destino DIR]

Los textos de los ADR no se publican en este repositorio: 41 de los 140 vienen de
repositorios sin licencia, y que un repo sea publico no autoriza a redistribuirlo.
Se publican los identificadores (fechas_git.csv: repo, ruta_en_repo, sha_corpus) y
este procedimiento, que descarga cada fichero en el commit en que coincide con la version anotada (sha_corpus) desde
raw.githubusercontent.com y lo guarda como adr/<owner>__<repo>/<fichero>.
"""
import argparse
import csv
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = "https://raw.githubusercontent.com/{repo}/{sha}/{ruta}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino", type=Path, default=HERE / "adr")
    args = ap.parse_args()
    filas = list(csv.DictReader(open(HERE / "fechas_git.csv", encoding="utf-8")))
    ok, fallos = 0, []
    for f in filas:
        dest = args.destino / f["repo"].replace("/", "__") / f["filename"]
        if dest.exists():
            ok += 1
            continue
        url = RAW.format(repo=f["repo"], sha=f["sha_corpus"] or f["head_sha"], ruta=urllib.request.quote(f["ruta_en_repo"]))
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                datos = r.read()
        except urllib.error.URLError as e:
            fallos.append(f"{f['repo']}/{f['ruta_en_repo']}: {e}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(datos)
        ok += 1
        time.sleep(0.1)
    print(f"{ok}/{len(filas)} ADR en {args.destino}")
    for x in fallos:
        print("  fallo:", x)


if __name__ == "__main__":
    main()
