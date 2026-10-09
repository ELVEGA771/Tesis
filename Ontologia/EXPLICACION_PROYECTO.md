# Carpeta `Ontologia/`: qué hay, qué hace y cómo se relaciona con CQbyCQ

Este documento explica todo lo que contiene `Tesis_herramientas/Ontologia/`:
qué hace cada archivo, qué parte del método original replica y qué se cambió
para el proyecto integrador (grafo de decisiones sobre ADR).

> **Paper de referencia:** M. J. Saeedizade y E. Blomqvist, *Navigating Ontology
> Development with Large Language Models*, ESWC 2024.
> Prompts originales: https://github.com/LiUSemWeb/LLMs4OntologyDev-ESWC2024
> (copia del prompt de CQbyCQ en `CQbyCQ.md`).

---

## 1. Resumen en un párrafo

La carpeta tiene tres partes. La primera es una **réplica del método CQbyCQ**
(paquete `cqbycq/` y carpeta `prompts/`): un LLM construye una ontología
resolviendo una *competency question* (CQ) cada vez. La réplica tiene una
diferencia deliberada: el modelo no escribe Turtle/OWL, sino un **script de
Python/NetworkX**. La segunda parte son las **entradas** (`inputs/`), con las
historias y las CQs del dominio ADR que se probaron. La tercera son las
**corridas** (`outputs/`), con cinco versiones del esquema. La última,
`outputs/esquema_v1/`, es el **esquema v1.0 congelado** que usan la anotación
manual y el futuro pipeline de LangGraph. También incluye herramientas que el
paper no tiene: un validador de instancias y las consultas de las CQs.

---

## 2. El método original, en breve

CQbyCQ (*Sub-task Decomposed Prompting – Competency Question by Competency
Question*) descompone los **requisitos**, no la tarea:

1. Se parte de una **historia** (descripción del dominio) y de una lista de CQs.
2. En cada etapa *i*, el LLM recibe un prompt con cuatro secciones fijas:
   **header**, **helper**, **story** y **footer**. Además recibe la CQ*i* y la
   ontología acumulada hasta ese momento (`Here is the last RDF: {RDF}`).
3. El LLM devuelve **la ontología completa** en Turtle. Esa salida sustituye a
   la anterior, así que la fusión consiste en reescribir todo, no en añadir.
4. Una **memoria externa** guarda el código generado y las comunicaciones.
5. Hay un paso de **validación sintáctica y corrección**.
6. La evaluación es **CQ Verification**: `score = (1/n) Σ f(O, CQᵢ)`, donde
   *f* vale 1 si se puede escribir una consulta SPARQL que responda la CQ. La
   hacen evaluadores humanos y se reporta con y sin *minor issues*.
7. En una fase previa, el paper filtra combinaciones de LLM y técnica con
   **8 criterios estructurales binarios**.

El apéndice del paper documenta los fallos típicos, y la réplica los tiene en
cuenta: el modelo olvida fusionar lo anterior, se salta la reificación sin el
*helper* y se distrae con los ejemplos de ODP y *few-shot*.

---

## 3. Mapa de la carpeta

```
Ontologia/
├── CLAUDE.md                 reglas de trabajo con Claude (memoria Qdrant, ingesta de PDFs)
├── CQbyCQ.md                 prompt original del paper (referencia, en inglés)
├── README.md                 manual de uso de la réplica
├── EXPLICACION_PROYECTO.md   este documento
├── requirements.txt          google-genai, networkx
├── .env.example              plantilla para GEMINI_API_KEY
├── cqbycq/                   el pipeline (paquete de Python)
├── prompts/                  las secciones del prompt, en español y adaptadas a NetworkX
├── inputs/                   historias + CQs (JSON)
├── outputs/                  corridas y esquema congelado
└── tests/                    pruebas que no gastan API
```

---

## 4. El pipeline: `cqbycq/`

| Archivo | Qué hace | Equivalente en el paper |
|---|---|---|
| `__init__.py`, `__main__.py` | Declaran el paquete y permiten ejecutar `python -m cqbycq`. | — |
| `cli.py` | Línea de comandos: `run`, `evaluate`, `criteria`, `score`, `api` (muestra la convención) y `models`. Opciones `--technique zero-shot`, `--delta-mode`, `--resume` y `--thinking-budget`. | El paper no publica una herramienta; son los scripts del experimento. |
| `config.py` | Parámetros de la corrida (modelo, `temperature=0`, `effort`, `max_tokens`, orden de secciones, modo de memoria, número de correcciones…). Escribe el `manifest.json` con el **hash SHA-256 de cada prompt**. | El paper fija `temperature=0` y penalizaciones a 0. El manifiesto se añadió porque el apéndice advierte de la variabilidad entre corridas idénticas. |
| `story.py` | Carga la historia y las CQs desde el JSON de `inputs/`. | La sección *Story*. |
| `prompts.py` | Monta el prompt: header, helper, story, **task** (la CQ de la etapa), footer y **memory** (el grafo anterior, al final). | Las 4 secciones del paper. `task` se separó del header para que el prefijo sea idéntico en todas las etapas y se pueda cachear. |
| `llm.py` | **Único** punto de contacto con la API (Google Gemini). Registra todas las llamadas en `transcript.jsonl`. Tiene reintentos por 503/429 y cortes de stream, distingue la cuota por minuto de la diaria y traduce `effort` a `thinking_level`/`thinking_budget`. | El paper usó GPT-3.5/GPT-4 en Azure. La parte de «comunicaciones previas» de la memoria externa está aquí. |
| `memory.py` | Guarda el grafo acumulado y lo devuelve en el formato que el modelo debe producir. Por defecto `canonical`: re-serializado, ordenado, sin duplicados ni prosa. | La memoria externa (`{RDF}` de la etapa anterior). |
| `dsl.py` | **Validación nivel 1.** Parsea el script con `ast` y admite solo `import networkx as nx`, `G = nx.MultiDiGraph()`, `G.add_node(...)` y `G.add_edge(...)` con literales. **No usa `exec`**: interpreta llamada a llamada. | *Syntax validation* del Turtle. |
| `ontograph.py` | Define la **convención del grafo** (sección 7) y hace la **validación nivel 2** (`validate`): cada error del footer original convertido en una comprobación automática. También hace la **validación nivel 3** (`diff`): qué perdió el modelo respecto a la etapa anterior. Exporta a `.py`, `.json`, `.graphml` y **`.ttl`**. | Equivale a validar el Turtle. El nivel 3 es nuevo y responde al fallo del apéndice: *«LLMs often forgot to merge…»*. |
| `pipeline.py` | El bucle CQ a CQ: prompt → LLM → 3 niveles de validación → prompt de corrección (2 intentos) → nueva versión. Si al final el modelo sigue perdiendo nodos, los **reinyecta** (`restore_lost`) y lo anota en el `record.json`. También hace `zero-shot` (una sola llamada con todas las CQs) y `--resume`. | Es el núcleo del método. `restore_lost` y `--resume` son añadidos de ingeniería. |
| `evaluate.py` | **CQ Verification**: produce un borrador con un LLM evaluador (`prompts/verify_cq.md`) y una hoja `cq_verification.csv` con columnas `humano_*` vacías. `score` calcula la métrica estricta y la métrica sin menores, más el **acuerdo LLM/humano**. | La métrica del paper. Allí la puntuación es humana; aquí el LLM solo pre-clasifica. |
| `criteria.py` | Los **8 criterios estructurales binarios**: clase definida por equivalencia, pivote, sintaxis, dominio/rango, jerarquía, coherencia, propiedades de datos e individuos. La coherencia semántica solo se automatiza en lo decidible (ciclos, disjunciones); el resto queda como `needs_human`. | Fase 2 del experimento inicial del paper. |

---

## 5. Los prompts: `prompts/`

Son una **traducción al español** del prompt de `CQbyCQ.md` con el mismo nivel
de detalle. La sintaxis Turtle se cambió por la convención NetworkX. Los
marcadores usan `{{doble_llave}}` para que las llaves de los ejemplos de código
no se interpreten.

| Archivo | Sección del paper | Qué cambia |
|---|---|---|
| `header_cqbycq.md` | *Header* | «Your output is an independent turtle» pasa a «un script de Python ejecutable e independiente». El chequeo de respondibilidad («is this CQ answerable by the previous RDF?») se pide como **comentario dentro del bloque de código**, porque la salida no admite prosa fuera del bloque. Se añade una línea de comentario que declara las clases pivote antes de usarlas (errores 2 y 5 del footer original). |
| `helper.md` | *Helper* | Mismos contenidos: clases, jerarquías, reificación con el ejemplo usuario/recurso/tiempo, propiedades de objeto y de datos, características de propiedad y restricciones (`someValuesFrom`, `allValuesFrom`, axiomas generales). Todo se expresa con `add_node` y `add_edge`. **No se usa el prefijo `Cl_`**: el tipo va en el atributo `type`. Se añaden un esqueleto del script y un resumen de la convención. Los ejemplos son solo de forma, **nunca del dominio ADR**. |
| `footer.md` | *Footer* | Recoge las cuatro listas de errores (generales, clases, jerarquía, propiedades de objeto y de datos). Se añade una sección, **«Errores en relaciones temporales y argumentativas»**, que pide reificar un cambio de estado en una fecha o los pros y contras de una alternativa. Es la única pista orientada al dominio, y se formula en abstracto. |
| `repair.md` | Paso de corrección | Da los errores del validador y el **grafo anterior («lo que no puedes perder»)** y pide el script completo corregido. |
| `verify_cq.md` | Evaluación humana | Prompt del LLM evaluador que hace el borrador de CQ Verification. Juzga la estructura, no los datos. |

**Decisiones que vienen del apéndice del paper:**
- El `helper` y el `footer` son obligatorios, porque sin ellos desaparecen la
  reificación y la taxonomía.
- No hay *few-shot* ni ODPs, porque el paper muestra que empeoran el resultado.
- El prompt lleva siempre **la historia completa y todas las CQs**. La CQ de la
  etapa solo acota la tarea.

---

## 6. Las entradas: `inputs/`

Cada JSON tiene `title`, `story`, `additional_statement` (requisito en prosa
que se modela como una etapa extra, `ADD`) y `competency_questions`.

| Archivo | Qué es | Qué pasó con él |
|---|---|---|
| `historia_ejemplo.json` | Dominio de juguete (compañía de teatro, 14 CQs) | Prueba del método fuera del dominio ADR. |
| `historia_adr.json` | Primera historia ADR genérica, 7 CQs | Corrida **v1** (`run_20260921_180225`). |
| `historia_adr_v2.json` | 13 CQs inspiradas en las plantillas MADR, Nygard y Tyree | Corrida **v2** (`run_20260922_191258`). Descartada: sigue campos de libro y le faltan tema y procedencia. |
| `historia_adr_cq10.json` | 10 CQs del autor (generadas desde ADR *excluidos* del corpus) | Corrida **CQ10**. Descartada por cambio de distribución: sobremodela fenómenos casi ausentes del corpus. |
| `historia_adr_plan.json` | **8 CQs derivadas de las métricas de la planificación** + requisito de procedencia | Corrida **esquema_plan**, que es la base de la v1.0. Los ejemplos de la historia se cambiaron por ejemplos neutros para evitar fuga de datos del corpus de evaluación. |

---

## 7. La convención del grafo (el «lenguaje destino»)

Es la adaptación central respecto al paper. Todo es un nodo con atributo
`type` y toda arista lleva `relation`.

| Concepto OWL/Turtle | En NetworkX |
|---|---|
| `owl:Class` | `G.add_node("ADR", type="Class")` (`pivot=True` si es de reificación) |
| `owl:ObjectProperty` / `owl:DatatypeProperty` | Nodo con `type="ObjectProperty"` / `"DatatypeProperty"` |
| `rdfs:domain` / `rdfs:range` | Aristas `domain` / `range` que **salen del nodo de la propiedad** |
| `rdfs:subClassOf`, `owl:equivalentClass`, `owl:disjointWith`, `owl:inverseOf`, `rdfs:subPropertyOf` | Aristas con esa `relation` |
| Nodo blanco `owl:Restriction` | Nodo `type="Restriction"` + aristas `onProperty` y `someValuesFrom`/`allValuesFrom`/`hasValue` |
| `xsd:*` | Nodo `type="Datatype"` |
| Características (Functional, Transitive…) | Lista en el atributo del nodo propiedad |
| Individuo | Nodo cuyo `type` es el nombre de una clase declarada; sus relaciones son aristas con el nombre de la propiedad |

Se usa **`MultiDiGraph`**, porque un mismo ADR puede tener
`considersAlternative` **y** `selectsAlternative` hacia la misma alternativa, y
un `DiGraph` perdería una de las dos aristas.

**Por qué NetworkX y no Turtle:** la planificación de la tesis fija un grafo
NetworkX + JSON como representación del pipeline de LangGraph. Si el esquema
nace en ese formato, los extractores escriben en él directamente. Para no
perder la comparación con el paper ni las herramientas OWL, cada corrida
exporta también `ontology.ttl` (Protégé, OOPS!, razonadores).

---

## 8. Las corridas: `outputs/`

### 8.1 Estructura común de una corrida

```
manifest.json        config + hash de cada prompt + versiones
transcript.jsonl     todas las comunicaciones con el modelo
ontology.py/.json/.graphml/.ttl   ontología final en 4 formatos
summary.json         métricas globales + registro por CQ + tokens
resumes.jsonl        (si se reanudó) cuándo y con qué prompts
cqs/CQk/             response.md (crudo), code.py (validado),
                     ontology_after.py (estado tras la etapa),
                     record.json (añadido, perdido, restaurado, delta, tokens)
eval/                cq_verification.json/.csv (hoja humana), verification.jsonl
```

El `record.json` de cada etapa muestra cómo creció la ontología, y es el
argumento principal a favor de CQbyCQ frente a zero-shot.

### 8.2 Las cinco versiones y sus archivos extra

| Carpeta | Versión | Archivos añadidos a mano (no los genera el pipeline) |
|---|---|---|
| `run_20260921_180225/` | **v1**: 7 CQs, 26 clases, 2 pivotes, 96k tokens | `revision.md`: hoja de revisión manual para leer. `ejemplo_consultas.py`: puebla la ontología con individuos y prueba las 7 CQs. `adr_de_prueba.py`: un ADR ficticio (ADR-012, RabbitMQ frente a Kafka) cargado y validado. `ontologia_explicada.md`. |
| `run_20260922_191258/` | **v2**: 13 CQs, 35 clases, 3 pivotes, 179k tokens | `adr_de_prueba.py` (ADR-012 + tres ADR vecinos) y `ontologia_explicada.md`. |
| `run_20260922_191258_sin_CQ7/` | **v2 sin la CQ7** (ablación) | `editar_sin_cq7.py` quita **exactamente** lo que añadió la etapa CQ7 según su `record.json`, sin volver a correr, y comprueba que coincide 1:1, que ninguna otra etapa lo usaba y que el grafo sigue siendo válido. La CQ7 había convertido en clases los ejemplos entre paréntesis (`Servicio`, `Modulo`…). |
| `esquema_cq10/` | **CQ10**: 20 clases, 8 pivotes, 46 propiedades de datos, 243k tokens | — (queda como catálogo de casos límite y trabajo futuro). |
| `esquema_plan/` | **plan**: 9 clases, 1 pivote, 125k tokens, sin pérdidas entre etapas | `prueba_corpus_dfe.py`: anota a mano dos cadenas **reales** (DFE-Digital 0006→0011 y 0018↔0023), las compara con `ground_truth_reemplazos.csv` y comprueba que cada evidencia aparece literalmente en su fichero. |
| **`esquema_v1/`** | **v1.0 congelado** (2026-09-22) | Ver 8.3. |

### 8.3 `esquema_v1/`: el esquema que usa la tesis

| Archivo | Qué hace |
|---|---|
| `esquema_v1.md` | Documento formal del esquema: trazabilidad planificación → CQ → términos, clases, propiedades, alcance, ajustes, convenciones y limitaciones. |
| `ontology.py` / `.ttl` / `.json` / `.graphml` | El esquema v1.0 en cuatro formatos. |
| `congelar_esquema.py` | Genera la v1.0 desde `esquema_plan` aplicando los ajustes **A1–A4** de forma reproducible (sección 9.3). |
| `conformidad.py` | **Validador de instancias** (base de la actividad A6): verifica el esquema (tipos, dominio/rango con subclases, vocabularios cerrados, funcionales, obligatorios), la integridad referencial de cada `RelationDeclaration` y la **tasa de nodos no fundamentados** (¿aparece el `evidenceSnippet` literalmente en el ADR?). |
| `consultas.py` | Las 8 CQs como funciones sobre el grafo, que devuelven datos. Son la base de la condición «grafo» del protocolo de evaluación (bloques 2 y 3). |
| `prueba_esquema_v1.py` | Ejecuta las 8 CQs sobre el gold anotado e inyecta 6 errores típicos de extracción para comprobar que `conformidad.py` los detecta todos. |

Fuera de esta carpeta, en `Extraccion_ADR/corpus_tesis/anotacion/`, están la
hoja de anotación (`anotaciones.csv`), la guía, `generar_plantilla.py` y
`csv_a_grafo.py`, que convierte la hoja en un grafo conforme a la v1.0.

---

## 9. Qué se mantiene del paper y qué se adaptó

### 9.1 Fiel al método

- Descomposición **CQ a CQ**, con la ontología acumulada como memoria.
- Prompt de **4 secciones** (header, helper, story, footer) con el mismo
  contenido, traducido.
- **La salida sustituye a la anterior**: el modelo reemite todo (`llm_does_merge=true`).
- Chequeo de respondibilidad antes de modelar.
- `temperature=0`.
- Sin *few-shot* ni ODPs.
- **CQ Verification** con las variantes estricta y sin menores, y los 8 criterios
  estructurales.
- Línea base **zero-shot** (`--technique zero-shot`) para contrastar.

### 9.2 Adaptaciones técnicas (de la réplica)

| Adaptación | Motivo |
|---|---|
| Destino **Python/NetworkX** en lugar de Turtle | Es el formato del grafo de la tesis. Se exporta `.ttl` para comparar. |
| Backend **Gemini** (`gemini-3.8-flash`) en lugar de GPT-3.5/4 | Disponibilidad. Los números **no son comparables** con la Tabla 2 del paper: se replica el método, no el resultado. |
| **3 niveles de validación** (sintaxis + lista blanca, convención, regresión) | El paper solo valida la sintaxis. La regresión ataca el fallo de fusión del apéndice. |
| Lista blanca con `ast`, **sin `exec`** | El código lo escribe un LLM; ejecutarlo sería inseguro. |
| `restore_lost` + registro de pérdidas | Ninguna pérdida entre etapas pasa desapercibida. |
| Memoria `canonical` | Quita el ruido que el modelo arrastra de una etapa a otra (`raw` sigue disponible para ser fiel al original). |
| Chequeo de respondibilidad como comentario | La salida no admite prosa fuera del bloque. |
| La CQ va en `TAREA` y no en el header | Prefijo idéntico en todas las etapas, que Gemini cachea de forma implícita. |
| `--delta-mode` (el modelo devuelve solo lo nuevo) | Variante opcional para comparar; se aparta del prompt original. |
| `thinking_budget`, reintentos, `--resume` | Robustez: una CQ llegó a gastar 15k de 16k tokens pensando; hay 503/429 y cuotas. |
| `manifest.json` con hashes | Distinguir «cambié el prompt» de «el modelo no fue determinista». |
| Evaluación asistida por LLM + hoja humana + acuerdo | En el paper puntúan humanos; aquí el LLM pre-clasifica. |

### 9.3 Adaptaciones al caso de la tesis (dominio ADR)

1. **Origen de las CQs.** Las CQs no vienen de una historia de usuario
   inventada, sino de las **métricas de la planificación aprobada**: una CQ
   por métrica de los bloques B1–B3, más un requisito de procedencia. Así el
   esquema se puede rastrear hasta lo que se va a medir y no se sesga hacia las
   plantillas de libro (v2) ni hacia los ADR atípicos (CQ10). El corpus solo
   sirvió para **acotar el alcance** (sección 5 de `esquema_v1.md`).
2. **Ajustes A1–A4 tras la corrida** (`congelar_esquema.py`):
   - **A1:** superclase `GroundedElement` como único dominio de
     `evidenceSnippet`, porque en OWL varios dominios equivalen a una
     intersección.
   - **A2:** `NormalizedStatus` pasa a ser un vocabulario cerrado de 6
     individuos (se añade `rejected`).
   - **A3:** `relationType` pasa de texto libre a `RelationType`
     {supersedes, amends}.
   - **A4:** `recordsDecision` y `selectsAlternative` pasan a ser funcionales.
3. **Relaciones entre ADR reificadas** (`RelationDeclaration`, la única clase
   pivote), con una por documento declarante. Así se mide *quién* declara un
   reemplazo (17 de 36 pares son unilaterales) y se guarda la evidencia de cada
   declaración.
4. **Procedencia obligatoria:** toda instancia extraída lleva el fragmento
   literal que la respalda. Con eso se calcula la tasa de nodos no
   fundamentados (métrica B1).
5. **Campos que no extrae el LLM:**
   - `recordDate` sale del manifiesto o de git.
   - `Topic` se **asigna**: todos los ADR de una cadena de reemplazo comparten
     tema, y no cuenta como nodo no fundamentado.
6. **Validación de instancias**, que el paper no tiene: el validador de CQbyCQ
   revisa el esquema, y `conformidad.py` revisa los **datos** que producirá el
   extractor (Qwen3-4B).
7. **CQs ejecutables** (`consultas.py`): en el paper la CQ Verification es un
   juicio sobre el esquema; aquí las CQs se convierten en consultas reales que
   usará la evaluación grafo frente a RAG. Incluye la regla temporal de la CQ7
   (estado en una fecha).
8. **Esquema ligero a propósito:** 1 pivote y 9 propiedades de datos, frente a
   8 y 46 en la versión CQ10, porque un modelo de 4B tiene que poder rellenarlo.
   No hay clase definida por equivalencia: es una consecuencia buscada, no un
   olvido.
9. **Nombres en inglés** (el corpus está en inglés y el dataset será público).
   La identidad de un ADR es `repositorio + fichero`, porque los números
   declarados se repiten.

---

## 10. Pruebas: `tests/`

Ninguna llama a la API.

| Archivo | Qué comprueba |
|---|---|
| `test_offline.py` | Validador (niveles 1 y 2), regresión y exportaciones. |
| `test_pipeline_mock.py` | El bucle completo con un LLM simulado: memoria, correcciones, detección de pérdidas y artefactos en disco. |
| `test_llm_retry.py` | Que un 503 se reintenta, que un 400 no, y que los mensajes de error dicen qué hacer. |
| `test_resume.py` | Que `--resume` no repite etapas y que el resultado es **idéntico** a una corrida de una sola tirada. |

---

## 11. Qué conviene declarar en la tesis

- Se replica el **método** CQbyCQ, no los resultados, con otro backend (Gemini)
  y otro lenguaje destino (NetworkX, con export a Turtle).
- La CQ Verification del esquema **no se revisó a mano**. Fue decisión del
  autor, y en el paper la revisión es humana.
- Hubo cuatro iteraciones del esquema antes de la v1.0 (v1, v2, CQ10 y plan), y
  cada una dejó una lección. La más importante: el origen de las CQs introduce
  sesgo, y el LLM convierte en clases los ejemplos entre paréntesis de una CQ.
- Los ajustes A1–A4 se hicieron después de la corrida y a mano, pero son
  reproducibles con `congelar_esquema.py`.
- Limitaciones:
  - No hay razonador sobre el `MultiDiGraph`, así que lo funcional se comprueba
    con `conformidad.py`.
  - 30 de los 140 ADR no tienen fecha.
  - La CQ8 depende de que el paso de asignación de `Topic` funcione.

Documentos relacionados:
- `outputs/esquema_v1/esquema_v1.md` (esquema formal)
- `../ontologia_v1_explicada_tutor.md` (explicación para el tutor)
- `../bitacora_sesion_2026-09-22.md` (cronología completa y decisiones)
