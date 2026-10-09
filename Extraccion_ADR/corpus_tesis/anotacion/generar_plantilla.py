"""Genera anotaciones.csv: la hoja de anotacion manual de los 45 ADR del subconjunto.

Precarga lo que ya se sabe para que el anotador solo copie evidencias y
decida valores:
  - una fila de estado, una de decision y una de tema por cada ADR;
  - las relaciones de reemplazo del ground truth (veredicto = valido), una
    fila por cada documento que las declara, con tipo y extremos ya puestos;
  - la cadena de reemplazo a la que pertenece cada ADR (columna `cadena`);
  - dos ADR resueltos como ejemplo (DFE-Digital 0006 y 0011).

NUNCA sobrescribe una hoja existente salvo con --forzar.
"""
import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE.parent
DESTINO = HERE / "anotaciones.csv"
COLUMNAS = ["repo", "archivo", "cadena", "elemento", "evidencia", "valor", "elegida",
            "estado_normalizado", "tipo_relacion", "adr_nuevo", "adr_afectado", "nota"]

EFR = "DFE-Digital/early-years-foundation-recovery"
EJEMPLOS = {
    (EFR, "0006-content-storage-strategy.md"): [
        dict(elemento="estado", evidencia="accepted, superseded by [ADR-0011](0011-contentful.md)",
             estado_normalizado="superseded"),
        dict(elemento="decision",
             evidencia="Chosen option: Create content as a mix of YAML and Markdown and mechanically translate to GDS compliant HTML.",
             valor="Create content as a mix of YAML and Markdown and mechanically translate to GDS compliant HTML"),
        dict(elemento="tema", valor="content storage"),
        dict(elemento="alternativa", evidencia="Integrate with a commercial CMS, e.g. Contentful", elegida="0"),
        dict(elemento="alternativa", evidencia="Adapt the EYFS Reforms CMS", elegida="0"),
        dict(elemento="alternativa",
             evidencia="Create content as a mix of YAML and Markdown and mechanically translate to GDS compliant HTML",
             elegida="1"),
        dict(elemento="alternativa", evidencia="Create GDS compliant HTML by hand", elegida="0"),
    ],
    (EFR, "0011-contentful.md"): [
        dict(elemento="estado", evidencia="accepted", estado_normalizado="accepted",
             nota="el campo Status solo dice 'accepted'"),
        dict(elemento="decision", evidencia="Contentful is the mandated CMS for DfE projects.",
             valor="Use Contentful as the CMS for content storage"),
        dict(elemento="tema", valor="content storage"),
        dict(elemento="alternativa", evidencia="Static YAML", elegida="0"),
        dict(elemento="alternativa", evidencia="Git-based CMS", elegida="0"),
        dict(elemento="alternativa", evidencia="Contentful", elegida="1"),
        dict(elemento="alternativa", evidencia="Sanity", elegida="0"),
        dict(elemento="alternativa", evidencia="Strapi", elegida="0"),
    ],
}
EVIDENCIA_REL_EJEMPLO = {
    (EFR, "0006-content-storage-strategy.md"): "superseded by [ADR-0011](0011-contentful.md)",
}


def main(forzar: bool) -> None:
    if DESTINO.exists() and not forzar:
        sys.exit("Ya existe %s. No se sobrescribe (usa --forzar si de verdad quieres "
                 "perder lo anotado)." % DESTINO)

    subset = list(csv.DictReader(open(CORPUS / "anotacion_subset.csv", encoding="utf-8")))
    en_subset = {(r["repo"], r["filename"]) for r in subset}
    gt = [r for r in csv.DictReader(open(CORPUS / "ground_truth_reemplazos.csv", encoding="utf-8"))
          if r["veredicto"] == "valido"
          and (r["repo"], r["adr_reemplazado"]) in en_subset
          and (r["repo"], r["adr_nuevo"]) in en_subset]

    relaciones = {}
    for r in gt:
        declara = r["declarado_como"]
        quien = []
        if "superseded_by" in declara:
            quien.append(r["adr_reemplazado"])
        if "supersedes" in declara or "replaces" in declara:
            quien.append(r["adr_nuevo"])
        for archivo in quien:
            relaciones.setdefault((r["repo"], archivo), []).append(dict(
                elemento="relacion", tipo_relacion="supersedes",
                adr_nuevo=r["adr_nuevo"], adr_afectado=r["adr_reemplazado"],
                evidencia=EVIDENCIA_REL_EJEMPLO.get((r["repo"], archivo), ""),
                nota="precargada del ground truth (%s): pega la evidencia" % r["cadena_id"]))

    filas = []
    for s in sorted(subset, key=lambda r: (r["repo"], r["filename"])):
        clave = (s["repo"], s["filename"])
        base = dict(repo=s["repo"], archivo=s["filename"], cadena=s["cadena_id"])
        if clave in EJEMPLOS:
            cuerpo = [dict(r, nota=r.get("nota", "") or "ejemplo resuelto: revisar")
                      for r in EJEMPLOS[clave]]
        else:
            pista = "tiene alternativas explicitas" if s["alternativas_explicitas"] == "True" else ""
            cuerpo = [dict(elemento="estado"), dict(elemento="decision", nota=pista),
                      dict(elemento="tema",
                           nota="misma etiqueta en toda la cadena %s" % s["cadena_id"] if s["cadena_id"] else "")]
        for r in cuerpo + relaciones.get(clave, []):
            if clave in EJEMPLOS and r["elemento"] == "relacion":
                r["nota"] = "ejemplo resuelto: revisar"
            filas.append({c: "" for c in COLUMNAS} | base | r)

    with open(DESTINO, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS, delimiter=";")
        w.writeheader()
        w.writerows(filas)
    print("Escrito %s: %d filas para %d ADR (%d relaciones precargadas del ground truth)"
          % (DESTINO.name, len(filas), len(subset), sum(len(v) for v in relaciones.values())))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--forzar", action="store_true")
    main(ap.parse_args().forzar)
