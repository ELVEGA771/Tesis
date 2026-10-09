"""Fechas de git, commit fijado y licencia de cada ADR del corpus (observaciones O1 y O3).

    python fechas_git.py [--clones DIR]

Clona cada repositorio del manifiesto sin blobs (--filter=blob:none, sin
checkout), busca la ruta de cada ADR en HEAD y escribe fechas_git.csv con:

  fecha_registro   fecha del primer commit que añade el fichero (git log --follow
                   --diff-filter=A). Es la fecha que fija la vigencia (regla O1).
  fecha_ultimo     fecha del ultimo commit que lo toca (ediciones posteriores).
  fecha_texto      campo date escrito en el ADR (manifiesto); solo informativo.
  sha_registro     commit que lo añade.
  sha_corpus       commit en el que el fichero es identico a la copia anotada (la
                   version del corpus); descargar_adr.py descarga desde aqui.
  editado_tras_recoleccion  el ADR cambio en GitHub despues de esa version.
  head_sha         HEAD del repo el dia que se ejecuto el script.
  licencia         fichero de licencia en la raiz del repo y su primera linea
                   (O3: decidir si se redistribuye el texto o solo identificadores).

No ejecuta nada de los repos clonados: solo lee el historial con git.
"""
import argparse
import csv
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFIESTO = HERE / "corpus_manifest.csv"
SALIDA = HERE / "fechas_git.csv"


def git(repo_dir, *args):
    r = subprocess.run(["git", "-C", str(repo_dir), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else ""


def clonar(repo, destino):
    d = destino / repo.replace("/", "__")
    if not (d / ".git").exists() and not (d / "HEAD").exists():
        subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout",
                        f"https://github.com/{repo}.git", str(d)], check=True)
    return d


def commit_del_corpus(d, ruta, local: Path):
    """Commit mas reciente en el que el fichero es identico a la copia del corpus.

    Algunos ADR se editaron en GitHub despues de la recoleccion; HEAD ya no es la
    version anotada. Se compara el hash de blob de git de la copia local (tal cual
    y con finales de linea LF) con el del fichero en cada commit que lo toco.
    """
    if not local.exists():
        return ""
    datos = local.read_bytes()
    blobs = set()
    for variante in (datos, datos.replace(b"\r\n", b"\n")):
        r = subprocess.run(["git", "-C", str(d), "hash-object", "--stdin"], input=variante, capture_output=True)
        blobs.add(r.stdout.decode().strip())
    for c in git(d, "log", "--format=%H", "--", ruta).splitlines():
        if git(d, "rev-parse", f"{c}:{ruta}") in blobs:
            return c
    return ""


def licencia(d):
    nombres = git(d, "ls-tree", "--name-only", "HEAD").splitlines()
    lic = [n for n in nombres if n.upper().startswith(("LICENSE", "LICENCE", "COPYING"))]
    if not lic:
        return "", "sin licencia en la raiz"
    primera = next((x.strip() for x in git(d, "show", f"HEAD:{lic[0]}").splitlines() if x.strip()), "")
    return lic[0], primera[:80]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clones", type=Path, default=HERE / "_clones")
    args = ap.parse_args()
    args.clones.mkdir(parents=True, exist_ok=True)

    filas = list(csv.DictReader(open(MANIFIESTO, encoding="utf-8")))
    por_repo = {}
    for f in filas:
        por_repo.setdefault(f["repo"], []).append(f)

    out = []
    for repo, adrs in sorted(por_repo.items()):
        d = clonar(repo, args.clones)
        head = git(d, "rev-parse", "HEAD")
        rutas = git(d, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
        lic_fich, lic_txt = licencia(d)
        for f in adrs:
            cand = [r for r in rutas if r.endswith("/" + f["filename"]) or r == f["filename"]]
            ruta = cand[0] if len(cand) == 1 else ""
            alta = git(d, "log", "--follow", "--diff-filter=A", "--format=%H %cs", "--", ruta) if ruta else ""
            ult = git(d, "log", "-1", "--format=%cs", "--", ruta) if ruta else ""
            sha_alta, f_alta = (alta.splitlines()[-1].split() if alta else ("", ""))
            local = HERE / "adr" / repo.replace("/", "__") / f["filename"]
            sha_corpus = commit_del_corpus(d, ruta, local) if ruta else ""
            out.append({"repo": repo, "filename": f["filename"], "ruta_en_repo": ruta,
                        "nota": "" if ruta else f"{len(cand)} rutas candidatas en HEAD",
                        "fecha_registro": f_alta, "fecha_ultimo": ult, "fecha_texto": f["date"],
                        "sha_registro": sha_alta, "sha_corpus": sha_corpus,
                        "editado_tras_recoleccion": bool(sha_corpus) and sha_corpus != git(d, "log", "-1", "--format=%H", "--", ruta),
                        "head_sha": head,
                        "licencia_fichero": lic_fich, "licencia": lic_txt})
        print(f"{repo}: {sum(1 for o in out if o['repo'] == repo and o['fecha_registro'])}/{len(adrs)} con fecha | {lic_txt or lic_fich}")

    with open(SALIDA, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    con = sum(1 for o in out if o["fecha_registro"])
    print(f"\n{con}/{len(out)} ADR con fecha de registro -> {SALIDA.name}")


if __name__ == "__main__":
    main()
