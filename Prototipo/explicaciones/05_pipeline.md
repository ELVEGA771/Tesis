# `Prototipo/pipeline.py` — pipeline de construcción del grafo (A5 + A6)

**Qué es en la tesis.** El sistema propuesto, etapa de construcción. La planificación (§5) lo define como un pipeline **secuencial determinista de cuatro nodos**, no un orquestador dinámico: (1) extracción de evidencia y alternativas → (2) identificación de la decisión y sus relaciones → (3) escritura en el grafo → (4) validación, con un único ciclo de reintento acotado (máximo 3) entre validación y extracción, siguiendo Reflexion (Shinn et al.). Implementado con LangGraph sobre Qwen3-4B-Instruct, que escribe mediante **llamadas a funciones con esquema fijo**.

**Uso:**
```
python pipeline.py --particion desarrollo              # todos los ADR de desarrollo
python pipeline.py --particion desarrollo --limite 3   # solo los 3 primeros
python pipeline.py --adr "repo/x|0011-contentful.md"   # uno solo
```

---

## 0. Conceptos de LangGraph que necesitas

LangGraph modela un programa como un **grafo de estados**:

- **Estado:** un diccionario que viaja de nodo en nodo. Aquí es la clase `Estado` (repo, texto del ADR, llamadas, filas, errores, intentos…).
- **Nodo:** una función Python que **recibe el estado completo** y **devuelve solo las claves que cambia**. LangGraph mezcla ese diccionario parcial con el estado: cada clave devuelta **reemplaza** a la anterior (no se acumulan listas salvo que se configure). Por eso `nodo_validar` construye el historial nuevo como `historial_viejo + [entrada]` y devuelve la lista entera.
- **Arista:** dice qué nodo va después. `add_edge(a, b)` es fija; `add_conditional_edges` llama a una función que decide el siguiente nodo según el estado.
- **`END`:** nodo especial que termina la ejecución.
- **`compile()`:** valida el grafo (que no haya nodos sueltos, etc.) y devuelve un objeto ejecutable con `.invoke(estado_inicial)`.

**Por qué LangGraph y no un bucle `for` normal:** la planificación lo justifica porque el flujo de control queda **declarado** (se ve y se puede dibujar), soporta el ciclo de reintento y tiene checkpointing (guardar el estado tras cada nodo). Un bucle `while` haría lo mismo hoy, pero el grafo declarado es lo que la tesis describe y evalúa. (El checkpointing todavía no está activado; está planificado para S3.)

El flujo de este archivo:

```
 extraer ──► decidir ──► escribir ──► validar ──┬──► END   (sin errores, o 3 intentos)
    ▲                                           │
    └───────────── errores y quedan intentos ───┘
```

---

## 1. Docstring e importaciones (líneas 1–37)

El docstring resume los cuatro nodos y las salidas. Importaciones relevantes:

```python
from typing import TypedDict

import networkx as nx
from langgraph.graph import END, StateGraph

from comun import CORPUS, DATOS, TESIS, chat, manifiesto, particion, texto_adr
```

- `TypedDict`: define el tipo del estado (qué claves tiene y de qué tipo). LangGraph lo usa para saber qué claves existen.
- `networkx`: el grafo NetworkX de la planificación.
- `StateGraph, END`: las piezas de LangGraph explicadas arriba.
- De `comun`: rutas, el cliente `chat` de Qwen y las funciones de corpus. `TESIS` se importa pero **no se usa** (se puede borrar).

```python
sys.path.insert(0, str(CORPUS / "anotacion"))
from csv_a_grafo import COLUMNAS, adr_id as nodo_adr, construir  # noqa: E402
from conformidad import VOCABULARIO, comprobar  # noqa: E402  (csv_a_grafo ya lo pone en sys.path)
```

- `csv_a_grafo.py` y `conformidad.py` viven en otras carpetas, fuera de un paquete instalable. `sys.path.insert(0, carpeta)` añade la carpeta a la lista de sitios donde Python busca módulos, para poder importarlos.
- A su vez, `csv_a_grafo.py`, al importarse, añade la carpeta del esquema v1.1 a `sys.path`; por eso la línea siguiente puede importar `conformidad` sin añadir nada más.
- `# noqa: E402` le dice al linter que no se queje de un import que no está arriba del todo (aquí es inevitable, porque primero hay que tocar `sys.path`).
- Qué se importa y por qué:
  - `COLUMNAS`: las 15 columnas de la hoja de anotación.
  - `adr_id as nodo_adr`: la función que da el **id de nodo** de un ADR (`adr_<repo>__<fichero>`). Se renombra para no confundirla con `comun.adr_id` (`repo|fichero`).
  - `construir`: la función que convierte filas de la hoja en grafo. **Es la misma que genera el gold.**
  - `VOCABULARIO`: los individuos de los vocabularios cerrados del esquema (estados normalizados y tipos de relación).
  - `comprobar`: el validador de A6.

## 2. Vocabularios derivados del esquema (líneas 39–43)

```python
MAX_INTENTOS = 3
ESTADOS = sorted(s.removeprefix("status_") for s in VOCABULARIO["NormalizedStatus"])
TIPOS_REL = sorted(s.removeprefix("reltype_") for s in VOCABULARIO["RelationType"])
POLARIDAD = ["positiva", "negativa", "neutra"]
ROLES = ["decision-maker", "consulted", "informed"]
```

- `MAX_INTENTOS = 3`: el máximo de la planificación.
- `ESTADOS`: se leen **del esquema congelado**, no se escriben a mano. En el esquema son individuos `status_accepted`, `status_superseded`…; se quita el prefijo → `["accepted", "amended", "deprecated", "proposed", "rejected", "superseded"]`. Si el esquema cambiara, las funciones cambiarían solas. Esto materializa la frase de la planificación: «las funciones de escritura salen del esquema».
- `TIPOS_REL`: igual → `["amends", "relatesTo", "supersedes"]`.
- `sorted`: orden fijo, para que el prompt sea idéntico en todas las corridas (un conjunto de Python no garantiza orden).
- `POLARIDAD` y `ROLES`: son los valores que acepta `csv_a_grafo.py` (allí se traducen a las subclases `Pro/Con/NeutralArgumentStatement`, `Positive/Negative/NeutralConsequenceStatement`, `DecisionMaker/Consulted/InformedParticipantStatement`). Se escriben aquí porque en el esquema son subclases, no individuos de un vocabulario.

## 3. Construcción de las funciones para Qwen (líneas 45–54)

**Qué es function calling.** Al modelo se le pasa una lista de funciones con nombre, descripción y parámetros en formato JSON Schema. En vez de responder con texto libre, el modelo devuelve una lista de **llamadas** (`tool_calls`), cada una con el nombre de la función y sus argumentos. Nosotros **no ejecutamos** nada: tratamos esas llamadas como datos estructurados. La ventaja frente a «devuélveme un JSON» es que la estructura la impone el esquema de la función y los modelos están entrenados para respetarlo (por eso la planificación cita el Berkeley Function Calling Leaderboard).

```python
EV = {"type": "string", "description": "Fragmento COPIADO LITERALMENTE del ADR (sin parafrasear) que respalda este elemento."}
```

El parámetro `evidencia`, común a **todas** las funciones. Es el `evidenceText` del esquema: toda afirmación del grafo guarda el fragmento literal que la respalda, y eso es lo que permite medir la **tasa de nodos no fundamentados** (bloque 1). Las mayúsculas en «COPIADO LITERALMENTE» son deliberadas: el error más común del modelo es parafrasear.

```python
def _f(funcion, descripcion, **props):
    req = ["evidencia"] + list(props)
    return {"type": "function", "function": {"name": funcion, "description": descripcion, "parameters": {
        "type": "object", "properties": {"evidencia": EV, **props}, "required": req}}}
```

Fábrica de definiciones de función en el formato que espera Ollama (el mismo de OpenAI):
- `funcion`: el nombre que verá el modelo (`"alternativa"`).
- `descripcion`: cuándo usarla. **Es el prompt real** de cada elemento; aquí están las reglas de la guía de anotación.
- `**props`: los parámetros extra (`nombre=...`, `polaridad=...`).
- `required`: todos los parámetros son obligatorios. Si el modelo pudiera omitir la evidencia, perderíamos la trazabilidad.
- **Nota histórica:** el primer parámetro se llamaba `nombre` y chocaba con el parámetro `nombre` de `alternativa`; Python lanzaba «got multiple values». Por eso ahora se llama `funcion`.

```python
S = lambda d, enum=None: {"type": "string", "description": d, **({"enum": enum} if enum else {})}  # noqa: E731
```

Atajo para declarar un parámetro de texto. Si se pasa `enum`, el parámetro solo admite esos valores. Es lo que **cierra el vocabulario**: el modelo no puede inventar un estado `"approved"`, tiene que elegir entre los seis del esquema. (Ollama no siempre lo garantiza al 100 %; si llegara un valor fuera de la lista, `construir()` lo rechaza y el validador lo convierte en error de reintento.)

## 4. Las funciones del nodo 1, extraer (líneas 56–75)

```python
HERRAMIENTAS_EXTRAER = [
    _f("contexto", "Situacion o antecedentes que motivan la decision (seccion Context). Una llamada con el primer parrafo."),
    ...
]
```

Una función por elemento MADR del esquema v1.1 (CQ9–17). Cada descripción traduce una **regla de la guía de anotación** para que el modelo y el anotador humano trabajen con el mismo criterio. Si no, el bloque 1 mediría diferencias de criterio y no errores de extracción.

| Función | Elemento en la hoja | Clase del esquema | Regla de la guía que recoge |
|---|---|---|---|
| `contexto` | `contexto` | `ContextStatement` | (l) solo el primer párrafo |
| `problema` | `problema` | `ProblemStatement` | — |
| `criterio` | `driver` | `DecisionDriverStatement` | (l) no deducir drivers sin encabezado |
| `alternativa(nombre)` | `alternativa` | `Alternative` | (b) sin lista de opciones no hay alternativas; la evidencia debe nombrar la opción |
| `argumento(alternativa, polaridad)` | `argumento` | `Pro/Con/NeutralArgumentStatement` | (d)(n) ligado a una alternativa concreta y solo si está escrito |
| `consecuencia(polaridad)` | `consecuencia` | `Positive/Negative/NeutralConsequenceStatement` | (o) riesgos = negativas |
| `justificacion` | `justificacion` | `JustificationStatement` | (c)(j)(k) «because», «selected as», «we tested» |
| `confirmacion` | `confirmacion` | `ConfirmationStatement` | (k) verificación continua; casi nunca existe |
| `participante(nombre, rol)` | `participante` | `DecisionMaker/…ParticipantStatement` | (t) Contributors no cuenta |
| `info_adicional` | `info_adicional` | `AdditionalInformationStatement` | (f)(i)(r) «revisit if», sub-decisiones |

Detalles que salieron de las pruebas del 09-10:
- La descripción de `argumento` dice «solo si el ADR lo escribe explícitamente… una lista de opciones sin pros ni contras no tiene argumentos». Se añadió porque Qwen **inventaba** pros y contras para el ADR 0011, que solo lista opciones.
- La de `alternativa` dice «la evidencia debe ser el texto que nombra ESA opción». Qwen citaba la frase de la opción elegida como evidencia de MongoDB.

## 5. Las funciones del nodo 2, decidir (líneas 78–92)

```python
def herramientas_decidir(otros):
    return [
        _f("decision", ...,
           resumen=S("Resumen de la decision en una frase."),
           alternativa_elegida=S("Nombre exacto de la alternativa elegida si hay lista de alternativas; cadena vacia si no.")),
```

- Es una **función** (no una lista fija como `HERRAMIENTAS_EXTRAER`) porque depende de `otros`: la lista de ficheros del mismo repo, distinta para cada ADR.
- `decision`: exactamente una por ADR (regla del esquema: un ADR tiene una decisión). `alternativa_elegida` se usa para marcar `selectsAlternative` (CQ2).

```python
        _f("estado", ...,
           literal=S("Literal tal como aparece, p. ej. 'Accepted', 'Superseded by ADR-0011'."),
           normalizado=S("Estado normalizado.", ESTADOS)),
```

CQ3: se guardan **las dos cosas**, el literal (fiel al documento) y el valor normalizado (comparable entre repos). El `enum` restringe el normalizado a los seis estados del esquema.

```python
        _f("relacion", ...,
           tipo=S("Tipo de relacion.", TIPOS_REL),
           sentido=S("'este_es_origen' si ESTE ADR reemplaza/enmienda/relaciona al otro; 'este_es_destino' si el otro reemplaza/enmienda a este.",
                     ["este_es_origen", "este_es_destino"]),
           otro_adr=S("Nombre de fichero del otro ADR, elegido de la lista dada.", otros or None)),
    ]
```

CQ5–6 y bloque 3. Tres decisiones de diseño:
1. **`sentido` en vez de pedir «origen» y «destino»:** «Superseded by ADR-0011» (el otro reemplaza a este) y «Supersedes ADR-0006» (este reemplaza al otro) usan el mismo tipo `supersedes` con direcciones opuestas. Preguntar «¿este ADR es el que reemplaza o el reemplazado?» es más fácil para un modelo pequeño que rellenar dos ficheros en el orden correcto.
2. **`otro_adr` con `enum` = ficheros del repo:** el texto dice «ADR-0011», pero el grafo necesita el **fichero** `0011-contentful.md`. Dándole la lista, el modelo elige entre ficheros que existen en vez de inventar un nombre. `otros or None`: si el repo no tiene otros ADR, no se pone `enum` (un `enum` vacío sería inválido).
3. **Solo relaciones DECLARADAS:** la descripción lo dice en mayúsculas. Es la distinción de la observación O1 entre **reemplazo documentado** (con evidencia) y contradicción factual (que no se extrae: se detecta después como conflicto en la CQ8).

## 6. Mensaje de sistema (líneas 95–97)

```python
SISTEMA = ("Eres un extractor de informacion de Architecture Decision Records (ADR). Registras cada elemento "
           "llamando a la funcion correspondiente. Toda evidencia es una copia literal de un fragmento del ADR. "
           "Si un elemento no aparece en el ADR, no llamas a su funcion.")
```

Instrucción general de los dos nodos con LLM. Tres ideas: el rol, que **todo** se registra con funciones (no con texto), y la regla anti-alucinación (si no aparece, no se llama). Va en español, como el resto del prompt; los ADR están en inglés. Es una decisión a declarar y, si quisieras, a probar en desarrollo con un prompt en inglés.

## 7. El estado (líneas 100–111)

```python
class Estado(TypedDict, total=False):
    repo: str                    # entrada: repositorio
    archivo: str                 # entrada: nombre del fichero del ADR
    texto: str                   # entrada: contenido del ADR
    otros: list[str]             # entrada: otros ficheros del mismo repo
    llamadas_extraer: list[dict] # salida del nodo 1
    llamadas_decidir: list[dict] # salida del nodo 2
    filas: list[dict]            # salida del nodo 3 (formato hoja de anotación)
    grafo: dict                  # salida del nodo 3 (grafo en JSON node-link)
    errores: list[str]           # salida de los nodos 3 y 4; alimenta el reintento
    intentos: int                # contador del nodo 1
    historial: list[dict]        # una entrada por intento (nodo 4)
```

- `total=False`: ninguna clave es obligatoria desde el principio. El estado inicial solo trae las de entrada y las demás aparecen a medida que los nodos las devuelven.
- **¿Por qué `grafo` es un `dict` y no un objeto NetworkX?** Se guarda en formato node-link (JSON). Así el estado es serializable, que es requisito para el checkpointing de LangGraph, y se puede escribir tal cual a disco.

## 8. Leer las llamadas del modelo (líneas 114–116)

```python
def _llamadas(msg):
    return [{**(c["function"].get("arguments") or {}), "funcion": c["function"]["name"]}
            for c in msg.get("tool_calls") or []]
```

Ollama devuelve algo como:
```json
{"tool_calls": [{"function": {"name": "alternativa", "arguments": {"nombre": "Contentful", "evidencia": "Contentful"}}}]}
```
Esta función lo aplana a `{"nombre": "Contentful", "evidencia": "Contentful", "funcion": "alternativa"}`.
- `msg.get("tool_calls") or []`: si el modelo no llamó a nada, o si hubo error HTTP, devuelve lista vacía.
- `.get("arguments") or {}`: protege contra llamadas sin argumentos.
- **El orden importa:** `"funcion"` va **después** de `**argumentos`. Así, si algún argumento se llamara `funcion`, el nombre real de la función prevalece. Este era el **bug del 09-10**: antes la clave era `"nombre"` y estaba **antes**, de modo que el argumento `nombre` de `alternativa` («Static YAML») sobrescribía el nombre de la función. Las alternativas desaparecían sin dar error.

## 9. Nodo 1: extraer (líneas 119–132)

```python
def nodo_extraer(st: Estado) -> Estado:
    pedido = f"ADR `{st['archivo']}` del repositorio {st['repo']}:\n\n{st['texto']}"
```

Primer intento: el pedido es solo el ADR completo con su nombre. Se pasa el **ADR completo**, no fragmentos: el pipeline construye el grafo de un documento a la vez, y el texto más largo (5 399 tokens) cabe en `num_ctx`.

```python
    if st.get("errores"):
        previas = json.dumps(st["llamadas_extraer"], ensure_ascii=False)
        pedido += ("\n\nEn el intento anterior hiciste estas llamadas:\n" + previas +
                   "\n\nEl validador encontro estos errores:\n- " + "\n- ".join(st["errores"]) +
                   "\n\nVuelve a hacer TODAS las llamadas: repite sin cambios las correctas y corrige las "
                   "erroneas: ...")
```

**Reintento (patrón Reflexion).** Si el estado trae errores, es porque `validar` falló y el flujo volvió aquí. Se añade al pedido:
1. **Las llamadas anteriores** del modelo. Sin ellas, el modelo empieza de cero y puede «arreglar» un error borrando lo que estaba bien: el 09-10 un reintento pasó la validación **eliminando las 5 alternativas**. El validador premia la omisión, porque lo que no existe no puede estar mal.
2. **Los errores** en lenguaje natural, uno por línea.
3. **Instrucciones**: repetir las correctas, corregir las erróneas; si una evidencia no aparece literal, buscar el fragmento exacto o no registrar el elemento.

**Limitación medida:** en desarrollo, este reintento rescató **0 de 17** ADR con errores. Con temperatura 0, el modelo repite las mismas paráfrasis. Es el siguiente punto de trabajo (A6).

```python
    msg = chat([{"role": "system", "content": SISTEMA}, {"role": "user", "content": pedido}],
               herramientas=HERRAMIENTAS_EXTRAER, registro="pipeline",
               etiqueta=f"extraer|{st['repo']}|{st['archivo']}|{st.get('intentos', 0) + 1}")
    return {"llamadas_extraer": _llamadas(msg), "intentos": st.get("intentos", 0) + 1}
```

- Llama a Qwen con las funciones del nodo 1; el costo queda en `registros/pipeline.jsonl` con una etiqueta que dice nodo, ADR y número de intento.
- Devuelve las llamadas nuevas (que **reemplazan** a las del intento anterior) y aumenta `intentos`. El contador vive en este nodo porque cada paso por aquí es, por definición, un intento.

## 10. Nodo 2: decidir (líneas 135–144)

```python
def nodo_decidir(st: Estado) -> Estado:
    alts = [c.get("nombre", "") for c in st["llamadas_extraer"] if c["funcion"] == "alternativa"]
    pedido = (f"ADR `{st['archivo']}` del repositorio {st['repo']}:\n\n{st['texto']}\n\n"
              f"Alternativas ya registradas: {alts or 'ninguna'}.\n"
              f"Otros ADR del mismo repositorio: {st['otros'] or 'ninguno'}.\n"
              "Registra la decision, el estado y las relaciones declaradas con otros ADR.")
```

- Le pasa al modelo **las alternativas que encontró el nodo 1**, para que `alternativa_elegida` use exactamente uno de esos nombres. Es la dependencia entre nodos que justifica que sean secuenciales: no se puede elegir entre alternativas que todavía no existen.
- Le pasa los otros ficheros del repo (también restringidos por `enum`).
- Es una conversación **nueva** (no continúa la del nodo 1): cada nodo tiene un trabajo acotado y un prompt corto, lo que ayuda a un modelo de 4B.

```python
    msg = chat([...], herramientas=herramientas_decidir(st["otros"]), registro="pipeline",
               etiqueta=f"decidir|{st['repo']}|{st['archivo']}|{st['intentos']}")
    return {"llamadas_decidir": _llamadas(msg)}
```

Solo devuelve sus llamadas. No toca `intentos`.

## 11. Nodo 3: escribir (líneas 147–201)

**Idea clave.** El modelo no escribe en el grafo. Sus llamadas se convierten en **filas con el mismo formato que la hoja de anotación** (`anotaciones.csv`) y se pasan a `construir()`, la función que genera el gold. Consecuencias:
- Mismos ids de nodo, mismas clases, mismas aristas, mismas comprobaciones: el bloque 1 compara **contenido**, no detalles de implementación.
- Cualquier regla de construcción (por ejemplo, «el documento que declara la relación debe ser uno de sus extremos») se aplica igual al humano y al modelo.

### 11.1 Fila vacía (líneas 147–150)

```python
def _fila(st, elemento, ev, **kw):
    f = dict.fromkeys(COLUMNAS, "")
    f.update(repo=st["repo"], archivo=st["archivo"], elemento=elemento, evidencia=(ev or "").strip(), **kw)
    return f
```

- `dict.fromkeys(COLUMNAS, "")`: una fila con las 15 columnas vacías, igual que una celda vacía de la hoja.
- Rellena repo, fichero, tipo de elemento y evidencia (sin espacios sobrantes en los extremos), más las columnas específicas que lleguen en `**kw` (`valor`, `polaridad`, `alternativa`…).

### 11.2 Emparejar nombres de alternativa (líneas 153–160)

```python
def _emparejar(ref: str, nombres: list[str]) -> str:
    r = ref.strip().lower()
    for cond in (lambda x: x == r, lambda x: r and (r in x or x in r)):
        hits = [n for n in nombres if n.strip() and cond(n.strip().lower())]
        if len(hits) == 1:
            return hits[0]
    return ref
```

**Problema:** el modelo registra la alternativa «Contentful CMS» y luego, en un argumento o en `alternativa_elegida`, escribe «Contentful». `construir()` empareja por texto exacto, así que el argumento quedaría huérfano y daría error.

**Solución determinista, en dos pasadas:**
1. Igualdad sin distinguir mayúsculas.
2. Si no hay, que uno contenga al otro («contentful» ⊂ «contentful cms»).

- En cada pasada, solo se acepta si hay **exactamente un** candidato. Si «Option 2» fuera parte de «Option 2A» y de «Option 2B», sería ambiguo y no se adivina.
- Si no hay coincidencia única, devuelve la referencia original; `construir()` la rechazará y el error volverá al modelo.
- `r and (...)`: si la referencia está vacía, no empareja con nada (la cadena vacía está contenida en cualquier texto).
- **¿Es legítimo?** Sí: es normalización de nombres en la escritura, no extracción. No inventa información; solo resuelve a qué alternativa **ya extraída** se refería el modelo. Conviene declararlo en la descripción del nodo 3.

### 11.3 De llamadas a filas (líneas 163–194)

```python
def a_filas(st: Estado) -> list[dict]:
    filas = []
    elegida = next((c.get("alternativa_elegida", "") for c in st["llamadas_decidir"] if c["funcion"] == "decision"), "")
    nombres_alt = [c.get("nombre", "") for c in st["llamadas_extraer"] if c["funcion"] == "alternativa"]
    elegida = _emparejar(elegida, nombres_alt) if elegida else ""
```

- `next(generador, "")`: la `alternativa_elegida` de la **primera** llamada a `decision`, o vacío si no hubo. Si el modelo llamara dos veces a `decision`, se usa la primera para esto (y `construir` generaría dos nodos de decisión, que el validador marca como error).
- `nombres_alt`: los nombres de todas las alternativas extraídas.
- La elegida se normaliza contra esos nombres.

```python
    for c in st["llamadas_extraer"]:
        n, ev = c["funcion"], c.get("evidencia", "")
        if n == "alternativa":
            filas.append(_fila(st, "alternativa", ev, valor=c.get("nombre", ""),
                               elegida="1" if elegida and c.get("nombre", "").strip() == elegida.strip() else "0"))
```

Cada alternativa es una fila: `valor` = nombre (pasará a `alternativeName`), `elegida` = `"1"` si coincide con la elegida, si no `"0"`. Es el mismo formato que usa el anotador humano: `construir()` pone la arista `selectsAlternative` cuando ve `"1"`.

```python
        elif n == "argumento":
            filas.append(_fila(st, "argumento", ev, polaridad=c.get("polaridad", ""),
                               alternativa=_emparejar(c.get("alternativa", ""), nombres_alt)))
        elif n == "consecuencia":
            filas.append(_fila(st, "consecuencia", ev, polaridad=c.get("polaridad", "")))
        elif n == "participante":
            filas.append(_fila(st, "participante", ev, valor=c.get("nombre", ""), rol=c.get("rol", "")))
        elif n == "criterio":
            filas.append(_fila(st, "driver", ev))
        elif n in ("contexto", "problema", "justificacion", "confirmacion", "info_adicional"):
            filas.append(_fila(st, n, ev))
```

- `argumento`: polaridad y alternativa (normalizada). `construir()` traduce la polaridad a la subclase y crea `argumentAlternative`.
- `criterio` se escribe como `driver`, que es el nombre del elemento en la hoja. La función se llama «criterio» porque es más clara para el modelo en el prompt en español.
- Los elementos simples solo llevan evidencia; `construir()` copia la evidencia también como descripción (`contextDescription`, etc.), igual que en el gold cuando el anotador no rellena `valor`.
- Si el modelo llamara a una función inexistente, ninguna rama coincide y la llamada se **ignora**. Con function calling no debería ocurrir.

```python
    for c in st["llamadas_decidir"]:
        n, ev = c["funcion"], c.get("evidencia", "")
        if n == "decision":
            filas.append(_fila(st, "decision", ev, valor=c.get("resumen", "")))
        elif n == "estado":
            filas.append(_fila(st, "estado", ev, valor=c.get("literal", ""), estado_normalizado=c.get("normalizado", "")))
```

- `decision`: `valor` = resumen en una frase (pasa a `decisionSummary`) y `evidencia` = fragmento literal.
- `estado`: `valor` = literal (`declaredStatusLiteral`) y `estado_normalizado` → arista `hasNormalizedStatus`.

```python
        elif n == "relacion":
            este, otro = st["archivo"], c.get("otro_adr", "")
            nuevo, afect = (este, otro) if c.get("sentido") == "este_es_origen" else (otro, este)
            filas.append(_fila(st, "relacion", ev, tipo_relacion=c.get("tipo", ""), adr_nuevo=nuevo, adr_afectado=afect))
    return filas
```

Traduce `sentido` a las columnas de la hoja:
- `este_es_origen` («Supersedes 0006»): este ADR es el **nuevo**, el otro el **afectado**.
- `este_es_destino` («Superseded by 0011»): el otro es el nuevo, este el afectado.

`construir()` crea entonces un `RelationshipStatement` con `sourceADR` = nuevo, `targetADR` = afectado y `declaringADR` = este ADR. Esto conserva **quién declara** la relación (CQ5: ¿lo declaran los dos documentos o solo uno?).

### 11.4 El nodo (líneas 197–201)

```python
def nodo_escribir(st: Estado) -> Estado:
    filas = a_filas(st)
    G, problemas, _ = construir(filas)
    errores = [f"{filas[i]['elemento']}: {p}" for i, p in problemas]
    return {"filas": filas, "grafo": nx.node_link_data(G, edges="links"), "errores": errores}
```

- `construir(filas)` devuelve: el grafo (esquema v1.1 + individuos de este ADR), los **problemas de fila** (`[(índice, mensaje)]`) y el conjunto de ADR empezados (no se usa aquí, por eso `_`).
- Qué detecta `construir()` como problema de fila: elemento desconocido, fila sin evidencia, estado o polaridad fuera del vocabulario, relación con un ADR que no existe en el repo o cuyo declarante no es uno de sus extremos, argumento cuya alternativa no coincide con ninguna.
- Qué añade sin que el modelo lo extraiga: el título del ADR (del propio fichero) y `registrationDate` (de `fechas_git.csv`). Las fechas **no las extrae el LLM**: es una decisión de diseño previa, porque la fecha es metadato de git, no contenido del documento.
- Los problemas se convierten en mensajes «elemento: problema» que el validador acumula y, si hay reintento, verá el modelo.
- `node_link_data`: convierte el grafo a un diccionario JSON (lista de nodos y lista de aristas `links`).

## 12. Nodo 4: validar (líneas 204–229)

### 12.1 Soporte de las alternativas (líneas 204–212)

```python
def _soporte(G):
    out = []
    for n, d in G.nodes(data=True):
        if d.get("type") == "Alternative":
            nombre, ev = (d.get("alternativeName") or "").lower(), (d.get("evidenceText") or "").lower()
            if nombre and nombre not in ev:
                out.append(f"alternativa '{d.get('alternativeName')}': la evidencia no la nombra; cita el texto que describe esa opcion")
    return out
```

**Por qué existe:** la retroalimentación del 05-10 pide «comprobar que el fragmento literal respalda la relación extraída, especialmente al distinguir alternativas consideradas de decisiones adoptadas». `conformidad.py` solo comprueba que la evidencia **existe** en el ADR. En la primera prueba, Qwen dio para la alternativa MongoDB la evidencia «Chosen option: PostgreSQL…», que existe en el ADR pero no respalda a MongoDB.

**Regla:** el nombre de la alternativa tiene que aparecer dentro de su evidencia (sin distinguir mayúsculas).

**Limitación:** si el modelo abrevia o reformula el nombre («switch to factory_bot» frente a «Use factory_bot»), la regla da un **falso positivo**. En desarrollo quedó como error final en 10 alternativas; hay que revisarlas antes de decidir si se relaja (por ejemplo, exigir solo que comparta palabras clave con la evidencia).

### 12.2 El nodo (líneas 215–229)

```python
def nodo_validar(st: Estado) -> Estado:
    G = nx.node_link_graph(st["grafo"], directed=True, multigraph=True, edges="links")
    hallazgos, met = comprobar(G, CORPUS / "adr")
```

- Reconstruye el grafo NetworkX desde el JSON del estado (dirigido y multigrafo, como el esquema).
- `comprobar()` es el validador de A6, con los tres mecanismos de la planificación:
  1. **esquema:** tipos, dominios y rangos, vocabularios cerrados, propiedades funcionales (por ejemplo, dos `selectsAlternative`);
  2. **integridad referencial:** cada relación con exactamente un origen, un destino, un declarante y un tipo; cada `Statement` colgando de un ADR;
  3. **fundamentación:** el `evidenceText` aparece en el ADR de origen, tras normalizar espacios y mayúsculas.
- Devuelve `hallazgos` (objetos con `severidad`, `tipo`, `nodo`, `mensaje`) y `met` (con `grounded_ok` y `grounded_total`).

```python
    propio = nodo_adr(st["repo"], st["archivo"])
    errores = list(st.get("errores", []))
    errores += [f"{h.tipo}: {h.mensaje}" for h in hallazgos
                if h.severidad == "error" and (h.nodo.startswith(propio) or h.nodo.startswith("rd_"))]
```

- `errores` empieza con los problemas de fila que dejó el nodo 3 (`list(...)` hace una copia para no modificar la lista del estado).
- Se añaden solo los hallazgos de severidad **error** (los avisos, como «sin tema», no bloquean) y solo los que afectan a **este** ADR: nodos cuyo id empieza por el id del ADR (`adr_x__0011…_alt1`, `_decision1`…) o relaciones (`rd_…`).
- **Por qué filtrar:** si el ADR declara una relación con otro ADR, `construir()` crea un nodo mínimo para ese otro ADR (sin decisión). El validador se quejaría de que a **ese** ADR le falta la decisión, pero no es culpa de este, y el modelo no puede arreglarlo.

```python
    if not any(f["elemento"] == "decision" for f in st["filas"]):
        errores.append("falta la decision: llama exactamente una vez a la funcion decision")
    errores += _soporte(G)
```

- Comprobación explícita de la regla «todo ADR tiene una decisión», con un mensaje que le dice al modelo **qué hacer**. También cubre el caso de error HTTP, en el que no hay llamadas.
- Se añaden los errores de soporte de las alternativas.

```python
    hist = st.get("historial", []) + [{"intento": st["intentos"], "errores": errores,
                                       "n_elementos": len(st["filas"]),
                                       "llamadas": st["llamadas_extraer"] + st["llamadas_decidir"],
                                       "fundamentadas": f"{met['grounded_ok']}/{met['grounded_total']}"}]
    return {"errores": errores, "historial": hist}
```

**El historial guarda cada intento**, no solo el último:
- `n_elementos`: cuántos elementos produjo el intento. Si baja entre intentos, el reintento está «arreglando» borrando, y eso se puede medir y reportar.
- `llamadas`: lo que dijo el modelo, para depurar después.
- `fundamentadas`: por ejemplo `14/19`.

Devuelve `errores`, que **reemplaza** la lista anterior en el estado. Eso es lo que verá `_siguiente` y, si hay reintento, `nodo_extraer`.

## 13. Decisión de continuar (líneas 232–233)

```python
def _siguiente(st: Estado) -> str:
    return "extraer" if st["errores"] and st["intentos"] < MAX_INTENTOS else END
```

- Hay errores **y** van menos de 3 intentos → vuelve a `extraer`.
- Si no → termina.
- Si al tercer intento sigue habiendo errores, **termina igualmente** y guarda el grafo con sus errores. No se descarta nada: los nodos no fundamentados que queden cuentan en la tasa del bloque 1. Ocultarlos inflaría la métrica.

## 14. Montaje del grafo LangGraph (líneas 236–247)

```python
def construir_pipeline():
    g = StateGraph(Estado)
    g.add_node("extraer", nodo_extraer)
    g.add_node("decidir", nodo_decidir)
    g.add_node("escribir", nodo_escribir)
    g.add_node("validar", nodo_validar)
    g.set_entry_point("extraer")
    g.add_edge("extraer", "decidir")
    g.add_edge("decidir", "escribir")
    g.add_edge("escribir", "validar")
    g.add_conditional_edges("validar", _siguiente, {"extraer": "extraer", END: END})
    return g.compile()
```

Es la traducción directa del diagrama de la planificación:
- `StateGraph(Estado)`: grafo cuyo estado tiene la forma de `Estado`.
- Cuatro nodos con nombre.
- Entrada en `extraer`.
- Tres aristas fijas: la secuencia es siempre la misma (pipeline **determinista**, no orquestador; la planificación lo justifica porque un orquestador LLM añadiría variabilidad sin aportar enrutamiento útil).
- Una arista condicional desde `validar`: `_siguiente` devuelve `"extraer"` o `END`, y el diccionario traduce ese valor al nodo destino.
- **Nota:** el reintento vuelve a `extraer` y, por tanto, también repite `decidir`. Es coherente (la alternativa elegida depende de las alternativas extraídas), pero cuesta una llamada más por intento.

## 15. Programa principal (líneas 250–282)

```python
    ap.add_argument("--particion", choices=["desarrollo", "prueba"], default="desarrollo")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--adr", help="repo|archivo.md para correr uno solo")
```

- Por defecto corre **desarrollo**. Hay que pedir `prueba` explícitamente: es una protección para no ejecutar sobre prueba por descuido mientras se ajustan prompts.
- `--limite N`: solo los N primeros (pruebas rápidas).
- `--adr`: uno concreto, con la clave `repo|fichero`.

```python
    part = particion()
    objetivos = [r for r in manifiesto() if part[f"{r['repo']}|{r['filename']}"] == args.particion]
    if args.adr:
        objetivos = [r for r in manifiesto() if f"{r['repo']}|{r['filename']}" == args.adr]
    objetivos = objetivos[:args.limite] if args.limite else objetivos
```

Selecciona los ADR del manifiesto que caen en la partición pedida. Con `--adr` se ignora la partición. El orden es el del manifiesto, así que dos corridas procesan los ADR en el mismo orden.

```python
    app = construir_pipeline()
    sal = DATOS / "grafos" / args.particion
    sal.mkdir(parents=True, exist_ok=True)
    (DATOS / "corridas").mkdir(exist_ok=True)
```

Compila el pipeline **una vez** y crea las carpetas de salida.

```python
    for r in objetivos:
        otros = sorted(x["filename"] for x in manifiesto() if x["repo"] == r["repo"] and x["filename"] != r["filename"])
```

Los otros ADR del mismo repo, para el `enum` de `otro_adr`. Se ordenan para que el prompt sea idéntico entre corridas. Solo del **corpus** (no del repo real en GitHub): una relación con un ADR fuera del corpus no puede enlazarse. La guía, regla (p), lo trata como info adicional.

```python
        t0 = time.perf_counter()
        fin = app.invoke({"repo": r["repo"], "archivo": r["filename"], "texto": texto_adr(r["repo"], r["filename"]),
                          "otros": otros, "intentos": 0, "errores": []}, {"recursion_limit": 25})
        seg = time.perf_counter() - t0
```

- `invoke` ejecuta el grafo desde el estado inicial hasta `END` y devuelve el estado final.
- `errores: []` al inicio: así el primer `extraer` no entra en modo reintento.
- `recursion_limit: 25`: LangGraph corta si se ejecutan más de 25 pasos, como red de seguridad contra bucles. El máximo real es 3 intentos × 4 nodos = 12 pasos.
- `seg`: tiempo total del ADR (todas las llamadas y validaciones).

```python
        nombre = nodo_adr(r["repo"], r["filename"])
        (sal / f"{nombre}.json").write_text(json.dumps({"filas": fin["filas"], "grafo": fin["grafo"],
                                                         "historial": fin["historial"]}, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
```

Un JSON por ADR en `datos/grafos/<partición>/` con: las filas (legibles, mismo formato que la hoja), el grafo (lo que lee `evaluar_b1.py`) y el historial de intentos. Se **sobrescribe** si el ADR se vuelve a correr.

```python
        res = {"adr": f"{r['repo']}|{r['filename']}", "intentos": fin["intentos"], "segundos": round(seg, 1),
               "errores_finales": fin["errores"], "elementos": len(fin["filas"])}
        with open(DATOS / "corridas" / f"{args.particion}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(res, ensure_ascii=False) + "\n")
```

Una línea de resumen por ADR en `datos/corridas/<partición>.jsonl`, en modo **append**. Si vuelves a correr un ADR, quedan dos líneas suyas; para una corrida limpia, borra antes el fichero (es lo que se hizo el 09-10 con `rm -f`).

```python
        print(f"{'OK ' if not fin['errores'] else 'ERR'} {r['filename']:<55} intentos={fin['intentos']} "
              f"{len(fin['filas'])} elementos {seg:.0f}s" + (f"  {fin['errores'][:2]}" if fin["errores"] else ""))
```

Progreso en pantalla: OK/ERR, fichero, intentos, elementos, segundos y los dos primeros errores si los hay.

## 16. Resumen de lo medido el 09-10 (desarrollo)

| Qué | Valor |
|---|---|
| ADR válidos al final | 7/24, todos en el primer intento |
| Rescatados por el reintento | 0/17 |
| Tiempo medio por ADR | ~49 s |
| Bloque 1 (9 ADR anotados) | decisión F1 0.89 · alternativas F1 0.96 · elegida 5/5 · estado 7/7 · relaciones F1 0.80 · no fundamentados 12.1 % |

Pendientes de este archivo: rediseñar el reintento (por elemento fallido), asignación de tema (CQ4/CQ8), activar el checkpointing y quitar el import de `TESIS`.
