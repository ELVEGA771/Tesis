# `Prototipo/comun.py` — piezas compartidas

Lo importan `rag.py`, `pipeline.py` y `evaluar_b1.py`. Reúne lo que tiene que ser **idéntico** en las dos condiciones experimentales (mismo modelo, misma configuración, mismo presupuesto de contexto, misma forma de contar tokens). Si cada script tuviera su propia copia de esto, bastaría un descuido para que el RAG y el grafo usaran configuraciones distintas y la comparación dejara de ser justa.

---

## 1. Docstring e importaciones (líneas 1–17)

```python
"""Piezas compartidas por el pipeline (A5/A6), la linea base RAG (A7) y el evaluador (A8).
...
"""
from __future__ import annotations
```

- El docstring dice qué contiene el módulo y por qué existe; aparece con `help(comun)`.
- `from __future__ import annotations`: hace que las anotaciones de tipo (`list[dict]`, `dict | None`) se guarden como texto y no se evalúen al cargar. Permite usar la sintaxis moderna sin preocuparse por la versión de Python. No cambia el comportamiento.

```python
import csv
import json
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
```

| Import | Para qué se usa aquí |
|---|---|
| `csv` | Leer `corpus_manifest.csv` y `particion.csv` |
| `json` | Armar el cuerpo de la petición a Ollama y escribir el registro `.jsonl` |
| `time` | Medir la latencia de cada llamada (`perf_counter`) |
| `urllib.request` / `urllib.error` | Hablar con Ollama por HTTP **sin instalar nada** (biblioteca estándar) |
| `lru_cache` | Memorizar resultados de funciones caras (leer CSV, cargar el tokenizador) |
| `Path` | Construir rutas que funcionan igual en Windows y Linux |

**¿Por qué `urllib` y no `langchain_ollama` o el paquete `ollama`?** Tres razones: (1) menos dependencias que fijar para la reproducibilidad; (2) la respuesta cruda de Ollama trae `prompt_eval_count` y `eval_count` (tokens de entrada y salida), que es exactamente lo que necesitamos registrar como costo, y los envoltorios a veces los esconden; (3) el control de flujo lo hace LangGraph, no LangChain, así que no se necesita su capa de modelos.

## 2. Rutas (líneas 19–23)

```python
RAIZ = Path(__file__).resolve().parent
TESIS = RAIZ.parent
CORPUS = TESIS / "Extraccion_ADR" / "corpus_tesis"
DATOS = RAIZ / "datos"
REGISTROS = RAIZ / "registros"
```

- `Path(__file__)`: la ruta de este mismo archivo. `.resolve()` la vuelve absoluta (sin `..` ni enlaces). `.parent` sube a la carpeta que lo contiene → `Prototipo/`.
- `TESIS`: una carpeta más arriba → `Tesis_herramientas/`.
- `CORPUS`: donde viven `corpus_manifest.csv`, `particion.csv`, `adr/` y `anotacion/`.
- `DATOS`: salidas pesadas (índice ChromaDB, grafos generados, resúmenes de corridas).
- `REGISTROS`: los `.jsonl` de costo por llamada.

**Por qué rutas relativas a `__file__` y no al directorio actual:** así los scripts funcionan sin importar desde qué carpeta los ejecutes. Si dependieran del directorio actual, `python Prototipo/pipeline.py` y `cd Prototipo; python pipeline.py` buscarían los archivos en lugares distintos.

## 3. Configuración experimental (líneas 25–29)

```python
MODELO = "qwen3:4b-instruct"
TOKENIZADOR = "Qwen/Qwen3-4B-Instruct-2507"
OPCIONES = {"temperature": 0, "seed": 42, "num_ctx": 16384}
OLLAMA = "http://127.0.0.1:11434/api/chat"
B = 2048   # presupuesto de contexto (tokens de Qwen) igual en RAG y grafo (observacion O2)
```

- `MODELO`: la etiqueta del modelo en Ollama. Es el Qwen3-4B-Instruct de la planificación.
- `TOKENIZADOR`: el mismo modelo, pero su nombre en Hugging Face. Ollama no expone un contador de tokens confiable, así que cargamos el tokenizador oficial para **contar** tokens (no para generar).
- `OPCIONES`:
  - `temperature: 0` → el modelo elige siempre el token más probable. La planificación pide temperatura controlada para que las corridas sean comparables.
  - `seed: 42` → semilla fija para cualquier muestreo residual. Ojo: el 09-10 vimos que, aun así, Ollama en GPU **no es determinista del todo** (una corrida idéntica dio otro resultado). Eso hay que declararlo en el bloque 4.
  - `num_ctx: 16384` → tamaño de la ventana de contexto en tokens. Ollama usa 4 096 por defecto y **trunca en silencio** lo que no cabe. Con 8 192 los reintentos de ADR largos se cortaban a mitad de una llamada a función y Ollama respondía HTTP 500; por eso se subió a 16 384. El ADR más largo del corpus tiene 5 399 tokens; el reintento añade las llamadas previas y los errores, así que hace falta margen.
- `OLLAMA`: el endpoint de chat del servidor local.
- `B = 2048`: el **presupuesto de contexto** que piden las observaciones O2 y la retroalimentación del 05-10 («mismo presupuesto de contexto»). Ambas condiciones reciben como máximo 2 048 tokens de contexto, medidos con el mismo tokenizador. Con una mediana de 497 tokens por ADR, caben unos cuatro ADR típicos.

## 4. Identificador de ADR (líneas 32–33)

```python
def adr_id(repo: str, filename: str) -> str:
    return f"{repo}|{filename}"
```

Un ADR se identifica por **repositorio + nombre de fichero** (el número del ADR se repite entre repos, así que no sirve de clave). Se unen con `|` porque ese carácter no aparece ni en nombres de repo ni de ficheros.

**Cuidado con la confusión:** `csv_a_grafo.py` tiene **otra** función `adr_id` que produce el id del **nodo** en el grafo (`adr_<repo>__<fichero>`). Por eso `pipeline.py` y `evaluar_b1.py` la importan con otro nombre: `adr_id as nodo_adr`. Son dos cosas distintas: la clave de tabla (`repo|fichero`) y el id de nodo.

## 5. Lectura del manifiesto y la partición (líneas 36–44)

```python
@lru_cache
def manifiesto() -> list[dict]:
    return list(csv.DictReader(open(CORPUS / "corpus_manifest.csv", encoding="utf-8")))
```

- `csv.DictReader` convierte cada fila en un diccionario `{columna: valor}`.
- `list(...)` lo materializa todo (140 filas; cabe sobrado en memoria).
- `@lru_cache`: la primera llamada lee el disco; las siguientes devuelven la misma lista. `pipeline.py` llama a `manifiesto()` una vez por ADR para listar los «otros ADR del repo», así que sin caché se releería el CSV 140 veces.
- **Consecuencia a tener en cuenta:** como la lista está en caché, si modificas un elemento (por ejemplo `manifiesto()[0]["date"] = ...`) lo modificas para todo el programa. Ningún script lo hace; solo leen.

```python
@lru_cache
def particion() -> dict[str, str]:
    return {adr_id(r["repo"], r["filename"]): r["particion"]
            for r in csv.DictReader(open(CORPUS / "particion.csv", encoding="utf-8"))}
```

Diccionario `{"repo|fichero": "desarrollo" | "prueba"}` construido desde `particion.csv` (lo genera `particion.py`). Se usa para elegir qué ADR corre el pipeline y cuáles evalúa `evaluar_b1.py`.

## 6. Texto de un ADR (líneas 47–48)

```python
def texto_adr(repo: str, filename: str) -> str:
    return (CORPUS / "adr" / repo.replace("/", "__") / filename).read_text(encoding="utf-8", errors="replace")
```

- Los ADR están en `corpus_tesis/adr/<owner>__<repo>/<fichero>.md`. Las carpetas no pueden contener `/`, por eso `NERC-CEH/datalab` se guarda como `NERC-CEH__datalab`.
- `errors="replace"`: si algún byte no es UTF-8 válido, se sustituye por `�` en lugar de lanzar una excepción. Es la **misma** política que usa `conformidad.py` al leer el ADR para comprobar la fundamentación; si fueran distintas, una evidencia podría parecer fundamentada en un sitio y no en el otro.

## 7. Contar tokens (líneas 51–58)

```python
@lru_cache
def _tok():
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(TOKENIZADOR)
```

- El `import` está **dentro** de la función a propósito: `transformers` tarda varios segundos en cargarse, y no todos los usos de `comun.py` necesitan contar tokens. Solo se paga ese costo la primera vez que alguien lo pide.
- `@lru_cache` hace que el tokenizador se cargue una sola vez por proceso.
- El guion bajo inicial (`_tok`) indica «uso interno del módulo».

```python
def n_tokens(texto: str) -> int:
    return len(_tok()(texto, add_special_tokens=False)["input_ids"])
```

- Llamar al tokenizador devuelve un diccionario; `input_ids` es la lista de ids de tokens. Su longitud es el número de tokens.
- `add_special_tokens=False`: no cuenta marcas como inicio o fin de secuencia. Queremos medir el **contenido** que metemos en el contexto, no los envoltorios.
- **Por qué el tokenizador de Qwen y no el de bge:** el presupuesto B limita lo que **lee Qwen**, así que se mide en tokens de Qwen. bge tiene otro tokenizador y otro límite (512); eso afecta solo a cómo se calculan los vectores en `rag.py`.

## 8. La llamada al modelo (líneas 61–83)

```python
def chat(mensajes: list[dict], *, herramientas: list[dict] | None = None,
         etiqueta: str = "", registro: str | None = None, **extra) -> dict:
```

- `mensajes`: la conversación en el formato de Ollama, una lista de `{"role": "system"|"user"|"assistant", "content": ...}`.
- El `*` obliga a pasar el resto de parámetros **por nombre** (`herramientas=...`), lo que evita confundir el orden.
- `herramientas`: las definiciones de funciones (function calling). `None` para una pregunta normal (como hará el evaluador).
- `etiqueta`: texto libre que identifica la llamada en el registro (p. ej. `extraer|repo|fichero|intento`).
- `registro`: nombre del `.jsonl` donde anotar el costo; `None` para no registrar.
- `**extra`: cualquier otro campo de la API de Ollama (por ejemplo `format`) sin tener que cambiar la firma.

```python
    cuerpo = {"model": MODELO, "stream": False, "options": OPCIONES, "messages": mensajes, **extra}
    if herramientas:
        cuerpo["tools"] = herramientas
```

- `stream: False`: queremos la respuesta completa de una vez (con sus contadores de tokens), no trozo a trozo.
- `options: OPCIONES`: siempre la misma configuración. Ningún script puede cambiar la temperatura por accidente.
- `tools` solo se añade si hay herramientas; mandar una lista vacía cambia cómo algunos modelos formatean la respuesta.

```python
    req = urllib.request.Request(OLLAMA, json.dumps(cuerpo).encode(), {"Content-Type": "application/json"})
    t0 = time.perf_counter()
```

- Se arma una petición POST (al pasar un cuerpo, `urllib` usa POST) con el JSON codificado en bytes.
- `perf_counter()` es el reloj de mayor resolución para medir intervalos; no le afectan los cambios de hora del sistema.

```python
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:   # p. ej. llamada a funcion truncada que Ollama no puede parsear
        resp = {"message": {"role": "assistant", "content": "", "error": f"HTTP {e.code}: {e.read()[:200]!r}"}}
```

- `timeout=600`: si en 10 minutos no hay respuesta, se aborta. Una llamada normal dura 3–15 s; 600 s solo deja margen para la primera carga del modelo en GPU.
- `json.load(r)` lee y decodifica la respuesta.
- **Por qué se captura `HTTPError`:** Ollama responde 500 cuando el modelo genera una llamada a función que no puede interpretar (pasó el 09-10 por truncamiento). Antes esto mataba la corrida entera en el ADR 23 de 24. Ahora se devuelve un mensaje **vacío** con un campo `error`: sin llamadas a funciones, el validador verá «falta la decisión» y el reintento lo tratará como un intento fallido. Así un ADR problemático no tumba a los demás.
- `e.read()[:200]` guarda solo los primeros 200 bytes del cuerpo del error; `!r` lo escribe con comillas y escapes para que quepa en una línea.
- **Lo que NO se captura:** `URLError` (por ejemplo, Ollama apagado). Ese fallo sí debe detener todo: no tiene sentido seguir si el servidor no existe.

```python
    seg = time.perf_counter() - t0
    if registro:
        REGISTROS.mkdir(exist_ok=True)
        with open(REGISTROS / f"{registro}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"etiqueta": etiqueta, "segundos": round(seg, 3),
                                 "tokens_entrada": resp.get("prompt_eval_count"),
                                 "tokens_salida": resp.get("eval_count"),
                                 "error": resp["message"].get("error"),
                                 "modelo": MODELO, **OPCIONES}, ensure_ascii=False) + "\n")
    return resp["message"]
```

- `seg`: latencia de pared de la llamada, incluida la red local y la carga del modelo si estaba frío.
- `mkdir(exist_ok=True)`: crea `registros/` si no existe; no falla si ya está.
- Modo `"a"` (append): cada llamada **añade** una línea; nunca se borra lo anterior. Es un registro acumulativo; si quieres medir una corrida limpia, borra o renombra el `.jsonl` antes.
- Formato **JSONL** (un JSON por línea): se puede ir escribiendo sin reabrir ni reescribir el archivo, y si el programa se cae a mitad, las líneas anteriores siguen siendo válidas.
- Campos:
  - `prompt_eval_count` y `eval_count` son los nombres que usa Ollama para tokens de entrada y de salida. Con esto se calcula el **costo de construir el grafo** (punto 3 de la retroalimentación).
  - `error` queda en `null` cuando todo fue bien.
  - Se repiten `modelo` y `OPCIONES` en **cada línea** para que el registro sea autoexplicativo: si dentro de un mes cambias `num_ctx`, las líneas viejas siguen diciendo con qué configuración se generaron.
- `ensure_ascii=False`: escribe acentos tal cual en lugar de `á`.
- Devuelve solo `resp["message"]`, que contiene `content` (texto) y, si hubo function calling, `tool_calls`.
