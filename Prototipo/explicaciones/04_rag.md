# `Prototipo/rag.py` — línea base RAG vectorial plana (A7)

**Qué es en la tesis.** La condición de control. La planificación (§5) fija: RAG clásico sobre el mismo corpus, embeddings `BAAI/bge-base-en-v1.5`, base vectorial ChromaDB sin servidor. En A8, un único Qwen responde las mismas preguntas recibiendo como contexto **o bien** los fragmentos que devuelve este archivo, **o bien** el subgrafo. La representación es la única variable.

**Uso:**
```
python rag.py indexar                                  # construye el índice (una vez)
python rag.py "Which reverse proxy does X use now?"    # muestra qué recuperaría
```

**Las dos fases de un RAG:**
1. **Indexar** (una vez): partir cada documento en fragmentos, convertir cada fragmento en un vector que representa su significado y guardarlo.
2. **Recuperar** (por pregunta): convertir la pregunta en vector, buscar los fragmentos cuyos vectores están más cerca y pasárselos al modelo como contexto.

---

## 1. Importaciones y constantes (líneas 15–27)

```python
from __future__ import annotations

import re
import sys
from functools import lru_cache

from comun import B, DATOS, adr_id, manifiesto, n_tokens, texto_adr
```

- `re`: expresiones regulares para partir el Markdown por encabezados.
- De `comun` vienen el presupuesto `B`, la carpeta `DATOS`, el contador de tokens de Qwen y el lector de ADR. Así el RAG mide el contexto **igual** que lo hará la condición grafo.

```python
MODELO_EMB = "BAAI/bge-base-en-v1.5"
INSTRUCCION = "Represent this sentence for searching relevant passages: "   # bge v1.5, solo consultas
COLECCION = "adr_secciones"
MAX_FRAG = 350
```

- `MODELO_EMB`: el modelo de embeddings fijado en la planificación. Produce vectores de 768 dimensiones y lee como máximo 512 tokens (de su propio tokenizador).
- `INSTRUCCION`: los autores de bge v1.5 recomiendan anteponer esta frase **solo a las preguntas** (no a los documentos) cuando la pregunta es corta y los pasajes largos. Mejora la recuperación porque le indica al modelo que el vector es de una búsqueda, no de un pasaje.
- `COLECCION`: nombre de la tabla dentro de ChromaDB.
- `MAX_FRAG = 350`: tamaño máximo deseado de un fragmento en tokens de Qwen. Razones: (a) 350 de Qwen ≈ 400–450 de bge, por debajo del límite de 512, así que normalmente bge ve el fragmento entero; (b) con B = 2048 caben al menos 5–6 fragmentos, de modo que el contexto puede combinar varios ADR, que es lo que exigen las preguntas sobre cadenas.

## 2. Título del ADR (líneas 30–32)

```python
def _titulo(texto, filename):
    m = re.search(r"(?m)^#\s+(.+)$", texto)
    return m.group(1).strip() if m else filename
```

- `(?m)` activa el modo multilínea: `^` y `$` marcan inicio y fin de **cada línea**, no del texto completo.
- `^#\s+(.+)$`: una línea que empieza con **un** `#` y un espacio (encabezado de nivel 1). Como después de `#` exige espacio, `## Context` no coincide.
- Devuelve el texto del título; si el ADR no tiene título en Markdown, usa el nombre del fichero.

## 3. Fragmentación (líneas 35–50)

```python
def fragmentar(repo, filename):
    texto = texto_adr(repo, filename)
    titulo = _titulo(texto, filename)
    partes = re.split(r"(?m)^(?=#{2,4}\s)", texto)
```

- `re.split` corta el texto **justo antes** de cada encabezado de nivel 2 a 4 (`##`, `###`, `####`).
- `(?=...)` es una **anticipación**: comprueba que viene un encabezado pero no lo consume, así que el encabezado se queda al principio de su parte («## Decision\nWe will…»). Sin la anticipación, el encabezado se perdería.
- **Por qué cortar por secciones:** los ADR se escriben por secciones con funciones distintas (Context, Decision, Consequences, Status). Una sección es una unidad de significado coherente; cortar cada N tokens partiría frases y mezclaría secciones.
- La primera parte (antes del primer `##`) contiene el `# Título` y, en muchos ADR, la línea de estado y la fecha.

```python
    frags = []
    for p in partes:
        p = p.strip()
        if not p:
            continue
```

Descarta partes vacías (por ejemplo, si el fichero empieza directamente con `##`).

```python
        m = re.match(r"#{1,4}\s+(.+)", p)
        seccion = m.group(1).strip() if m else "(inicio)"
```

El nombre de la sección es el texto de su encabezado. `re.match` solo mira el principio de la parte. La primera parte empieza con `# Título`, así que su «sección» es el propio título; si no hay ningún encabezado, se llama `(inicio)`.

```python
        bloques = [p] if n_tokens(p) <= MAX_FRAG else _por_parrafos(p)
        for b in bloques:
            frags.append({"texto": f"{titulo}\n{b}", "repo": repo, "filename": filename,
                          "seccion": seccion, "titulo": titulo})
    return frags
```

- Si la sección cabe en 350 tokens, es un único fragmento; si no, se divide por párrafos.
- **El título va delante de cada fragmento.** Una sección «## Decision — We will use Kong» suelta no dice de qué trata; con el título («Use Kong for reverse proxy») el vector recoge el tema. Es una técnica estándar (enriquecer el fragmento con el contexto del documento) y no da ventaja injusta: el grafo también sabe a qué ADR pertenece cada nodo.
- Los metadatos (`repo`, `filename`, `seccion`, `titulo`) se guardan aparte del texto para poder decir **de dónde** viene cada fragmento. Sirven para la trazabilidad y para citar la fuente en el contexto.

## 4. División por párrafos (líneas 53–64)

```python
def _por_parrafos(p):
    out, actual = [], ""
    for par in re.split(r"\n\s*\n", p):
        cand = f"{actual}\n\n{par}" if actual else par
        if actual and n_tokens(cand) > MAX_FRAG:
            out.append(actual)
            actual = par
        else:
            actual = cand
    if actual:
        out.append(actual)
    return out
```

Algoritmo **voraz** de acumulación:
- `\n\s*\n` separa párrafos: un salto de línea, espacios opcionales y otro salto (línea en blanco).
- `actual` es el fragmento que se va llenando. Para cada párrafo se prueba añadirlo (`cand`).
- Si al añadirlo se pasa de 350 tokens **y** `actual` ya tenía algo, se cierra `actual` y el párrafo empieza un fragmento nuevo.
- Si no se pasa, se acumula.
- Al final se guarda lo que quedó.
- **Limitación (anotada en el índice):** un solo párrafo de más de 350 tokens queda como fragmento entero. Si supera los 512 tokens de bge, bge solo «ve» el principio para calcular el vector, aunque el texto completo sí se guarda y llega a Qwen. Afecta a muy pocos fragmentos; si quisieras eliminarlo, habría que dividir también por frases.

## 5. Modelo de embeddings y colección (líneas 67–78)

```python
@lru_cache
def _emb():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODELO_EMB)
```

Carga bge una sola vez por proceso (`lru_cache`). El import va dentro para no pagar su carga si solo se usan otras funciones. La primera vez descarga el modelo de Hugging Face (~440 MB) y lo guarda en caché local.

```python
@lru_cache
def _col():
    import chromadb
    cli = chromadb.PersistentClient(path=str(DATOS / "chroma"))
    return cli.get_or_create_collection(COLECCION, metadata={"hnsw:space": "cosine"})
```

- `PersistentClient`: ChromaDB guarda la base en disco (`datos/chroma/`), sin servidor. Es lo que la planificación justifica como «reproducibilidad sin servidor».
- `get_or_create_collection`: abre la colección o la crea si no existe.
- `hnsw:space: cosine`: la distancia es **coseno**, el estándar con bge. HNSW es el índice de vecinos aproximados que usa Chroma; con 1 067 vectores es prácticamente exacto.

## 6. Indexar (líneas 81–97)

```python
def indexar():
    import chromadb
    cli = chromadb.PersistentClient(path=str(DATOS / "chroma"))
    if COLECCION in [c.name for c in cli.list_collections()]:
        cli.delete_collection(COLECCION)
    _col.cache_clear()
```

- **Borra y reconstruye** la colección entera. Si solo se añadieran fragmentos, al reindexar quedarían duplicados.
- `_col.cache_clear()`: olvida la colección vieja que pudiera estar en caché, para que `_col()` cree la nueva.

```python
    frags = [f for r in manifiesto() for f in fragmentar(r["repo"], r["filename"])]
```

Fragmenta los **140 ADR del corpus**, no solo los 45 anotados. El RAG busca en todo el corpus, igual que el grafo contendrá los 140: los 95 no anotados actúan como **distractores** (ADR del mismo repo con vocabulario parecido), que es justo lo que hace difícil la pregunta «cuál es la decisión vigente».

```python
    vec = _emb().encode([f["texto"] for f in frags], normalize_embeddings=True,
                        batch_size=32, show_progress_bar=True)
```

- Calcula un vector por fragmento, en lotes de 32 (aprovecha la GPU sin agotar memoria).
- `normalize_embeddings=True`: deja cada vector con longitud 1. Con vectores normalizados, la similitud coseno es simplemente el producto escalar; es lo que recomienda bge.
- A los fragmentos **no** se les antepone `INSTRUCCION`; solo a las preguntas.

```python
    col = _col()
    for i in range(0, len(frags), 500):
        lote = frags[i:i + 500]
        col.add(ids=[f"{adr_id(f['repo'], f['filename'])}#{i + j}" for j, f in enumerate(lote)],
                embeddings=vec[i:i + 500].tolist(),
                documents=[f["texto"] for f in lote],
                metadatas=[{k: f[k] for k in ("repo", "filename", "seccion", "titulo")} for f in lote])
    print(f"{len(frags)} fragmentos de {len(manifiesto())} ADR indexados")
```

- Se inserta en lotes de 500 porque Chroma limita el tamaño de cada inserción.
- `ids`: `repo|fichero#n`, donde `n` es la posición global del fragmento. Es único y legible.
- `embeddings`: se pasan **nuestros** vectores. Así Chroma no usa su modelo por defecto (que sería otro distinto del fijado en la planificación).
- `documents`: el texto, que es lo que se devolverá al recuperar.
- `metadatas`: solo campos simples (Chroma no admite listas ni objetos).
- Resultado actual: **1 067 fragmentos de 140 ADR**.

## 7. Recuperar con presupuesto (líneas 100–114)

```python
def recuperar(pregunta: str, presupuesto: int = B, candidatos: int = 50):
    """Fragmentos por similitud hasta llenar el presupuesto. Devuelve (contexto, fragmentos usados)."""
    q = _emb().encode([INSTRUCCION + pregunta], normalize_embeddings=True)[0].tolist()
    r = _col().query(query_embeddings=[q], n_results=candidatos)
```

- La pregunta se vectoriza **con** la instrucción de bge.
- Se piden los 50 fragmentos más cercanos. Con fragmentos de ~100 tokens de media, B = 2048 se llena con unos 15–20, así que 50 candidatos sobran.
- **Esto responde a la observación O2:** en vez de un `k` fijo (los 5 mejores), el número de fragmentos lo decide el **presupuesto de tokens**. Así el grafo y el RAG reciben como máximo la misma cantidad de contexto, y la diferencia en las respuestas se puede atribuir a la representación y no a que una condición leyó más texto.

```python
    usados, total, piezas = [], 0, []
    for doc, meta, dist in zip(r["documents"][0], r["metadatas"][0], r["distances"][0]):
```

- Chroma devuelve listas de listas (una por pregunta); `[0]` toma la de nuestra única pregunta.
- `zip` recorre en paralelo texto, metadatos y distancia, ya ordenados del más cercano al más lejano.

```python
        pieza = f"[{meta['repo']}/{meta['filename']} · {meta['seccion']}]\n{doc}"
        t = n_tokens(pieza) + 2
        if total + t > presupuesto:
            continue
        piezas.append(pieza)
        total += t
        usados.append({**meta, "similitud": round(1 - dist, 4), "tokens": t})
```

- Cada fragmento se presenta con una **cabecera de procedencia** `[repo/fichero · sección]`. Así Qwen puede citar de qué ADR viene la respuesta (trazabilidad, bloque 2), igual que en el grafo cada nodo pertenece a un ADR.
- `+ 2`: los dos saltos de línea `\n\n` que separan fragmentos al unirlos. Se cuentan para que el total sea exacto.
- Si un fragmento **no cabe**, se salta con `continue` y se sigue probando con los siguientes, que pueden ser más cortos. Así se aprovecha el presupuesto (en la prueba: 2047/2048).
  - **Consecuencia:** el contexto no es estrictamente «los N mejores»; un fragmento algo menos similar pero corto puede entrar en lugar de uno largo y más similar. Es una decisión a declarar. La alternativa (detenerse en el primero que no cabe) desperdicia presupuesto. Si prefieres esa, se cambia `continue` por `break`.
- `similitud = 1 - distancia`: Chroma devuelve distancia coseno; se convierte a similitud (1 = idéntico) para que sea más fácil de leer.

```python
    return "\n\n".join(piezas), usados
```

Devuelve dos cosas: el **texto de contexto** listo para meter en el prompt de Qwen, y la **lista de fragmentos usados** (para registrar qué se recuperó, analizar fallos y calcular trazabilidad).

## 8. Línea de comandos (líneas 117–124)

```python
if __name__ == "__main__":
    if sys.argv[1:] == ["indexar"]:
        indexar()
    else:
        ctx, usados = recuperar(" ".join(sys.argv[1:]))
        for u in usados:
            print(f"{u['similitud']:.3f} {u['tokens']:>4} tok  {u['repo']}/{u['filename']} · {u['seccion']}")
        print(f"total {sum(u['tokens'] for u in usados)} / {B} tokens")
```

- `if __name__ == "__main__"`: este bloque solo se ejecuta al llamar al archivo directamente, no al importarlo desde el evaluador.
- `indexar` construye el índice; cualquier otro texto se trata como pregunta y se muestra qué se recuperaría, con su similitud y tamaño. Es una herramienta de inspección; la respuesta de Qwen la generará el evaluador de A8.

## Qué falta para A8

`rag.py` solo **recupera**. Falta la parte que arma el prompt («Contexto: … Pregunta: …»), llama a Qwen con `comun.chat` sin herramientas y registra latencia y tokens. Esa misma función servirá para la condición grafo, cambiando solo de dónde sale el contexto.
