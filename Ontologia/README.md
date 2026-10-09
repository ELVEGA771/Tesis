# CQbyCQ sobre Python/NetworkX

Replica del metodo **CQbyCQ** (*Competency Question by Competency Question*)
descrito en:

> M. J. Saeedizade, E. Blomqvist. *Navigating Ontology Development with Large
> Language Models*. ESWC 2024.
> Prompts originales: https://github.com/LiUSemWeb/LLMs4OntologyDev-ESWC2024

con una sola diferencia deliberada: **el lenguaje destino no es Turtle/OWL sino
Python + NetworkX**. Todo lo demas (estructura del prompt en cuatro secciones,
memoria externa, descomposicion CQ a CQ, validacion y correccion, metrica de
evaluacion) sigue el metodo original.

Los prompts de `prompts/` son una traduccion del prompt original de CQbyCQ
(`CQbyCQ.md`) a esta convencion de grafo, manteniendo su nivel de detalle:
estrategias de taxonomia, reificacion con ejemplo trabajado, propiedades de
objeto y de datos, caracteristicas de propiedad, restricciones, y las cuatro
listas de errores recurrentes del pie de pagina.

---

## 1. Que hace exactamente

```
                 ┌──────────────────────────────────────────┐
                 │  historia de usuario + N competency Qs   │
                 └──────────────────────────────────────────┘
                                    │
        ┌───────────────────────────┴──────────────────────────┐
        │   para cada CQ_i  (i = 1 .. N)                       │
        │                                                      │
        │   memoria ──► PROMPT ──► LLM ──► script NetworkX     │
        │      ▲     header                     │     completo │
        │      │     helper                     ▼              │
        │      │     historia+CQs      1. sintaxis+lista blanca │
        │      │     CQ_i              2. convencion ontologica │
        │      │     footer            3. regresion vs anterior │
        │      │     grafo anterior         │         │         │
        │      │                        (falla)     (ok)        │
        │      │                            │         │         │
        │      │                  prompt de           │         │
        │      │                  correccion ─────────┘         │
        │      │                                      │         │
        │      └────── nueva version de la ontologia ◄┘         │
        └──────────────────────────────────────────────────────┘
                                    │
                                    ▼
                     ontologia final (nx.MultiDiGraph)
                                    │
                     ┌──────────────┴──────────────┐
                     ▼                             ▼
          criterios estructurales          CQ Verification
          (8 criterios binarios)        score = (1/n) Σ f(O,CQᵢ)
```

**La salida de cada etapa sustituye a la anterior.** El header original lo dice
sin ambiguedad: *"Your output at each stage is an independent turtle, so
rewrite/edit the previous RDF and produce the new one"*, y el pie de pagina
insiste en *"put all of the previous rdf classes, relations, restrictions and
add yours... dont remove previous code"*. El modelo reemite la ontologia
entera en cada etapa.

Por eso existe el **nivel 3 de validacion**: se compara la version nueva con la
anterior y, si el modelo ha perdido nodos o aristas por el camino, se le dice
exactamente cuales en el prompt de correccion. Es el fallo que el apendice del
paper documenta (*"LLMs often forgot to merge classes (or properties) from the
previous section, which resulted in incomplete modelling"*). Si tras agotar las
correcciones sigue perdiendo cosas, el pipeline las reinyecta y lo registra en
el `record.json` de esa CQ, para que la perdida nunca sea silenciosa.

## 2. Instalacion

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...                # PowerShell: $env:GEMINI_API_KEY="..."
```

## 3. Uso

```bash
# Generar la ontologia ADR
python -m cqbycq run --story inputs/historia_adr.json

# Generar y evaluar en una sola pasada
python -m cqbycq run --story inputs/historia_adr.json --evaluate

# Linea base de contraste: una sola interaccion, sin descomposicion
python -m cqbycq run --story inputs/historia_adr.json --technique zero-shot

# Evaluar / puntuar una corrida ya hecha
python -m cqbycq evaluate --run outputs/run_20260920_120000
python -m cqbycq criteria --run outputs/run_20260920_120000
python -m cqbycq score    --worksheet outputs/run_.../eval/cq_verification.csv

# Ver la convencion exacta que se le describe al modelo
python -m cqbycq api

# Modelos Gemini disponibles para tu clave
python -m cqbycq models

# Retomar una corrida que se corto a mitad (por cuota, por red, por Ctrl+C)
python -m cqbycq run --story inputs/historia_adr.json --resume outputs/run_20260921_180225

# Pruebas que no gastan API
python tests/test_offline.py
python tests/test_pipeline_mock.py
python tests/test_llm_retry.py
python tests/test_resume.py
```

### Formato de entrada

```json
{
  "id": "adr",
  "title": "Registros de decision arquitectonica (ADR)",
  "story": "texto de la historia...",
  "additional_statement": "requisito en prosa, opcional",
  "competency_questions": [
    { "id": "CQ1", "text": "..." }
  ]
}
```

`competency_questions` tambien acepta una lista plana de strings.

## 4. La convencion del grafo

Todo es un nodo con atributo `type` y toda arista lleva atributo `relation`.

### Nivel esquema

| `type` | Que es |
|---|---|
| `Class` | una clase (`pivot=True` si es de reificacion) |
| `ObjectProperty` | relacion entre clases |
| `DatatypeProperty` | atributo literal de una clase |
| `Restriction` | restriccion anonima (el nodo blanco de OWL) |
| `Datatype` | un `xsd:*` |

### Nivel instancia

Un individuo es un nodo cuyo `type` es el **nombre de una clase ya declarada**.
Asi tu ejemplo original queda valido tal cual:

```python
G.add_node("ADR", type="Class")                      # esquema
G.add_node("ADR-001", type="ADR", title="Usar SQL")  # instancia de ADR
G.add_edge("ADR-001", "Alt-01", relation="selectsAlternative")
```

### Relaciones

| `relation` | De | A |
|---|---|---|
| `subClassOf` | Class / Restriction | Class |
| `equivalentClass` | Class | Class / Restriction |
| `disjointWith` | Class | Class |
| `domain` | Property | Class |
| `range` | ObjectProperty / DatatypeProperty | Class / Datatype |
| `subPropertyOf` | Property | Property |
| `inverseOf` | ObjectProperty | ObjectProperty |
| `onProperty` | Restriction | Property |
| `someValuesFrom` / `allValuesFrom` / `hasValue` | Restriction | Class / Individual |
| `<nombre de ObjectProperty>` | Individual | Individual |

Caracteristicas de propiedad, como lista en el nodo: `Functional`,
`InverseFunctional`, `Transitive`, `Symmetric`, `Asymmetric`, `Reflexive`,
`Irreflexive`.

### Por que `MultiDiGraph` y no `DiGraph`

Un `DiGraph` solo admite **una** arista por par de nodos. Tus CQ2 y CQ3
necesitan que un mismo ADR tenga `considersAlternative` **y**
`selectsAlternative` hacia la misma alternativa, asi que `DiGraph` perderia una
de las dos silenciosamente. El validador acepta `nx.DiGraph()` en el codigo del
modelo y lo promociona a `MultiDiGraph`, pero el prompt pide `MultiDiGraph`
directamente.

## 5. Los tres niveles de validacion

El metodo original aplica un paso de *"syntax validation and correction"* sobre
el Turtle. Aqui son tres, y todos alimentan el mismo bucle de correccion
(`repair_attempts`, 2 por defecto):

1. **Sintaxis y lista blanca** (`dsl.py`). El codigo se parsea con `ast` y solo
   se admiten `import networkx as nx`, `G = nx.MultiDiGraph()`,
   `G.add_node(...)` y `G.add_edge(...)` con argumentos literales. Nada de
   bucles, funciones ni asignaciones. **El codigo no se ejecuta con `exec`**:
   se interpreta llamada a llamada.
2. **Convencion de la ontologia** (`ontograph.py:validate`). Implementa, una a
   una, las listas de errores del pie de pagina: nodos sin `type`, aristas sin
   `relation`, propiedades sin dominio o sin rango, `DatatypeProperty` con una
   clase como rango, clases pivote a las que no apuntan sus componentes,
   literales colgados de una clase, propiedades usadas como arista directa
   entre clases, ciclos en la jerarquia, restricciones incompletas.
3. **Regresion contra la etapa anterior** (`ontograph.py:diff`). Que nodos y
   aristas ha perdido el modelo respecto a la version previa.

## 6. Decisiones que vienen del apendice del paper

1. **`helper` y `footer` no son opcionales.** Sin `helper` el modelo se salta
   las reificaciones y coloca mal las propiedades; el `helper` arranca por las
   estrategias de taxonomia, que es lo que los LLM ignoran por defecto. El
   `footer` recoge los pitfalls que el apendice enumera.
2. **Sin few-shot y sin ejemplos de ODPs.** El apendice documenta que anadir
   Ontology Design Patterns al prompt *degrada* el resultado: el modelo se
   distrae y acaba modelando el patron de ejemplo en vez de la historia. Los
   unicos ejemplos del helper son de forma (usuario/recurso/tiempo,
   profesor/administrativo), nunca del dominio ADR.
3. **Hiperparametros.** El paper fija `temperature=0` y penalizaciones a 0 para
   CQbyCQ. Gemini **si** acepta `temperature`, asi que se envia 0 y el ajuste
   del paper se replica tal cual. `effort` se conserva como control de
   profundidad de razonamiento y se traduce al `thinking_level` de Gemini
   (`low`->LOW, `medium`->MEDIUM, `high`/`xhigh`/`max`->HIGH).

   **Cuidado con `max_tokens`:** en Gemini los tokens de razonamiento salen de
   ese mismo presupuesto. En una corrida real con `effort=high` y
   `max_tokens=16000`, la CQ2 gasto 15.359 tokens pensando y solo 637 en
   escribir: la respuesta se corto a mitad y la etapa se perdio. Por eso el
   valor por defecto es 32.000, y por eso existe `thinking_budget`, que a
   diferencia de `thinking_level` pone un **tope duro** a lo que el modelo
   puede gastar razonando. Si una etapa se trunca, mira el desglose que da el
   error: casi siempre el problema no es que la ontologia sea larga, sino que
   el razonamiento se comio el presupuesto.
   Como el apendice avisa de variabilidad entre corridas identicas, cada corrida
   guarda un `manifest.json` con el hash SHA-256 de cada prompt, para distinguir
   "cambie el prompt" de "el modelo no fue determinista".
4. **El prompt lleva siempre la historia completa y TODAS las CQs**, no solo la
   de la etapa. La CQ concreta acota la tarea; el contexto global evita nombres
   incoherentes con las CQs que aun no se han visto.

### Dos reconciliaciones respecto al fichero original

- **El chequeo de respondibilidad.** El original pide escribir, antes del
  codigo, si la CQ ya era respondible con la version anterior. Tu version
  prohibe todo texto fuera del bloque. Se resuelve pidiendolo como **comentario
  dentro del bloque de codigo**: se conserva el razonamiento y no hay prosa
  suelta.
- **El prefijo cacheado.** El header no lleva el texto de la CQ (va en la
  seccion `TAREA`), de modo que header + helper + historia son byte a byte
  identicos en las N etapas y se cachean. Ahorra bastante en una historia de
  7 CQs con correcciones. Con Gemini el cacheo es **implicito**: no hay que
  declarar nada, basta con que el prefijo se repita; los tokens servidos desde
  cache aparecen en `usage.cached_input_tokens` del `summary.json`.

## 7. Evaluacion

### 7.1 CQ Verification (la metrica del paper)

    score = (1/n) · Σ f(O, CQ_i),    f(O, CQ_i) ∈ {0,1}

`f` vale 1 si se puede escribir una consulta que recupere lo que pide la CQ con
los terminos que existen en la ontologia. En el paper la consulta es SPARQL;
aqui es una funcion sobre el `MultiDiGraph`. Se reportan las dos variantes:

- **estricta**: sin tolerar problemas menores.
- **sin menores**: un problema menor es un unico error de sintaxis, o **una
  sola** propiedad que falta y que impedia formular la consulta por un unico
  patron. Si falta una clase, o mas de una propiedad, no es menor.

> **En el paper la evaluacion es humana.** `python -m cqbycq evaluate` produce
> un borrador asistido por LLM **y** una hoja `cq_verification.csv` con columnas
> `humano_*` vacias. Para la tesis, el numero que debes reportar es el tuyo:
> rellena la hoja y usa `python -m cqbycq score --worksheet ...`, que ademas te
> da el **acuerdo LLM/humano**, un dato defendible por si mismo.

Nota sobre tus CQ6 y CQ7: son consultas de **validacion del grafo**, no de
dominio. Preguntan por ADRs sin `selectsAlternative`, con mas de una, o en
estado `Superseded` sin `supersededBy`. La ontologia las "cubre" si declara
esas propiedades con sus cardinalidades; la comprobacion en si es una consulta
que se ejecuta sobre las instancias.

### 7.2 Criterios estructurales

Adaptacion de los 8 criterios binarios de la fase 2 del experimento inicial:
clase definida por equivalencia, clase pivote, correccion sintactica,
dominio/rango, jerarquia, coherencia de la jerarquia, propiedades de datos e
individuos. Automaticos salvo la coherencia semantica, donde solo se automatiza
la parte decidible (ciclos, disjunciones contradictorias) y el resto queda
marcado `[revisar a mano]`.

## 8. Que produce una corrida

```
outputs/run_YYYYMMDD_HHMMSS/
├── manifest.json          config + hash de cada prompt + versiones
├── transcript.jsonl       todas las comunicaciones con el modelo
├── ontology.py            ontologia final como script NetworkX canonico
├── ontology.json          node-link JSON de NetworkX
├── ontology.graphml       para Gephi / Cytoscape / yEd
├── ontology.ttl           export a Turtle/OWL (Protege, OOPS!, razonador)
├── summary.json           metricas globales + registro por CQ + tokens
├── cqs/CQ1/
│   ├── response.md        respuesta cruda del modelo
│   ├── code.py            script ya validado
│   ├── ontology_after.py  la ontologia tras esta etapa
│   └── record.json        anadido, perdido, correcciones, delta, tokens
└── eval/
    ├── cq_verification.json
    ├── cq_verification.csv    ← hoja para tu validacion humana
    └── verification.jsonl
```

El `record.json` de cada CQ lleva `added`, `lost`, `restored_by_pipeline` y
`delta`: es la traza de como fue creciendo la ontologia etapa a etapa, que es
el argumento principal a favor de CQbyCQ frente a zero-shot.

El `ontology.ttl` no forma parte del metodo (el destino aqui es NetworkX), pero
permite comparar 1:1 con el resultado original del paper y pasar la ontologia
por Protege, OOPS! o un razonador.

## 8.1 Retomar una corrida interrumpida

Una corrida de 7 CQs son 15-20 llamadas, y basta con que la cuota se agote en
la CQ5 para perder las cuatro anteriores. `--resume` evita repagarlas:

```bash
python -m cqbycq run --story inputs/historia_adr.json \
                     --resume outputs/run_20260921_180225
```

Reutiliza las etapas que dejaron `record.json` **y** `ontology_after.py`, y
sigue por la primera que falte. Una etapa a medias se rehace entera: el grafo
acumulado se reconstruye desde `ontology_after.py`, asi que heredar un estado
incompleto daria una ontologia incoherente.

Lo que garantiza:

- la ontologia final es **identica** a la de una sola tirada (lo comprueba
  `tests/test_resume.py`, comparando los dos scripts);
- los tokens de las etapas reutilizadas se suman a los nuevos, para que el
  `summary.json` no mienta sobre lo que costo la ontologia;
- el `manifest.json` original **no** se toca, porque guarda los hashes de los
  prompts de la corrida inicial; cada reanudacion se apunta en `resumes.jsonl`
  con su fecha, las CQs reutilizadas y los hashes de los prompts usados esa
  vez. Si entre una tanda y otra editas un prompt, ahi queda la prueba.

Reanudar con otra historia se rechaza, y con `--technique zero-shot` tambien:
es una sola interaccion, no hay etapas que retomar.

## 9. Configuracion

```json
{
  "model": "gemini-3.8-flash",
  "effort": "high",
  "max_tokens": 32000,
  "thinking_budget": null,
  "max_tokens": 16000,
  "technique": "CQbyCQ",
  "section_order": ["header", "helper", "story", "task", "footer", "memory"],
  "memory_mode": "canonical",
  "llm_does_merge": true,
  "restore_lost": true,
  "repair_attempts": 2,
  "model_additional_statement": true,
  "namespace": "http://www.example.org/test#"
}
```

- `section_order`: las secciones constantes que van al principio forman el
  prefijo cacheado. La memoria va al final, como el `{RDF}` del prompt original.
- `memory_mode`:
  - `canonical` — la memoria se re-serializa desde el grafo: orden estable, sin
    duplicados, sin la prosa que el modelo haya podido colar. **Recomendado.**
  - `raw` — se pasa el script tal cual lo emitio el modelo, mas fiel a la
    implementacion original pero arrastra su ruido.
- `llm_does_merge`:
  - `true` (por defecto, y lo que pide el prompt original) — el modelo reemite
    la ontologia entera y su salida sustituye a la anterior.
  - `false` (`--delta-mode`) — el modelo devuelve solo lo nuevo y el pipeline
    fusiona. Sale mas barato en tokens y elimina el riesgo de regresion, a
    costa de apartarse del prompt original. Util como variante para comparar.
- `restore_lost`: red de seguridad del modo anterior.
- `thinking_budget`: tope duro de tokens de razonamiento (`--thinking-budget`).
  `null` deja que mande `effort` via `thinking_level`, que **no** acota cuanto
  piensa el modelo. Si el modelo rechaza una de las dos formas, el cliente baja
  un escalon solo (`budget` -> `level` -> sin razonamiento) y lo avisa, en vez
  de tirar la corrida.
- **Reintentos.** Hay dos capas. El SDK reintenta por su cuenta los cortes de
  red y los 5xx puntuales (`max_retries`, `retry_initial_delay`,
  `retry_max_delay`), pero solo cubre la peticion inicial. Encima va la del
  pipeline, que reemite la llamada **entera** cuando la API responde 503
  (*"this model is currently experiencing high demand"*) o 429, y tambien
  cuando el stream se corta a mitad de la generacion: `overload_retries` (5) y
  `overload_backoff_s` (20 s, que se duplica hasta un tope de 120 s, o sea
  20/40/80/120/120). Son esperas largas a proposito: un pico de demanda no se
  pasa en dos segundos, y perder una corrida de 7 CQs por un 503 sale mucho
  mas caro que esperar seis minutos. Un 400 o un 404 **no** se reintentan: son
  fallos de la peticion, no del servidor.
- **Cuota (429).** No todos los 429 son iguales, y el pipeline los separa
  leyendo el `QuotaFailure` del cuerpo de la respuesta. Si el limite tocado es
  **por minuto**, se espera lo que diga el `RetryInfo.retryDelay` del servidor
  y se reintenta. Si es **por dia** (o el servidor pide esperar mas de diez
  minutos), se corta en seco: la cuota diaria no se renueva esperando dentro
  de la corrida, asi que gastar cinco reintentos en ello solo hace perder
  tiempo. El mensaje dice entonces las tres salidas reales: activar
  facturacion, cambiar de modelo, o retomar cuando la cuota se renueve.

Los ficheros de `prompts/` son el esqueleto editable del metodo. Los
placeholders usan `{{doble_llave}}`, asi que las llaves simples de los ejemplos
de codigo quedan literales. Disponibles: `cq_id`, `cq_text`, `cq_index`,
`cq_total`, `story_title`, `story_text`, `all_requirements`,
`convention_reference`, `modelled_so_far`; en el de correccion, `code`,
`errors` y `previous_graph`.

## 10. Limites de esta replica

- El paper evalua GPT-3.5/GPT-4 (Azure, versiones 613). Aqui el backend es
  Gemini. Los numeros **no son comparables** con la Tabla 2 del paper: lo que
  se replica es el metodo, no el resultado.
- La evaluacion automatica es un borrador; el paper puntua a mano.
- El grafo no tiene razonador. Las restricciones y las clases definidas se
  almacenan fielmente, pero nada infiere tipos ni detecta inconsistencias sobre
  el `MultiDiGraph`. Si tu tesis necesita esa parte, exporta a `ontology.ttl` y
  pasa HermiT o Pellet desde Protege.
- `to_turtle()` es un serializador minimo escrito a mano, no `rdflib`: cubre lo
  que genera esta convencion y no pretende ser un writer OWL general.
