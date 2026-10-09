# `Extraccion_ADR/corpus_tesis/particion.py` — desarrollo frente a prueba

**Problema que resuelve.** La retroalimentación del 05-10 (punto 2) pide separar los ejemplos que se usan para **ajustar** (la ontología, las consultas, los prompts) de los casos con los que se **evalúa**, mantener juntas las decisiones de una misma cadena y valorar una reserva por repositorio. Si ajustas los prompts mirando un ADR y luego lo evalúas, el resultado está inflado: el sistema ya «vio» la respuesta.

**Uso:**
```
python particion.py              # escribe particion.csv
python particion.py --congelar   # además guarda los hashes del gold
```

---

## 1. Importaciones (líneas 18–25)

```python
import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
```

- `argparse`: lee la opción `--congelar` de la línea de comandos.
- `hashlib`: calcula SHA-256 para congelar el gold.
- `Counter`: cuenta pares (cadena, partición) para detectar cadenas partidas.
- `date`: pone la fecha de congelación.

## 2. La lista de repos de desarrollo (líneas 27–34)

```python
HERE = Path(__file__).resolve().parent
DESARROLLO = {
    "DFE-Digital/early-years-foundation-recovery",   # C02, MADR, ya usado al ajustar
    "cloudfoundry/cloud_controller_ng",               # C12, Nygard
    "home-assistant/architecture",                    # C21, sin alternativas
    "grafana/grafana-image-renderer",                 # MADR con alternativas
    "remix-run/react-router",                         # Nygard con alternativas
}
```

- `HERE`: la carpeta `corpus_tesis/`.
- `DESARROLLO` es un **conjunto** (`{...}`), no una lista: solo importa si un repo está o no, y la búsqueda en un conjunto es inmediata.
- **Por qué estos cinco:**
  - DFE recovery va **obligatoriamente** a desarrollo: ya se usó en `prueba_corpus_dfe.py` para probar el esquema y sus ADR 0006 y 0011 son los ejemplos resueltos de la guía de anotación. Evaluar sobre él sería evaluar sobre datos de ajuste.
  - Los otros cuatro completan unos 10 ADR anotados (≈ 20 % de los 45) y cubren los casos que el pipeline tiene que aprender a manejar: formato MADR y Nygard, con y sin lista de alternativas, con cadena de reemplazo (C02, C12, C21) y sin ella.
- Los comentarios dicen el motivo de cada uno para que la elección sea auditable en la tesis.

## 3. Asignación (líneas 37–38)

```python
def particion(repo):
    return "desarrollo" if repo in DESARROLLO else "prueba"
```

La partición depende **solo del repositorio**. Esto garantiza dos cosas que pide la retroalimentación:
1. **Reserva por repositorio:** el sistema nunca se evalúa sobre un repo cuyo estilo de redacción ya vio al ajustar.
2. **Cadenas enteras:** una cadena de reemplazo siempre está dentro de un repo, así que no puede quedar partida.

## 4. Leer la partición desde otros scripts (líneas 41–44)

```python
def leer():
    """{(repo, filename): 'desarrollo'|'prueba'} para los 140 ADR."""
    return {(r["repo"], r["filename"]): r["particion"]
            for r in csv.DictReader(open(HERE / "particion.csv", encoding="utf-8"))}
```

Lee el CSV ya generado. Devuelve un diccionario con clave tupla `(repo, fichero)`. (`comun.particion()` hace lo mismo con clave `"repo|fichero"`; el prototipo usa esa.)

## 5. Hash (líneas 47–48)

```python
def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
```

Lee el archivo como bytes y devuelve su huella SHA-256 en hexadecimal (64 caracteres). Cualquier cambio en el archivo, aunque sea un espacio, cambia la huella por completo.

## 6. Programa principal (líneas 51–89)

```python
    ap = argparse.ArgumentParser()
    ap.add_argument("--congelar", action="store_true")
    args = ap.parse_args()
```

`store_true`: si escribes `--congelar`, `args.congelar` vale `True`; si no, `False`.

```python
    manifiesto = list(csv.DictReader(open(HERE / "corpus_manifest.csv", encoding="utf-8")))
    subset = {(r["repo"], r["filename"]): r["cadena_id"]
              for r in csv.DictReader(open(HERE / "anotacion_subset.csv", encoding="utf-8"))}
```

- `manifiesto`: los 140 ADR del corpus.
- `subset`: los 45 ADR anotados, con su id de cadena (`C02`, `""` si no pertenece a ninguna).

```python
    filas = [{"repo": r["repo"], "filename": r["filename"], "particion": particion(r["repo"]),
              "anotado": (r["repo"], r["filename"]) in subset, "cadena": r["cadena_id"]}
             for r in manifiesto]
```

Una fila por cada ADR **del corpus** (no solo los anotados). Así también se sabe a qué lado van los 95 ADR sin anotar: el pipeline los procesa igual (cuentan para el costo y para el grafo completo que usará A8), aunque no puntúan en el bloque 1 porque no tienen gold.

```python
    with open(HERE / "particion.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)
```

- `newline=""` es obligatorio con el módulo `csv` en Windows; sin él aparecen líneas en blanco entre filas.
- `fieldnames=list(filas[0])`: las columnas son las claves del primer diccionario, en su orden.

```python
    for p in ("desarrollo", "prueba"):
        xs = [f for f in filas if f["particion"] == p]
        an = [f for f in xs if f["anotado"]]
        cad = sorted({subset[(f["repo"], f["filename"])] for f in an} - {""})
        print(f"{p:<10} {len(xs):>3} ADR | {len(an):>2} anotados | {len({f['repo'] for f in xs})} repos | cadenas {cad}")
```

Resumen por partición. `{...} - {""}` quita el valor vacío (ADR sin cadena) del conjunto de cadenas. `:<10` y `:>3` alinean columnas. Salida actual:

```
desarrollo  24 ADR | 10 anotados | 5 repos | cadenas ['C02', 'C12', 'C21']
prueba     116 ADR | 35 anotados | 20 repos | cadenas ['C01', 'C06', 'C08', 'C11', 'C14', 'C15', 'C18', 'C22', 'C26']
```

```python
    lados = Counter()
    for f in filas:
        if f["cadena"]:
            lados[(f["cadena"], f["particion"])] += 1
    partidas = {c for c, _ in lados if sum(1 for c2, _ in lados if c2 == c) > 1}
    assert not partidas, f"cadenas partidas: {partidas}"
```

**Comprobación de seguridad.** Cuenta en qué particiones aparece cada cadena. Si una cadena aparece en las dos, está partida y el `assert` detiene el programa con un mensaje. Por construcción (todo por repo) no debería pasar nunca; el `assert` está para que, si algún día alguien cambia `particion()` por otra regla, el error salte en vez de pasar en silencio.

```python
    if args.congelar:
        anot = HERE / "anotacion"
        reg = {"fecha": date.today().isoformat(),
               "anotaciones.csv": sha(anot / "anotaciones.csv"),
               "gold_anotado.json": sha(anot / "gold_anotado.json"),
               "particion.csv": sha(HERE / "particion.csv")}
        (HERE / "gold_congelado.json").write_text(json.dumps(reg, indent=2), encoding="utf-8")
        print("congelado:", reg)
```

**Congelación del gold.** Se acordó el 29-09 para mitigar el conflicto de interés del anotador único: antes de ajustar el pipeline sobre la partición de prueba, se publican los hashes de la hoja, del grafo gold y de la partición. En la tesis puedes declarar «el gold con hash X se fijó el día D»; cualquiera puede verificar después que no se tocó tras ver los resultados. **Se ejecuta cuando termines los 45 ADR**, y no antes, porque cada cambio en la hoja cambia el hash.
