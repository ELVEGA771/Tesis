# Esquema del grafo de decisiones · v1.0 (congelado)

Esquema formal (nodos, relaciones y propiedades) del grafo de decisiones sobre
ADR. Es el entregable del objetivo específico 1 y de la actividad A2 de la
planificación. **Congelado el 2026-09-22**: la anotación del subconjunto de
verificación, las funciones de escritura del pipeline de LangGraph y las
consultas del protocolo se construyen sobre esta versión. Cualquier cambio
posterior tiene que publicarse como una versión nueva.

---

## 1. Cómo se obtuvo

El esquema se derivó con **CQbyCQ** (Saeedizade & Blomqvist, ESWC 2024),
adaptado a un grafo NetworkX, con **competency questions derivadas a priori de
las métricas de la planificación aprobada**. No se derivó de ningún ADR del
corpus de evaluación. Antes hubo tres iteraciones, que forman parte del
proceso de diseño:

| Versión | Origen de las CQs | Clases / pivote | Prop. obj. / datos | Tokens | Lección |
|---|---|---|---|---|---|
| v1 | Historia genérica, 7 CQs | 26 / 2 | 22 / 10 | 96k | El método funciona; aparecen alias redundantes y ninguna clase definida real |
| v2 | CQs de plantillas (MADR, Nygard, Tyree), 13 CQs | 30 / 3 | 23 / 12 | 179k | Modela campos de libro poco frecuentes; le faltan tema y procedencia. El LLM convierte en clases los ejemplos entre paréntesis de una CQ |
| CQ10 | 10 CQs ancladas en ADR excluidos del corpus | 20 / **8** | 29 / **46** | 243k | Sin fuga de datos, pero con cambio de distribución: modela fenómenos casi ausentes del corpus (§5) |
| **plan** | **8 CQs derivadas de las métricas + 1 requisito** | 9 / 1 | 11 / 10 | 125k | Es la base de la v1.0 |

- **Corrida base:** `outputs/esquema_plan` (entrada `inputs/historia_adr_plan.json`;
  `gemini-3.8-flash`, `temperature=0`, `thinking_budget=8192`). 9 etapas, ninguna
  pérdida entre etapas.
- **Ajustes posteriores:** los cuatro de ingeniería de §6, aplicados con
  `congelar_esquema.py` (reproducible).

## 2. Trazabilidad planificación → CQ → esquema

| CQ | Pregunta | Métrica de la planificación | Términos que la responden |
|---|---|---|---|
| CQ1 | ¿Qué decisión registra un ADR y a qué repositorio pertenece? | B1: P/R/F1 de nodos de decisión | `recordsDecision`, `Decision.decisionDescription`, `belongsToRepository` |
| CQ2 | ¿Qué alternativas se consideraron y cuál se adoptó? | B1: F1 de alternativas · B2: conjunto de alternativas | `considersAlternative`, `selectsAlternative` |
| CQ3 | ¿Qué estado declara, con qué literal, a qué normalizado corresponde y en qué fecha? | B2: base del estado en una fecha | `hasStatusDeclaration`, `rawStatusLiteral`, `hasNormalizedStatus`, `recordDate` |
| CQ4 | ¿Sobre qué tema trata y qué otros ADR tratan ese tema? | B3: agrupación por tema | `addressesTopic`, `Topic` |
| CQ5 | ¿Qué ADR reemplaza a cuál, y lo declaran ambos o solo uno? | B3: F1 de reemplazo entre pares | `RelationDeclaration` + `sourceADR`/`targetADR`/`declaresRelation` |
| CQ6 | ¿Qué ADR enmienda a otro sin reemplazarlo? | B3: evita falsos positivos de reemplazo | `relationType = reltype_amends` |
| CQ7 | ¿En qué estado estaba la decisión en la fecha F? | B2: exactitud del estado en una fecha | `recordDate` + reemplazos (§8) |
| CQ8 | ¿Cuál es la decisión vigente sobre un tema y por qué cadena? | B3: exactitud de la decisión vigente | `Topic` + reemplazos + `recordsDecision` |
| ADD | Toda afirmación guarda el fragmento literal que la respalda | B1: tasa de nodos no fundamentados | `GroundedElement.evidenceSnippet` |

El bloque 4 (reproducibilidad) no exige nada del esquema.

## 3. Clases

```
Document
└── ArchitectureDecisionRecord      identidad = repositorio + fileName

GroundedElement                     toda instancia lleva evidenceSnippet
├── Decision                        una por ADR
├── Alternative
├── StatusDeclaration               literal crudo + estado normalizado
└── RelationDeclaration   [pivote]  una por documento que afirma la relación

Repository
Topic                               asignado, no extraído
NormalizedStatus    = {proposed, accepted, rejected, deprecated, superseded, amended}
RelationType        = {supersedes, amends}
```

Los dos vocabularios son **cerrados**: sus valores son individuos fijos del
esquema (`status_*`, `reltype_*`), y el validador rechaza cualquier valor nuevo.

## 4. Propiedades

**De objeto (12)** — F = funcional

| Propiedad | Dominio → Rango | F | Nota |
|---|---|---|---|
| `belongsToRepository` | ADR → Repository | F | inversa: `containsADR` |
| `recordsDecision` | ADR → Decision | F | obligatoria |
| `considersAlternative` | ADR → Alternative | | |
| `selectsAlternative` | ADR → Alternative | F | `⊂ considersAlternative` |
| `hasStatusDeclaration` | ADR → StatusDeclaration | F | |
| `hasNormalizedStatus` | StatusDeclaration → NormalizedStatus | F | vocabulario cerrado |
| `addressesTopic` | ADR → Topic | | |
| `sourceADR` | ADR (**nuevo**) → RelationDeclaration | | |
| `targetADR` | ADR (**afectado**) → RelationDeclaration | | |
| `declaresRelation` | ADR (**declarante**) → RelationDeclaration | | |
| `relationType` | RelationDeclaration → RelationType | F | vocabulario cerrado |
| `containsADR` | Repository → ADR | | |

**De datos (9)**

| Propiedad | Dominio | Rango | F |
|---|---|---|---|
| `fileName`, `title` | ADR | xsd:string | F |
| `recordDate` | ADR | xsd:date | F |
| `repositoryName` | Repository | xsd:string | F |
| `decisionDescription` | Decision | xsd:string | |
| `alternativeDescription` | Alternative | xsd:string | |
| `rawStatusLiteral` | StatusDeclaration | xsd:string | F |
| `topicName` | Topic | xsd:string | F |
| `evidenceSnippet` | GroundedElement | xsd:string | |

## 5. Fuera del alcance (con datos)

Criterio: fenómenos que **ninguna métrica de la planificación mide** y que son
**marginales en el corpus de evaluación** (140 ADR). Las cifras solo acotan el
alcance; no se usaron para diseñar el esquema.

| Fenómeno | Frecuencia en el corpus |
|---|---|
| Reemplazo parcial («superseded in part») | 1/140; los 36 reemplazos válidos del ground truth son totales |
| Relaciones negadas («does NOT supersede»), «Supersedes: none» | 0/140 |
| Armonización, «builds on» | 0/140 |
| Sub-decisiones numeradas | solo aparecen con reemplazos parciales o enmiendas por sub-decisión |
| Participantes y roles (deciders) | 4/140 |
| Destinos que no son ADR (código, variables de entorno, secciones) | quedan como texto de evidencia |

El catálogo completo de casos límite está en
`Extraccion_ADR/competency_questions_v2.md` y en la corrida `outputs/esquema_cq10`.
Queda documentado como trabajo futuro.

## 6. Ajustes de ingeniería sobre la corrida base

Salieron de probar el esquema con cadenas reales del corpus. No cambian qué
responde el esquema.

| # | Problema | Ajuste |
|---|---|---|
| A1 | `evidenceSnippet` tenía 5 dominios, y en OWL eso es una **intersección** | Superclase `GroundedElement` como único dominio; `Topic` excluido |
| A2 | `NormalizedStatus` no tenía valores | 6 individuos fijos (se añade `rejected`, presente en el corpus) |
| A3 | `relationType` era texto libre | ObjectProperty hacia `RelationType` {supersedes, amends} |
| A4 | Faltaba funcionalidad | `recordsDecision` y `selectsAlternative` pasan a Functional |

## 7. Convenciones de uso (también en los `comment` del esquema)

1. **Identificador de nodo:** slug de repositorio + fichero
   (`adr_DFE-Digital_early-years-foundation-recovery__0006-content-storage-strategy`).
   Los nombres de nodo solo admiten letras, dígitos, `_` y `-`.
2. **Dirección de las relaciones:** `sourceADR` es el ADR nuevo y `targetADR` el
   afectado, diga el texto «superseded by» o «supersedes».
3. **Una `RelationDeclaration` por documento declarante.** El reemplazo es
   bidireccional si existen declaraciones de los dos extremos.
4. **`recordDate` no la extrae el LLM:** sale de `corpus_manifest.csv` o del historial de git.
5. **`Topic` se asigna:** todos los ADR de una cadena de reemplazo comparten el
   mismo nodo `Topic`. No cuenta en la tasa de nodos no fundamentados.
6. **Opción elegida no listada:** se crea como `Alternative`, con la frase de la
   elección como evidencia.

## 8. Validación (actividad A6)

`conformidad.py` valida las **instancias**. El validador de CQbyCQ solo revisaba el esquema.

| Mecanismo de la planificación | Qué comprueba |
|---|---|
| Verificación de esquema | Tipos, dominio y rango con subclases, atributos declarados, vocabularios cerrados, propiedades funcionales, obligatorios (decisión, repositorio, fichero) |
| Integridad referencial | Cada `RelationDeclaration` tiene exactamente 1 origen, 1 destino, 1 declarante y 1 tipo; origen ≠ destino; los extremos de un reemplazo comparten tema |
| Nodos no fundamentados | El `evidenceSnippet` de cada `GroundedElement` aparece literalmente en el fichero de su ADR. Devuelve la **tasa de nodos no fundamentados** |

`prueba_esquema_v1.py` inyecta seis errores típicos de extracción (evidencia
inventada, valor de estado nuevo, atributo no declarado, rango incorrecto,
dos decisiones, falta de evidencia) y comprueba que se detectan los seis.

**Regla de CQ7 (estado en la fecha F):**
- Antes de su `recordDate`, el ADR «no existía».
- Después toma su estado declarado; si hoy dice superseded o amended, se asume
  que antes estaba accepted.
- Pasa a `superseded` en la fecha del primer ADR que lo reemplaza.
- Si falta alguna de esas fechas, el resultado es `indeterminado`.

## 9. Pruebas con datos reales

Cadenas de `DFE-Digital/early-years-foundation-recovery`:

- **CQ5:** 2/2 pares coinciden con `ground_truth_reemplazos.csv`, incluido quién
  declara la relación (0006→0011 solo lo declara 0006; 0018↔0023 lo declaran ambos).
- **CQ8:** la decisión vigente es la correcta en los dos temas (Contentful y Microsoft Clarity).
- **Fundamentación:** 23/23 evidencias encontradas en su fichero.
- **CQ7** con fechas: correcta (0006 pasa de accepted a superseded en la fecha de 0011).

## 10. Limitaciones conocidas

1. **Fechas:** 30 de 140 ADR no tienen fecha (13 de los 45 del subconjunto). Esos
   ADR no entran en las consultas de estado en una fecha, salvo que se recupere la
   fecha del historial de git.
2. **El tema depende de un paso de asignación** (agrupar por cadena y por título).
   Si el pipeline crea temas distintos para ADR de la misma cadena, la CQ8 falla.
   Esto hay que medirlo.
3. **Sin clase definida por equivalencia** (criterio estructural 0.75): es
   consecuencia de pedir un esquema ligero, no un olvido.
4. **Funcional con mundo abierto:** en OWL, dos `selectsAlternative` no darían error,
   sino que el razonador deduciría que las dos alternativas son la misma. El caso lo
   detecta `conformidad.py`, no un razonador.
5. **`selectsAlternative ⊂ considersAlternative`:** obliga a añadir como alternativa
   la opción elegida aunque no esté listada (convención 6).

## 11. Ficheros

| Fichero | Contenido |
|---|---|
| `ontology.py` / `.ttl` / `.json` / `.graphml` | Esquema v1.0 |
| `congelar_esquema.py` | Deriva la v1.0 de `outputs/esquema_plan` (ajustes A1–A4) |
| `conformidad.py` | Validador de instancias (A6); módulo y línea de comandos |
| `consultas.py` | Las 8 CQs como funciones sobre el grafo (base de la condición «grafo» de la evaluación) |
| `prueba_esquema_v1.py` | CQs sobre el gold anotado + pruebas de detección de errores |
| `../esquema_plan/prueba_corpus_dfe.py` | Prueba de la corrida base con dos cadenas reales |
| `Extraccion_ADR/corpus_tesis/anotacion/` | Hoja, guía y conversor de la anotación manual |
