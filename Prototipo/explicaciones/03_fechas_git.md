# `Extraccion_ADR/corpus_tesis/fechas_git.py` — fechas de registro y licencias

**Problema que resuelve.**
- **Observación O1** y punto 1 de la retroalimentación del 05-10: hay que distinguir la fecha del documento, su última modificación y el momento del reemplazo. El campo `date` de MADR es opcional y es de **última actualización**, así que no sirve para ordenar reemplazos. Antes de este script, el gold usaba esa fecha del texto como `registrationDate`, y 14 de los 45 ADR no tenían fecha.
- **Observación O3**: que un repo sea público no da permiso para redistribuir su contenido; hay que mirar la licencia de cada uno.

**Solución:** la fecha de registro de un ADR es la del **commit que añadió el fichero** al repositorio. Es objetiva, existe para todos los ADR y no depende de que el autor la escribiera.

**Resultado del 08-10:** 140/140 ADR con fecha; la fecha escrita difiere de la de git en 77 de 110 casos; git nunca invierte el orden de un reemplazo (0 de 36); 5 pares se añadieron en el mismo commit; 41 ADR vienen de repos sin licencia.

---

## 1. Importaciones y rutas (líneas 19–26)

```python
import argparse
import csv
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFIESTO = HERE / "corpus_manifest.csv"
SALIDA = HERE / "fechas_git.csv"
```

`subprocess` ejecuta el programa `git` y recoge lo que imprime.

## 2. Ejecutar git (líneas 29–32)

```python
def git(repo_dir, *args):
    r = subprocess.run(["git", "-C", str(repo_dir), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else ""
```

- `*args` recoge cualquier número de argumentos: `git(d, "log", "-1")` ejecuta `git -C d log -1`.
- `-C <dir>`: ejecuta git como si estuvieras en esa carpeta, sin cambiar el directorio del programa.
- Se pasa una **lista** y no un texto: así no interviene el shell y un nombre raro no puede interpretarse como otro comando.
- `capture_output=True, text=True`: recoge la salida como texto en vez de mostrarla.
- `errors="replace"`: algunos mensajes de commit traen bytes no UTF-8; se sustituyen en vez de fallar.
- Si git falla (`returncode != 0`), devuelve cadena vacía. Quien llama interpreta «vacío» como «no hay dato», y eso termina como celda vacía en el CSV en lugar de detener el programa por un solo fichero.

## 3. Clonar sin descargar el contenido (líneas 35–40)

```python
def clonar(repo, destino):
    d = destino / repo.replace("/", "__")
    if not (d / ".git").exists() and not (d / "HEAD").exists():
        subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout",
                        f"https://github.com/{repo}.git", str(d)], check=True)
    return d
```

- La carpeta del clon se llama como en `adr/` (`owner__repo`).
- La condición evita volver a clonar si ya existe: el script se puede ejecutar de nuevo sin descargar todo otra vez.
- `--filter=blob:none` (**clon parcial**): baja todo el historial (commits, árboles de carpetas, fechas, autores) pero **no** el contenido de los ficheros, que se pide solo si hace falta. Un repo grande como `react-router` pasa de cientos de MB a unos pocos.
- `--no-checkout`: no crea los ficheros en disco. Solo necesitamos el historial.
- **Por qué clonar y no usar la API de GitHub:** sin token, la API permite 60 peticiones por hora; para 140 ADR más búsqueda de rutas haría falta bastante más. Además, git es reproducible y no depende de un servicio.
- `check=True`: si el clon falla (repo borrado, sin red), el programa se detiene. Sin el clon no hay datos de ese repo, así que es mejor enterarse.
- **Seguridad:** solo se **lee** el historial con `git log`, `ls-tree` y `show`. No se ejecuta nada del repo (clonar no ejecuta hooks del repositorio remoto).

## 4. Licencia del repositorio (líneas 43–49)

```python
def licencia(d):
    nombres = git(d, "ls-tree", "--name-only", "HEAD").splitlines()
    lic = [n for n in nombres if n.upper().startswith(("LICENSE", "LICENCE", "COPYING"))]
    if not lic:
        return "", "sin licencia en la raiz"
    primera = next((x.strip() for x in git(d, "show", f"HEAD:{lic[0]}").splitlines() if x.strip()), "")
    return lic[0], primera[:80]
```

- `ls-tree --name-only HEAD`: lista los ficheros de la raíz en la versión actual (sin descargarlos).
- Busca nombres que empiecen por `LICENSE`, `LICENCE` (ortografía británica) o `COPYING` (convención GNU), en cualquier caso de mayúsculas.
- `git show HEAD:<fichero>` obtiene su contenido (aquí sí se descarga ese único blob).
- `next(...)` toma la primera línea no vacía, que normalmente nombra la licencia («MIT License», «Apache License»…). Se recorta a 80 caracteres.
- **Limitación:** es una heurística. Algunos ficheros empiezan con «Copyright (c) …» o con una sección numerada («1 Definitions.»), y la primera línea no dice el tipo. Para la tesis conviene revisar a mano esos pocos casos. Tampoco detecta licencias declaradas solo en `package.json` o en el README.

## 5. Programa principal (líneas 52–92)

```python
    ap.add_argument("--clones", type=Path, default=HERE / "_clones")
    ...
    args.clones.mkdir(parents=True, exist_ok=True)
```

Carpeta donde guardar los clones (por defecto `corpus_tesis/_clones`). `parents=True` crea las carpetas intermedias.

```python
    filas = list(csv.DictReader(open(MANIFIESTO, encoding="utf-8")))
    por_repo = {}
    for f in filas:
        por_repo.setdefault(f["repo"], []).append(f)
```

Agrupa los 140 ADR por repositorio, para clonar cada repo **una sola vez** aunque tenga 19 ADR. `setdefault` crea la lista vacía la primera vez que aparece el repo.

```python
    for repo, adrs in sorted(por_repo.items()):
        d = clonar(repo, args.clones)
        head = git(d, "rev-parse", "HEAD")
        rutas = git(d, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
        lic_fich, lic_txt = licencia(d)
```

- `rev-parse HEAD`: el hash del commit actual de la rama principal el día que se ejecuta el script. **Ojo:** no siempre es la versión anotada, porque algunos ADR se editaron después de la recolección. El commit que fija la versión del corpus es `sha_corpus` (sección 6).
- `ls-tree -r --name-only`: lista **recursiva** de todos los ficheros del repo. Hace falta porque el manifiesto guarda solo el nombre del fichero, no en qué carpeta del repo está (`docs/adr/`, `architecture/decisions/`…).

```python
        for f in adrs:
            cand = [r for r in rutas if r.endswith("/" + f["filename"]) or r == f["filename"]]
            ruta = cand[0] if len(cand) == 1 else ""
```

- Busca las rutas que terminan en `/<fichero>`. El `/` delante evita confundir `0001-x.md` con `a0001-x.md`.
- Solo acepta la ruta si hay **exactamente una** candidata. Si hubiera dos ficheros con el mismo nombre en distintas carpetas, sería ambiguo y es preferible dejarlo vacío (y anotar el motivo en `nota`) que adivinar. En el corpus actual no pasó.

```python
            alta = git(d, "log", "--follow", "--diff-filter=A", "--format=%H %cs", "--", ruta) if ruta else ""
            ult = git(d, "log", "-1", "--format=%cs", "--", ruta) if ruta else ""
            sha_alta, f_alta = (alta.splitlines()[-1].split() if alta else ("", ""))
```

Es el corazón del script:
- `git log -- <ruta>`: los commits que tocaron ese fichero.
- `--follow`: sigue el fichero aunque lo hayan **renombrado o movido** (muchos repos movieron sus ADR de `doc/` a `docs/adr/`). Sin esto, la «fecha de alta» sería la del movimiento, no la de creación.
- `--diff-filter=A`: solo los commits donde el fichero fue **añadido** (A = Added).
- `--format=%H %cs`: hash completo y fecha del commit en formato `AAAA-MM-DD`. Se usa la fecha de **commit**, que es cuando entró al repositorio, no la de autoría.
- `git log` lista del más reciente al más antiguo; con `--follow` puede haber más de una «alta» (por ejemplo, si se borró y se volvió a añadir). `splitlines()[-1]` toma la **última línea**, es decir, la alta más antigua.
- `ult`: con `-1`, el commit más reciente que tocó el fichero. Sirve para saber si el ADR se **editó después** (relevante para O1: un ADR puede haberse editado tras ser reemplazado).

```python
            out.append({"repo": repo, "filename": f["filename"], "ruta_en_repo": ruta,
                        "nota": "" if ruta else f"{len(cand)} rutas candidatas en HEAD",
                        "fecha_registro": f_alta, "fecha_ultimo": ult, "fecha_texto": f["date"],
                        "sha_registro": sha_alta, "sha_corpus": sha_corpus,
                        "editado_tras_recoleccion": ..., "head_sha": head,
                        "licencia_fichero": lic_fich, "licencia": lic_txt})
```

Una fila por ADR con las **tres fechas** que pide distinguir la retroalimentación:

| Columna | Qué es | Para qué se usa |
|---|---|---|
| `fecha_registro` | Commit que añadió el fichero | **Fija la vigencia** (`registrationDate` en el grafo) |
| `fecha_ultimo` | Último commit que lo tocó | Detectar ediciones posteriores |
| `fecha_texto` | Campo `date` escrito en el ADR | Solo informativa (`lastUpdateDate`) |

```python
        print(f"{repo}: {sum(1 for o in out if o['repo'] == repo and o['fecha_registro'])}/{len(adrs)} con fecha | {lic_txt or lic_fich}")
```

Progreso por repo: cuántos ADR obtuvieron fecha y qué licencia tiene.

```python
    with open(SALIDA, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
```

Escribe `fechas_git.csv`. Desde el 08-10, `csv_a_grafo.fechas_manifiesto()` lee este fichero y usa `fecha_registro` como `registrationDate`; si el fichero no existe, vuelve a la fecha del texto.

## 6. Commit del corpus (`sha_corpus`), añadido el 10-10

```python
def commit_del_corpus(d, ruta, local: Path):
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
```

**Por qué existe:** al preparar la publicación se descubrió que 3 ADR se **editaron en GitHub después de la recolección** (OpenTermsArchive 0001 recibió una enmienda el 2026-09-23; pepr 0022 y home-assistant 0018 también cambiaron en septiembre). Si alguien descargara la versión de `HEAD`, obtendría un texto distinto del anotado y algunas evidencias no se encontrarían.

**Cómo funciona:**
- Git identifica cada versión de un fichero por un hash de su contenido (el *blob*). `git hash-object --stdin` calcula ese hash para la copia local **sin guardarla** en el repo.
- Se calcula dos veces: con los bytes tal cual y convirtiendo `\r\n` → `\n`, porque en Windows la copia local puede tener finales de línea distintos de los del repo.
- `git log --format=%H -- ruta` lista los commits que tocaron el fichero, del más reciente al más antiguo. Para cada uno, `git rev-parse <commit>:<ruta>` da el hash del fichero en ese commit. El primero que coincide es la **versión anotada**.
- Resultado: los 140 ADR tienen `sha_corpus`; la columna `editado_tras_recoleccion` marca los 3 que cambiaron después.

## 7. `descargar_adr.py`: el procedimiento de obtención (O3)

Como los textos no se publican, el repositorio incluye `descargar_adr.py`, que lee `fechas_git.csv` y descarga cada ADR desde `https://raw.githubusercontent.com/<repo>/<sha_corpus>/<ruta>` a `adr/<owner>__<repo>/<fichero>`.
- Usa `sha_corpus` (y `head_sha` solo si faltara), así que reconstruye **exactamente** la versión anotada. Verificado el 10-10: 140/140 ficheros idénticos a la copia local.
- Si el fichero ya existe, no lo vuelve a descargar.
- `raw.githubusercontent.com` no tiene el límite de 60 peticiones por hora de la API; aun así espera 0.1 s entre descargas para no saturar.

## 8. Cómo se usa esto en la regla temporal

- **Vigencia:** un ADR existe desde su `fecha_registro`, y un ADR reemplazado pasa a `superseded` en la `fecha_registro` del ADR que lo reemplaza (`consultas.cq7_estado_en_fecha`).
- **Orden del reemplazo:** lo da la relación **declarada en el texto**; git lo confirma en 31 de 36 pares y no lo contradice en ninguno.
- **Importaciones en bloque:** en 5 pares (C01, C05, C06 ×2, C13), reemplazado y reemplazante entraron en el **mismo commit**. El instante del reemplazo es indeterminado, así que no se generan preguntas de «estado en la fecha F» entre ambos ADR.
