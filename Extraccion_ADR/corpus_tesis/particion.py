"""Particion desarrollo/prueba del corpus (retroalimentacion del 2026-10-05, punto 2).

    python particion.py              escribe particion.csv
    python particion.py --congelar   ademas guarda el SHA-256 de anotaciones.csv y
                                     gold_anotado.json en gold_congelado.json

Reserva por repositorio: todos los ADR de un repo (y por tanto sus cadenas
enteras) caen del mismo lado. Desarrollo = repos con los que se ajustan los
prompts del pipeline y las consultas; prueba = el resto, que no se mira hasta
evaluar. DFE recovery va a desarrollo porque ya se uso para ajustar el esquema
(prueba_corpus_dfe.py) y en los ejemplos resueltos de la guia. Los otros cuatro
completan ~10 ADR anotados cubriendo MADR, Nygard, con y sin alternativas.
Los 9 repos sin ADR en el subconjunto anotado van a prueba (sirven de
distractores para el RAG y no puntuan en el bloque 1).
"""
import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESARROLLO = {
    "DFE-Digital/early-years-foundation-recovery",   # C02, MADR, ya usado al ajustar
    "cloudfoundry/cloud_controller_ng",               # C12, Nygard
    "home-assistant/architecture",                    # C21, sin alternativas
    "grafana/grafana-image-renderer",                 # MADR con alternativas
    "remix-run/react-router",                         # Nygard con alternativas
}


def particion(repo):
    return "desarrollo" if repo in DESARROLLO else "prueba"


def leer():
    """{(repo, filename): 'desarrollo'|'prueba'} para los 140 ADR."""
    return {(r["repo"], r["filename"]): r["particion"]
            for r in csv.DictReader(open(HERE / "particion.csv", encoding="utf-8"))}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--congelar", action="store_true")
    args = ap.parse_args()
    manifiesto = list(csv.DictReader(open(HERE / "corpus_manifest.csv", encoding="utf-8")))
    subset = {(r["repo"], r["filename"]): r["cadena_id"]
              for r in csv.DictReader(open(HERE / "anotacion_subset.csv", encoding="utf-8"))}
    filas = [{"repo": r["repo"], "filename": r["filename"], "particion": particion(r["repo"]),
              "anotado": (r["repo"], r["filename"]) in subset, "cadena": r["cadena_id"]}
             for r in manifiesto]
    with open(HERE / "particion.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)

    for p in ("desarrollo", "prueba"):
        xs = [f for f in filas if f["particion"] == p]
        an = [f for f in xs if f["anotado"]]
        cad = sorted({subset[(f["repo"], f["filename"])] for f in an} - {""})
        print(f"{p:<10} {len(xs):>3} ADR | {len(an):>2} anotados | {len({f['repo'] for f in xs})} repos | cadenas {cad}")
    # ninguna cadena puede quedar partida
    lados = Counter()
    for f in filas:
        if f["cadena"]:
            lados[(f["cadena"], f["particion"])] += 1
    partidas = {c for c, _ in lados if sum(1 for c2, _ in lados if c2 == c) > 1}
    assert not partidas, f"cadenas partidas: {partidas}"

    if args.congelar:
        anot = HERE / "anotacion"
        reg = {"fecha": date.today().isoformat(),
               "anotaciones.csv": sha(anot / "anotaciones.csv"),
               "gold_anotado.json": sha(anot / "gold_anotado.json"),
               "particion.csv": sha(HERE / "particion.csv")}
        (HERE / "gold_congelado.json").write_text(json.dumps(reg, indent=2), encoding="utf-8")
        print("congelado:", reg)


if __name__ == "__main__":
    main()
