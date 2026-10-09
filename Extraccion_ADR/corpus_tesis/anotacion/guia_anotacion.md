
# Guía de anotación del subconjunto de verificación

Anotación manual de los **45 ADR** de `anotacion_subset.csv` con el esquema
**v1.1** (`Ontologia/outputs/esquema_v1_1`: MADR 4.0.0 completo, derivado con
CQbyCQ y congelado con los ajustes C1–C5). El resultado, `gold_anotado.json`,
es la referencia (*ground truth*) de los bloques 1 y 2 de la evaluación.

> Versión anterior de esta guía (esquema v1.0): `guia_anotacion_v1_0.md.bak`.

## 0. Esquema v1.1 y herramienta de anotación (léelo primero)

**Herramienta:** doble clic en `anotar.bat` (o `python anotador/servidor.py`) y
abre http://127.0.0.1:8765. Lee y escribe esta misma hoja, prellena las filas a
partir de la estructura MADR del ADR (marcadas **auto**), comprueba en vivo que
cada evidencia sea literal y valida el grafo al guardar. Selecciona texto en el
ADR y pulsa una tecla para crear una fila. `Ctrl+Enter` = terminar y siguiente.

**Tipos de fila (`elemento`) de la v1.1** — además de estado, decisión,
alternativa, driver, tema y relación (§3):

| elemento | Sección MADR | Columnas propias |
|---|---|---|
| `contexto` | Context and Problem Statement | – (basta el primer párrafo) |
| `problema` | la pregunta o el problema, si se enuncia | – |
| `justificacion` | el «because…» de Decision Outcome, o Rationale | – |
| `consecuencia` | Consequences (una por punto) | `polaridad`: positiva, negativa, neutra |
| `argumento` | Pros and Cons of the Options | `polaridad` y `alternativa` (evidencia de la alternativa) |
| `confirmacion` | Confirmation | – |
| `participante` | decision-makers / deciders, consulted, informed | `rol`: decision-maker, consulted, informed |
| `info_adicional` | More Information, Links, References | – |
| `fecha` | campo `date` del ADR (última actualización) | `valor` AAAA-MM-DD; no es la fecha de registro |

`relacion` admite ahora `relatesTo`: el ADR menciona a otro como relacionado,
sin reemplazarlo ni enmendarlo. Solo con enlace o identificador explícito.

**Qué se evalúa:** solo decisión, alternativas, estado, tema y relaciones
alimentan consultas con respuesta única. Los demás elementos se anotan para que
el grafo cubra MADR y cuentan en la tasa de nodos no fundamentados. Si un ADR
no documenta un elemento, no se crea: solo la decisión es obligatoria.

Las secciones 1–6 siguen valiendo para los elementos de la v1.0. Donde dicen
«abre anotaciones.csv en Excel», usa la herramienta.

## 1. Flujo de trabajo

```
1. Abre anotaciones.csv (Excel o LibreOffice; separador «;», UTF-8)
2. Para cada ADR, abre su fichero en adr/<repo con __>/<archivo> y rellena sus filas
3. Cada cierto tiempo:  python csv_a_grafo.py
4. Corrige lo que indique y sigue
```

`csv_a_grafo.py` valida la hoja fila a fila, construye el grafo con el esquema,
lo pasa por el validador de conformidad y **comprueba que cada evidencia
aparezca literalmente en su ADR**. Una errata en una evidencia aparece como
`no_fundamentado`. Ejecútalo a menudo: es más barato corregir cinco filas que
cuarenta y cinco.

Anota **por cadena** (columna `cadena`): así pones la etiqueta de tema una sola
vez y las relaciones quedan coherentes. Los ADR sueltos van al final.

> ⚠️ `generar_plantilla.py` no sobrescribe una hoja existente. Solo con
> `--forzar`, y eso **borra lo anotado**.

## 2. Columnas

Hay una sola hoja para seis tipos de elemento. Cada columna sirve a un tipo, así
que en cada fila solo llenas una a tres columnas; el resto queda vacío.

| Columna | Qué va |
|---|---|
| `repo`, `archivo`, `cadena` | Precargadas; no se tocan |
| `elemento` | `estado`, `decision`, `alternativa`, `driver`, `tema` o `relacion` |
| `evidencia` | **Fragmento copiado literalmente** del ADR: una línea o una frase |
| `valor` | Forma limpia del elemento, si difiere de la evidencia. Vacío = se usa la evidencia |
| `elegida` | Solo en `alternativa`: `1` la adoptada, `0` las demás |
| `estado_normalizado` | Solo en `estado`: ver §4 |
| `tipo_relacion` | Solo en `relacion`: `supersedes` o `amends` |
| `adr_nuevo`, `adr_afectado` | Solo en `relacion`: nombres de fichero del mismo repositorio |
| `nota` | Libre: dudas, anomalías y decisiones que tomaste |

Qué se llena en cada tipo de fila:

| elemento | evidencia | valor | otras |
|---|---|---|---|
| `estado` | sí | – | `estado_normalizado` |
| `decision` | sí | **sí**, siempre | – |
| `alternativa` | sí | si difiere | `elegida` |
| `driver` | **precargada**: revisar | si la evidencia es larga | – |
| `tema` | – | **sí**, siempre | – |
| `relacion` | sí | – | precargadas: `tipo_relacion`, `adr_nuevo`, `adr_afectado` |

Se pueden añadir filas (una por alternativa, por ejemplo) y borrar las que no
apliquen. El orden no importa.

## 3. Reglas por elemento

### Decisión (una por ADR, obligatoria)
- **Evidencia:** la frase que enuncia la decisión (`Chosen option: …`, `We will …`,
  `Decision: …`). Si no hay una frase explícita, la primera frase de la sección
  de decisión.
- **Valor:** la decisión reformulada en una línea («Use Contentful as the CMS for
  content storage»). Es lo que se compara al medir el F1 de decisiones.
- Todo ADR tiene decisión, aunque no liste alternativas.
- **Decisión que adopta varias cosas a la vez** («Explore both platforms»,
  «Explore both sections»): se anota como **una sola decisión**. Si lo que se
  adopta está en el contexto y no en una lista de opciones, no hay alternativas.
  En `valor` nombra lo adoptado («Explore both Koshvani sections (Expenditure and
  Receipts) …») y explícalo en `nota`. Nunca marques dos alternativas con
  `elegida = 1`: `selectsAlternative` es funcional y el conversor da error.

### Alternativas (cero o más)
- Una fila por **opción listada** en la sección de opciones consideradas
  (`Considered Options`, `Options`, `Alternatives` o equivalente).
- **Si el ADR no lista opciones, no se inventan** alternativas: se deja sin filas.
  Una decisión sin alternativas es normal. Elementos descritos en el contexto
  (plataformas, secciones, herramientas existentes) no son opciones.
- `elegida = 1` en la opción adoptada. Como mucho una.
- **Regla de la opción no listada:** si la opción elegida no figura entre las
  listadas (p. ej. `Chosen option: Use Hotjar` sin «Hotjar» en la lista), se
  **añade** como alternativa con `elegida = 1` y con la frase de la elección como
  evidencia. Anótalo en `nota`.
- La columna `alternativas_explicitas` del manifiesto es una pista heurística,
  no una verdad: manda lo que dice el texto.

### Criterios de decisión, `driver` (cero o más) · nuevo en v1.1
- Una fila por **criterio listado** en la sección `Decision Drivers` (o un
  encabezado equivalente: `Drivers`, `Decision Criteria`, `Forces`).
- **Vienen precargados** en los 11 ADR que tienen esa sección: la evidencia es
  el texto del punto de la lista (o su primera frase, si es largo). Revisa que
  cada fila sea un criterio y no otra cosa; borra la que no lo sea.
- **Valor:** vacío si el criterio es corto. Si es largo o la evidencia es solo
  su primera frase, escribe el criterio en una línea («Avoid the IETF draft
  process»).
- **No se deducen:** si el ADR no tiene sección de criterios, no hay filas
  `driver`, aunque el contexto o el «because …» de la decisión expliquen el
  porqué. Así el criterio de anotación es objetivo.
- Los drivers no generan consultas de evaluación (no tienen una respuesta única);
  sí cuentan en la tasa de nodos no fundamentados, como todo elemento con
  evidencia.

### Estado (uno por ADR, si existe)
- **Evidencia:** el valor del campo de estado **tal cual**, sin el rótulo
  «Status:» (`accepted, superseded by [ADR-0011](0011-contentful.md)`,
  `[deprecated] | [superseded] by [ADR-004]`).
- Si el ADR no tiene campo de estado (9 de los 140 no lo tienen), **borra la fila**.
  No se deduce.

### Tema (uno por ADR)
- Etiqueta corta en inglés, en minúsculas (`content storage`, `user journey tracking`).
- **Todos los ADR de una misma cadena (columna `cadena`) llevan exactamente la
  misma etiqueta.** El conversor avisa si dos ADR de una relación de reemplazo
  no comparten tema.
- No lleva evidencia: el tema se asigna, no se extrae, y no cuenta en la tasa de
  nodos no fundamentados.

### Relación (una fila por documento que la declara)
- **Precargadas** del ground truth: solo falta pegar la evidencia
  (`superseded by [ADR-0023](…)` o `supersedes [ADR-0018](…)`).
- La fila va en el ADR que **hace la afirmación**. Si los dos ADR la declaran,
  hay dos filas (una en cada uno), y eso ya viene precargado.
- **Dirección:** `adr_nuevo` es el que reemplaza o enmienda; `adr_afectado` es el
  reemplazado o enmendado. Da igual si el texto dice «superseded by» o «supersedes».
- `supersedes` si la decisión anterior deja de valer (superseded, replaced,
  obsoleted by); `amends` si sigue valiendo con cambios (amends, amended by, extends).
- **Solo con referencia explícita (regla O1 de la planificación v2).** Cuenta un
  enlace markdown o la mención del identificador (`[ADR-004]`). Si un ADR
  contradice a otro sin nombrarlo, **no** es una relación: anótalo en `nota`
  como «contradicción factual», porque se reporta aparte en el bloque 3.
- Si encuentras una relación que el ground truth no tiene, **añádela** y
  explícala en `nota`. Si una precargada no se sostiene al leer el texto, no la
  borres: deja la evidencia vacía y explícalo en `nota`.

### Lo que no se anota
- **Fechas.** `recordDate` no sale del texto: el campo `Date` de MADR es opcional
  y marca la última edición. La fecha se obtiene del historial de git con la
  regla temporal (E1: commit del enlace de reemplazo; E2: commit de creación).
- Participantes (`Deciders`), contexto, consecuencias, pros y contras.

## 4. Normalización del estado

| Literal en el ADR | `estado_normalizado` |
|---|---|
| accepted, approved, implemented, adopted, done, completed | `accepted` |
| proposed, draft, in progress, open, pending, under review | `proposed` |
| rejected, declined, not chosen | `rejected` |
| deprecated, obsolete, retired | `deprecated` |
| superseded, superceded, replaced, «superseded by X», «accepted, superseded by X» | `superseded` |
| amended, «amended by X» | `amended` |
| «accepted, supersedes X» (el que reemplaza) | `accepted` |
| «deprecated \| superseded by X» | `superseded` |

Regla general: **gana la parte que describe el estado actual del propio ADR.**
«accepted, superseded by X» → `superseded`, porque fue aceptado y después
reemplazado. Si el literal combina deprecated y superseded, gana `superseded`,
porque nombra al reemplazante y es coherente con la relación. Si un literal no
encaja en la tabla, elige la fila más cercana y explícalo en `nota`.

## 5. Ejemplo resuelto

`DFE-Digital/early-years-foundation-recovery`, ADR 0006 y 0011, ya anotados en
la hoja (nota «ejemplo resuelto: revisar»):

| archivo | elemento | evidencia (resumida) | valor / otros |
|---|---|---|---|
| 0006 | estado | `accepted, superseded by [ADR-0011](0011-contentful.md)` | `superseded` |
| 0006 | decision | `Chosen option: Create content as a mix of YAML and Markdown…` | valor: la opción sin «Chosen option» |
| 0006 | alternativa ×4 | las cuatro opciones listadas | la tercera, `elegida = 1` |
| 0006 | driver ×4 | los cuatro puntos de `Decision Drivers` (precargados) | – |
| 0006 | tema | – | `content storage` |
| 0006 | relacion | `superseded by [ADR-0011](0011-contentful.md)` | supersedes, nuevo 0011, afectado 0006 |
| 0011 | estado | `accepted` | `accepted` |
| 0011 | decision | `Contentful is the mandated CMS for DfE projects.` | valor: `Use Contentful as the CMS for content storage` |
| 0011 | driver ×3 | los tres puntos de `Decision Drivers` (precargados) | – |
| 0011 | tema | – | `content storage` (misma cadena C02) |

0011 no declara la relación con 0006: por eso solo hay una fila de relación, y
la CQ5 la reporta como «declarada solo por un extremo», igual que el ground truth.

## 6. Criterios de calidad

- **Literal quiere decir literal.** La evidencia se compara con el fichero (los
  espacios se normalizan). No corrijas erratas del original («techinical»,
  «depending to»).
- Ante la duda, **anota lo que el texto dice, no lo que crees que quiso decir**, y
  usa `nota`.
- Ritmo orientativo: 9–10 ADR al día; son 43 pendientes, así que unos 5 días.
- **Test-retest (un solo anotador).** Al terminar, deja pasar unos días y vuelve
  a anotar a ciegas unos 9 ADR (~20 %) en una hoja aparte. El acuerdo entre las
  dos pasadas (kappa de Cohen) se reporta en la tesis.
