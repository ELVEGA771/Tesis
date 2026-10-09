#!/usr/bin/env python3
"""
Arma el corpus de la tesis (120-150 ADR) y el subconjunto de anotación manual
(40-50 ADR) a partir de la salida de build_corpus.py.

Todos los juicios manuales (repos excluidos, pares falsos o parciales) están
codificados abajo como listas explícitas, para que la selección sea
reproducible y auditable.

Uso:
    python build_corpus.py --from ./corpus_viejos ./corpus_pre2024 ./corpus_repos ./corpus_completados \
        --target 300 --max-per-repo 30 --strict-cap --out ./corpus_final
    python select_tesis_subset.py --from ./corpus_viejos ./corpus_pre2024 ./corpus_repos ./corpus_completados \
        --relations ./corpus_final/relations.csv --out ./corpus_tesis

Salida:
    <out>/adr/<owner>__<repo>/*.md       los ADR del corpus
    <out>/corpus_manifest.csv            una fila por ADR (incluye flag de anotación)
    <out>/ground_truth_reemplazos.csv    TODOS los pares de reemplazo con su veredicto
    <out>/anotacion_subset.csv           el subconjunto de verificación
"""

import argparse
import csv
import re
import shutil
from collections import defaultdict
from pathlib import Path

import build_corpus as bc

# ---------- juicios manuales ----------

# Lista del autor: indicios de generación asistida por IA.
EXCLUIDOS_IA = {r.lower() for r in """
dork-labs/dorkos poplanguage/pop asdecided/core joinwell52-AI/FCoP Oblikovati/Oblikovati
h0x91b/dev-3.0 seahal/umaxica-apps-edge seahal/umaxica-apps-jit-global momhq/mom
DirectedEdges/specs YellowWhiteBlackCat/TaskForest davebream/claude-of-alexandria
""".split()}

# Repos con cadenas que NO están en la lista pero tampoco muestran historial
# orgánico (repo creado 2025-2026, decenas de ADR en semanas) o no son proyectos
# reales. No cuentan como ground truth hasta revisarlos a mano.
SOSPECHOSOS = {
    "strateg/home-lab": "repo 2025-10; 120 ADR entre feb y jul 2026, hasta 14 el mismo día",
    "vladimirgamalyan/vgiew": "repo creado 2026-07; 26 ADR en 6 semanas",
    "vladimirgamalyan/tg_reader": "repo creado 2026-06; 13 ADR en un mes",
    "synesenom/ran": "53 ADR en 88 días (may-ago 2026)",
    "VirtusLab/orca": "repo creado 2026-05; 22 ADR en 4 meses",
    "kodhama/trellis": "repo creado 2026-06; 17 ADR en 26 días",
    "asdecided/WayfinderRouter": "misma organización que asdecided/core (excluido)",
    "nishiryo23/shopify-app-templete": "repo 2026, 5 ADR",
    "quizwedge/trackloom": "repo creado 2026-03; 30 ADR",
    "KikoNet13/SpiritPlanner": "repo creado 2026-01; 8 ADR en un mes",
    "ferrumio/pydynox": "repo creado 2025-12",
    "ards-project/ard-spec": "repo creado 2026-05",
    "vicentereig/dspy.rb": "ADR 2025-2026; fechas del par invertidas",
    "RealityCtrl/ADR_Tools_Test": "repo de prueba de adr-tools, decisiones de juguete",
}

# Pares de repos orgánicos que se revisaron a mano y no son reemplazos válidos.
VEREDICTOS_PAR = {
    ("konflux-ci/architecture", "0015-integration-service-two-phase-architecture.md", "0036-trusted-artifacts.md"):
        ("descartado", "enlace con número viejo: el título citado es el ADR 0037; frontmatter dice superseded_by 0037"),
    ("konflux-ci/architecture", "0016-integration-service-promotion-logic.md", "0036-trusted-artifacts.md"):
        ("descartado", "enlace con número viejo: el título citado es el ADR 0037; frontmatter dice superseded_by 0037"),
    ("konflux-ci/architecture", "0029-component-dependencies.md", "0067-nudging-relationship-singleton-crd.md"):
        ("descartado", "'Partially supersedes' y el ADR 0067 está en estado Proposed"),
    ("konflux-ci/architecture", "0013-integration-service-api-contracts.md", "0030-tekton-results-naming-convention.md"):
        ("dudoso", "reemplazo parcial: solo los estándares de nombres ('All other ... remain')"),
    ("victorrentea/clean-architecture", "adr-004-layers-architecture.md", "adr-005-onion-architecture.md"):
        ("descartado", "repo didáctico; adr-004 tiene 13 palabras"),
}

REPOS_CADENA = {
    "NERC-CEH/datalab", "konflux-ci/architecture", "govuk-forms/forms", "defenseunicorns/pepr",
    "DFE-Digital/early-years-foundation-recovery", "DFE-Digital/early-years-foundation-reform",
    "cloudfoundry/cloud_controller_ng", "home-assistant/architecture", "remix-run/react-router",
    "alphagov/govuk-design-system-architecture", "hashicorp-services/tfm",
    "CivicDataLab/up-fiscal-data", "TryGhost/ActivityPub",
}

# Repos orgánicos sin cadenas válidas: aportan contexto y ADR con alternativas
# explícitas (MADR). Creados antes de 2024, ADR repartidos en meses/años.
REPOS_CONTEXTO = [
    "bitwarden/adr", "ministryofjustice/offender-management-architecture-decisions",
    "zarf-dev/zarf", "json-schema-org/json-schema-spec", "dxw/rails-template",
    "wbcsd/data-exchange-protocol", "mozilla/sre-adrs", "grafana/grafana-image-renderer",
    "aws/aws-parallelcluster-ui", "linode/apl-core", "OpenTermsArchive/engine", "eslint/rewrite",
]

# Cadenas para el subconjunto de anotación (se identifica cada una por un ADR
# miembro). Se priorizan cadenas técnicas, multi-salto (NERC proxy: 4 ADR) y
# muchos-a-uno (NERC Spark+Dask -> 41), y diversidad de repos y formatos.
ANOTAR_CADENAS = [
    ("NERC-CEH/datalab", "0013-custom-redbird-proxy.md"),
    ("NERC-CEH/datalab", "0041-user-driven-dask-and-spark.md"),
    ("konflux-ci/architecture", "0001-pipeline-service-phase-1.md"),
    ("govuk-forms/forms", "ADR002-use-govuk-notify.md"),
    ("defenseunicorns/pepr", "0011-semantic-release.md"),
    ("home-assistant/architecture", "0002-minimum-supported-python-version.md"),
    ("cloudfoundry/cloud_controller_ng", "0002-using-machinist-for-factories.md"),
    ("CivicDataLab/up-fiscal-data", "002-koshvani.md"),
    ("DFE-Digital/early-years-foundation-recovery", "0006-content-storage-strategy.md"),
    ("govuk-forms/forms", "ADR013-use-paper-trail-gem-for-auditing-and-record-backups.md"),
    ("konflux-ci/architecture", "0031-sprayproxy.md"),
    ("alphagov/govuk-design-system-architecture", "001-javascript-for-less-capable-browsers.md"),
]

STOP = set("a an the of for to in on and or with as by use using is be de 0 md adr".split())
# Encabezados de alternativas en MADR y en formatos libres (Nygard, govuk-forms,
# konflux): "Considered Options", "Alternatives Considered", "Option 1: ...".
ALT_HEADING_RE = re.compile(
    r"^#+\s*(considered options|options considered|pros and cons of the options"
    r"|(rejected |other )?alternatives|alternative (approaches|\w+\b)|other options|options\b"
    r"|option\s+(\d+|one|two|three|[a-d])\b)",
    re.IGNORECASE | re.MULTILINE)


def tokens(fn):
    return {t for t in re.split(r"[^a-z]+", fn.lower()) if len(t) > 2 and t not in STOP}


# ADR "meta" (decidir usar ADR / MADR / log4brains): no se usan como relleno.
META_RE = re.compile(r"record-architecture-decisions|log4brains|^0000-adr|use-madr|markdown-architectural-decision"
                     r"|^\d+-adrs?\.md$", re.IGNORECASE)


def tiene_alternativas(v):
    return v["madr"] >= 3 or bool(ALT_HEADING_RE.search(v["content"]))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="roots", nargs="+", required=True)
    p.add_argument("--relations", required=True, help="relations.csv de build_corpus.py")
    p.add_argument("--target", type=int, default=140)
    p.add_argument("--anotar", type=int, default=45)
    p.add_argument("--anotar-cadenas", type=int, default=27,
                   help="cupo del subconjunto de anotación reservado a ADR en cadena")
    p.add_argument("--max-extra-por-repo-cadena", type=int, default=3)
    p.add_argument("--max-por-repo-contexto", type=int, default=5)
    p.add_argument("--out", default="./corpus_tesis")
    args = p.parse_args()

    regs = bc.load_corpora(args.roots)
    with open(args.relations, encoding="utf-8") as f:
        rels = list(csv.DictReader(f))

    # ---------- pares de reemplazo con veredicto ----------
    pares = defaultdict(lambda: {"declarado": set(), "raw": []})
    enmiendas = set()
    for r in rels:
        if r["complete"] != "True":
            continue
        s, t = r["source_file"], r["target_file"]
        if r["invalidating"] != "True":
            enmiendas.add((r["repo"], s))
            enmiendas.add((r["repo"], t))
            continue
        viejo, nuevo = (s, t) if r["relation"] in ("superseded_by", "replaced_by") else (t, s)
        k = (r["repo"], viejo, nuevo)
        pares[k]["declarado"].add(r["relation"])
        pares[k]["raw"].append(r["raw_value"])

    filas_gt = []
    for (repo, viejo, nuevo), info in sorted(pares.items()):
        if repo.lower() in EXCLUIDOS_IA:
            ver, motivo = "excluido_lista_ia", "lista de exclusión del autor"
        elif repo in SOSPECHOSOS:
            ver, motivo = "excluido_no_organico", SOSPECHOSOS[repo]
        elif (repo, viejo, nuevo) in VEREDICTOS_PAR:
            ver, motivo = VEREDICTOS_PAR[(repo, viejo, nuevo)]
        elif repo in REPOS_CADENA:
            ver, motivo = "valido", ""
        else:
            ver, motivo = "sin_revisar", "repo no clasificado"
        filas_gt.append({
            "repo": repo, "adr_reemplazado": viejo, "adr_nuevo": nuevo,
            "fecha_reemplazado": regs.get((repo, viejo), {}).get("date") or "",
            "fecha_nuevo": regs.get((repo, nuevo), {}).get("date") or "",
            "declarado_como": "+".join(sorted(info["declarado"])),
            "bidireccional": len(info["declarado"]) > 1,
            "texto_crudo": " || ".join(info["raw"]), "veredicto": ver, "motivo": motivo,
        })

    usables = [g for g in filas_gt if g["veredicto"] in ("valido", "dudoso")]

    # cadenas = componentes conexos de los pares usables, por repo
    padre = {}

    def raiz(x):
        padre.setdefault(x, x)
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    for g in usables:
        a, b = (g["repo"], g["adr_reemplazado"]), (g["repo"], g["adr_nuevo"])
        padre[raiz(a)] = raiz(b)
    comp = defaultdict(set)
    for g in usables:
        for k in ((g["repo"], g["adr_reemplazado"]), (g["repo"], g["adr_nuevo"])):
            comp[raiz(k)].add(k)
    cadena_id = {}
    for i, (_, miembros) in enumerate(sorted(comp.items(), key=lambda kv: sorted(kv[1])), 1):
        for k in miembros:
            cadena_id[k] = f"C{i:02d}"
    for g in filas_gt:
        g["cadena_id"] = cadena_id.get((g["repo"], g["adr_reemplazado"]), "")

    # ---------- selección del corpus ----------
    seleccion = {}   # key -> motivo

    def agregar(k, motivo):
        if k in regs and k not in seleccion:
            seleccion[k] = motivo

    for k in sorted(cadena_id):
        agregar(k, "cadena de reemplazo")
    for k in sorted(enmiendas):
        if k[0] in REPOS_CADENA or k[0] in REPOS_CONTEXTO:
            agregar(k, "relación de enmienda")

    # distractores temáticos dentro de los repos con cadenas: ADR vigentes que
    # comparten vocabulario con los de la cadena (útil para "¿cuál es la
    # decisión vigente sobre X?")
    por_repo = defaultdict(list)
    for k, v in regs.items():
        por_repo[k[0]].append((k, v))
    for repo in sorted(REPOS_CADENA):
        voc = set().union(*[tokens(fn) for (rp, fn) in cadena_id if rp == repo] or [set()])
        cands = [(k, v) for k, v in por_repo[repo]
                 if k not in seleccion and 80 <= v["words"] <= 2500 and not META_RE.search(k[1])]
        cands.sort(key=lambda kv: (-len(tokens(kv[0][1]) & voc), kv[1]["date"] or "9999", kv[0][1]))
        for k, v in cands[: args.max_extra_por_repo_cadena]:
            if len(seleccion) >= args.target:
                break
            agregar(k, "contexto del mismo repo")

    # relleno round-robin con repos orgánicos de contexto
    colas = {}
    for repo in REPOS_CONTEXTO:
        c = [(k, v) for k, v in por_repo[repo] if k not in seleccion and 100 <= v["words"] <= 2000
             and not META_RE.search(k[1])]
        c.sort(key=lambda kv: (not tiene_alternativas(kv[1]), kv[1]["date"] is None, kv[1]["date"] or "", kv[0][1]))
        colas[repo] = c[: args.max_por_repo_contexto]
    while len(seleccion) < args.target and any(colas.values()):
        for repo in REPOS_CONTEXTO:
            if colas[repo] and len(seleccion) < args.target:
                k, _ = colas[repo].pop(0)
                agregar(k, "contexto orgánico")

    # ---------- subconjunto de anotación ----------
    anotar = []
    comps = defaultdict(list)
    for k in seleccion:
        if k in cadena_id:
            comps[cadena_id[k]].append(k)
    # cadenas elegidas a mano, en orden de prioridad: se anota la cadena
    # COMPLETA (todos sus ADR) para poder medir detección de reemplazo
    for repo, fn in ANOTAR_CADENAS:
        cid = cadena_id.get((repo, fn))
        if cid is None:
            print(f"  aviso: {repo}/{fn} no está en ninguna cadena usable")
            continue
        ks = [k for k in sorted(comps[cid]) if k not in anotar]
        if len(anotar) + len(ks) <= args.anotar_cadenas:
            anotar.extend(ks)
    n_cad = len(anotar)

    # resto: ADR fuera de cadena, primero los que tienen alternativas explícitas
    # (bloque de fidelidad: F1 de alternativas) y luego los que no (miden
    # alternativas inventadas). Máx. 3 por repo, en ronda para diversidad.
    def llenar(filtro):
        otros = defaultdict(list)
        for k in sorted(seleccion):
            v = regs[k]
            if (k not in cadena_id and k not in anotar and filtro(v) and 80 <= v["words"] <= 2000
                    and not META_RE.search(k[1])):
                otros[k[0]].append(k)
        hubo = True
        while len(anotar) < args.anotar and hubo:
            hubo = False
            for repo in sorted(otros):
                ya = sum(1 for a in anotar if a[0] == repo)
                if otros[repo] and ya < 3 and len(anotar) < args.anotar:
                    anotar.append(otros[repo].pop(0))
                    hubo = True

    llenar(tiene_alternativas)
    n_alt = len(anotar) - n_cad
    llenar(lambda v: not tiene_alternativas(v))
    anotar_set = set(anotar)

    # ---------- salida ----------
    out = Path(args.out)
    adr_dir = out / "adr"
    if adr_dir.exists():
        shutil.rmtree(adr_dir)
    adr_dir.mkdir(parents=True)
    manifest = []
    for k in sorted(seleccion):
        v = regs[k]
        d = adr_dir / k[0].replace("/", "__")
        d.mkdir(exist_ok=True)
        shutil.copy2(v["path"], d / k[1])
        manifest.append({
            "repo": k[0], "filename": k[1], "date": v["date"] or "", "words": v["words"],
            "madr_score": v["madr"], "alternativas_explicitas": tiene_alternativas(v),
            "cadena_id": cadena_id.get(k, ""), "motivo": seleccion[k], "anotar": k in anotar_set,
            "source_path": v["path"],
        })
    with open(out / "corpus_manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0]))
        w.writeheader()
        w.writerows(manifest)
    with open(out / "ground_truth_reemplazos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas_gt[0]))
        w.writeheader()
        w.writerows(filas_gt)
    with open(out / "anotacion_subset.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0]))
        w.writeheader()
        w.writerows([m for m in manifest if m["anotar"]])

    # ---------- resumen ----------
    cnt = defaultdict(int)
    for g in filas_gt:
        cnt[g["veredicto"]] += 1
    print("=== Pares de reemplazo ===")
    for ver in ("valido", "dudoso", "descartado", "excluido_no_organico", "excluido_lista_ia", "sin_revisar"):
        print(f"  {ver:22} {cnt[ver]:4d}")
    val = [g for g in filas_gt if g["veredicto"] == "valido"]
    cad_val = {g["cadena_id"] for g in val}
    print(f"\nCadenas (componentes conexos) con al menos un par válido: {len(cad_val)}")
    print("\nPares válidos por repo:")
    pr = defaultdict(int)
    for g in val:
        pr[g["repo"]] += 1
    for repo, n in sorted(pr.items(), key=lambda x: -x[1]):
        print(f"  {n:3d}  {repo}")
    mot = defaultdict(int)
    for m in manifest:
        mot[m["motivo"]] += 1
    print(f"\n=== Corpus: {len(manifest)} ADR de {len({m['repo'] for m in manifest})} repos ===")
    for m_, n in mot.items():
        print(f"  {n:4d}  {m_}")
    print(f"\n=== Anotación: {len(anotar)} ADR ({n_cad} en cadenas, {n_alt} fuera de cadena con "
          f"alternativas explícitas, {len(anotar) - n_cad - n_alt} sin alternativas) ===")
    print(f"\nSalida en {out.resolve()}")


if __name__ == "__main__":
    main()
