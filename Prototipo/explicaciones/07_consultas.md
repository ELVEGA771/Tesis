# `Ontologia/outputs/esquema_v1_1/consultas.py` — las CQ1–8 sobre el grafo

**Qué es en la tesis.** Las *competency questions* (CQ) son las preguntas que el esquema tiene que poder responder; la v1.0 y la v1.1 se derivaron de ellas con CQbyCQ. Las CQ1–8 salen de las **métricas de la planificación** y son las únicas que se evalúan con consultas de respuesta única. Este archivo las convierte en **funciones Python** que recorren el grafo y devuelven **datos** (no texto). Tiene dos usos:

1. **Respuestas esperadas (A4):** aplicadas al **gold**, generan la respuesta correcta de cada pregunta del protocolo (lo que pide el punto 3 de la retroalimentación del 05-10).
2. **Condición grafo (A8):** aplicadas al grafo **del pipeline**, muestran qué responde el grafo construido automáticamente.

Es el port de `esquema_v1/consultas.py` (v1.0). Las preguntas son las mismas; solo cambian los nombres de clases y propiedades de la v1.1.

---

## 1. Docstring (líneas 1–19)

Además de describir el módulo, documenta:
- **la tabla de cambios de nombres v1.0 → v1.1**, útil para la subsección «Evolución del esquema» del documento;
- **la regla temporal (O1)**: la vigencia la fija `registrationDate` (fecha del primer commit, de `fechas_git.py`); `lastUpdateDate` (campo `date` de MADR) es de última edición y no ordena nada; si falta una fecha la respuesta es «indeterminado», nunca una suposición.

## 2. Constante y utilidades (líneas 20–45)

```python
from __future__ import annotations
import networkx as nx

FECHA = "registrationDate"
```

`FECHA` concentra en un solo sitio qué propiedad fija la vigencia. Si algún día quisieras comparar con la fecha del texto (por ejemplo, como análisis de sensibilidad para O1), basta con cambiar esta constante.

```python
def _out(G, n, rel):
    return [v for _, v, d in G.out_edges(n, data=True) if d.get("relation") == rel]

def _in(G, n, rel):
    return [u for u, _, d in G.in_edges(n, data=True) if d.get("relation") == rel]

def _a(G, n, k):
    return G.nodes[n].get(k)
```

Tres utilidades que usan todas las consultas:
- `_out`: «¿a qué nodos apunta `n` por la relación `rel`?» (ADR → su decisión).
- `_in`: «¿qué nodos apuntan a `n` por `rel`?» (tema ← ADR que lo tratan).
- `_a`: el valor de un atributo de un nodo, o `None`.

En el grafo cada arista guarda su tipo en el atributo `relation`. Como es un **multigrafo**, entre dos nodos puede haber varias aristas de tipos distintos (por ejemplo, `considersAlternative` y `selectsAlternative` del ADR a la misma alternativa), y por eso se filtra por `relation`.

```python
def adrs(G):
    return sorted(n for n, d in G.nodes(data=True) if d.get("type") == "ADR" and d.get("fileName"))

def temas(G):
    return sorted(n for n, d in G.nodes(data=True) if d.get("type") == "Topic")
```

Listas ordenadas de todos los ADR y temas del grafo. Se exige `fileName` para no confundir el **nodo de clase** `ADR` del esquema (que también está en el grafo, con `type="Class"`) con los individuos.

## 3. Bloque 1

### CQ1 — ¿Qué decisión registra un ADR y a qué repositorio pertenece?

```python
def cq1_decision(G, adr):
    d = _out(G, adr, "hasDecisionStatement")
    r = _out(G, adr, "belongsToRepository")
    return {"decision": _a(G, d[0], "decisionSummary") if d else None,
            "repositorio": _a(G, r[0], "repositoryName") if r else None}
```

- Sigue la arista ADR → `DecisionStatement` y lee su resumen.
- Sigue ADR → `Repository` y lee el nombre.
- `d[0]`: como `hasDecisionStatement` es **funcional** (C3), hay como máximo una; si hubiera más, el validador ya lo habría marcado como error.
- Si no hay decisión, devuelve `None` en vez de fallar: una pregunta sin respuesta en el grafo es un resultado válido (abstención), no un error de programa.

### CQ2 — ¿Qué alternativas se consideraron y cuál se adoptó?

```python
def cq2_alternativas(G, adr):
    elegida = set(_out(G, adr, "selectsAlternative"))
    return [{"alternativa": _a(G, x, "alternativeName"), "elegida": x in elegida}
            for x in _out(G, adr, "considersAlternative")]
```

- `considersAlternative`: todas las opciones; `selectsAlternative`: la adoptada.
- Devuelve una lista con cada alternativa y si es la elegida. Es la base de la métrica del bloque 2 «recuperación del conjunto de alternativas» (P/R/F1 sobre conjuntos).
- Si el ADR no lista opciones, devuelve `[]` (regla (b) de la guía: sin lista no hay alternativas). Esa es la respuesta correcta para las preguntas de abstención.

## 4. Bloque 2

### CQ3 — ¿Qué estado declara, con qué literal, normalizado y en qué fecha?

```python
def cq3_estado(G, adr):
    s = _out(G, adr, "hasStatusStatement")
    norm = _out(G, s[0], "hasNormalizedStatus") if s else []
    return {"literal": _a(G, s[0], "declaredStatusLiteral") if s else None,
            "normalizado": norm[0].removeprefix("status_") if norm else None,
            "fecha_registro": _a(G, adr, FECHA),
            "fecha_actualizacion": _a(G, adr, "lastUpdateDate")}
```

- ADR → `StatusStatement` → individuo `status_*`. Se quita el prefijo para devolver `"superseded"` en vez de `"status_superseded"`.
- Devuelve **las dos fechas por separado**, que es lo que pide distinguir la retroalimentación: la de registro (git) y la de actualización (texto).

### Auxiliar: relaciones declaradas

```python
def _declaraciones(G, tipo):
    """{(nuevo, afectado): {ADR que lo declaran}} para un tipo de relacion."""
    pares: dict[tuple[str, str], set[str]] = {}
    for n, d in G.nodes(data=True):
        if d.get("type") != "RelationshipStatement" or _out(G, n, "relationType") != [tipo]:
            continue
        src, dst = _in(G, n, "sourceADR"), _in(G, n, "targetADR")
        if src and dst:
            pares.setdefault((src[0], dst[0]), set()).update(_in(G, n, "declaringADR"))
    return pares
```

**El patrón «pivote».** Una relación entre ADR no es una arista directa, sino un **nodo** `RelationshipStatement` con aristas a su origen, su destino, su declarante y su tipo. ¿Por qué? Porque la misma relación puede afirmarla **un documento o los dos** («0006: superseded by 0011» y «0011: supersedes 0006» son dos declaraciones con dos evidencias distintas), y cada declaración tiene su propia evidencia. Una arista simple no puede guardar eso.

Esta función agrupa las declaraciones por par:
- Filtra los nodos de relación del tipo pedido (`reltype_supersedes`, `reltype_amends`).
- `src` / `dst`: el ADR nuevo y el afectado (aristas **entrantes** al pivote).
- `setdefault(...).update(...)`: para el par `(nuevo, afectado)`, acumula en un conjunto los ADR que lo declaran.
- Resultado: `{("adr_…0011", "adr_…0006"): {"adr_…0006"}}` → solo el 0006 lo declara.

### CQ7 — ¿En qué estado estaba la decisión de un ADR en la fecha F?

```python
def cq7_estado_en_fecha(G, adr, fecha: str):
    propia = _a(G, adr, FECHA)
    if not propia:
        return "indeterminado (sin fecha)"
    if fecha < propia:
        return "no existia"
```

- Las fechas son texto `AAAA-MM-DD`, que se puede comparar con `<` como texto y da el orden cronológico correcto (por eso se usa ese formato).
- Sin fecha de registro, la respuesta honesta es «indeterminado» (lo que pide O1 cuando la fecha no puede confirmarse). Con `fechas_git.py`, los 140 ADR tienen fecha, así que este caso queda solo para grafos sin ese dato.
- Antes de su registro, el ADR no existía.

```python
    base = cq3_estado(G, adr)["normalizado"] or "desconocido"
    if base in ("superseded", "amended"):
        base = "accepted"
```

**Reconstrucción del estado anterior.** El estado declarado hoy es el **final**. Si hoy dice `superseded`, antes del reemplazo estaba vigente: se asume `accepted`. Es una suposición que hay que declarar en la tesis (podría haber pasado por `proposed`), pero es la única información disponible en el texto.

```python
    for (nuevo, afectado) in _declaraciones(G, "reltype_supersedes"):
        if afectado != adr:
            continue
        f = _a(G, nuevo, FECHA)
        if f is None:
            return "indeterminado (el ADR que lo reemplaza no tiene fecha)"
        if f <= fecha:
            return "superseded"
    return base
```

- Busca los ADR que reemplazan a este.
- **Regla temporal:** el reemplazo ocurre en la **fecha de registro del ADR nuevo**. Si esa fecha es anterior o igual a F, en F ya estaba `superseded`.
- Si el reemplazante no tiene fecha, «indeterminado».
- Si ninguno lo reemplazó antes de F, devuelve el estado base.
- **Caso de las importaciones en bloque** (5 pares que entraron en el mismo commit): el reemplazado y el reemplazante tienen la **misma** fecha, y la función responde `superseded` desde ese mismo día. No es incorrecto, pero el instante real es desconocido; por eso el protocolo **no generará preguntas** de CQ7 con F igual a esa fecha. Esa exclusión se hará en `respuestas_esperadas.py`, no aquí.

## 5. Bloque 3

### CQ4 — ¿Sobre qué tema trata y qué otros ADR lo tratan?

```python
def cq4_tema(G, adr):
    out = []
    for t in _out(G, adr, "hasTopic"):
        otros = sorted(x for x in _in(G, t, "hasTopic") if x != adr)
        out.append({"tema": _a(G, t, "topicName"), "otros_adr": otros})
    return out
```

Del ADR a su tema, y del tema a todos los ADR que lo tratan, excepto el propio. El tema es **asignado** (lo pone el anotador; el pipeline aún no lo asigna), no extraído. Agrupa los ADR de una misma cadena y es la base de la CQ8.

### CQ5 — ¿Qué ADR reemplaza a cuál? ¿Lo declaran los dos o solo uno?

```python
def cq5_reemplazos(G):
    return [{"nuevo": n, "reemplazado": a, "declarado_por": sorted(w),
             "bidireccional": {n, a} <= w}
            for (n, a), w in sorted(_declaraciones(G, "reltype_supersedes").items())]
```

- Una entrada por par de reemplazo.
- `{n, a} <= w`: «¿el conjunto {nuevo, afectado} está contenido en los declarantes?», es decir, ¿lo declaran los dos? En el gold, CQ5 da 15 pares, los mismos 15 del `ground_truth_reemplazos.csv` dentro de los 45. Esa coincidencia valida la anotación de relaciones.
- Base de la métrica del bloque 3 «detección de la relación de reemplazo entre pares (F1)».

### CQ6 — ¿Qué ADR enmienda a otro sin reemplazarlo?

```python
def cq6_enmiendas(G):
    sup = _declaraciones(G, "reltype_supersedes")
    return [{"enmienda": n, "enmendado": a, "sigue_vigente": not any(x == a for (_, x) in sup)}
            for (n, a) in sorted(_declaraciones(G, "reltype_amends"))]
```

- Lista los pares `amends` e indica si el enmendado **sigue vigente** (no fue reemplazado por nadie).
- Existe para que el bloque 3 no cuente una enmienda como reemplazo (evita falsos positivos).
- **En los 45 ADR anotados hay 0 enmiendas** (los 7 ADR de enmiendas del corpus quedaron fuera del subconjunto). Hay que reconocerlo en la tesis: la CQ6 no se puede evaluar con el gold actual.

### CQ8 — ¿Cuál es la decisión vigente sobre un tema y por qué cadena se llega?

```python
def cq8_vigente(G, topic):
    miembros = set(_in(G, topic, "hasTopic"))
    sup = [(n, a) for (n, a) in _declaraciones(G, "reltype_supersedes")
           if n in miembros and a in miembros]
```

- `miembros`: los ADR del tema.
- `sup`: los reemplazos **dentro** del tema (se ignoran los que conectan con ADR de otros temas).

```python
    H = nx.DiGraph()
    H.add_nodes_from(miembros)
    H.add_edges_from((a, n) for n, a in sup)          # arista: viejo -> nuevo
    reemplazados = {a for _, a in sup}
    vig = sorted(miembros - reemplazados)
```

- Construye un pequeño grafo dirigido viejo → nuevo con los ADR del tema.
- **Vigente = miembro del tema que nadie reemplazó.** Es una diferencia de conjuntos.

```python
    try:
        cadena = list(nx.topological_sort(H))
    except nx.NetworkXUnfeasible:
        cadena = None                                  # ciclo: anomalia
```

- El **orden topológico** pone cada ADR antes de los que lo reemplazan: es la cadena de la más antigua a la vigente.
- Si hay un **ciclo** (A reemplaza a B y B reemplaza a A), no existe orden: se devuelve `None` como anomalía en vez de fallar. Puede ocurrir en el grafo del pipeline si el modelo invierte una dirección.

```python
    return {"tema": _a(G, topic, "topicName"),
            "vigentes": [{"adr": v, "decision": cq1_decision(G, v)["decision"]} for v in vig],
            "cadena": cadena,
            "conflicto": len(vig) != 1}
```

- `conflicto`: hay **más de una** decisión vigente (o ninguna). Es la **contradicción factual** de la observación O1: dos ADR del mismo tema activos sin que uno declare reemplazar al otro. El sistema **la reporta, no la resuelve**; decidir cuál es la vigente exigiría inventar una relación que el texto no declara. En el gold: 26 de 26 temas sin conflicto.

## 6. Carga y prueba rápida (líneas al final)

```python
def cargar(ruta):
    import json
    from networkx.readwrite import json_graph
    data = json.load(open(ruta, encoding="utf-8"))
    return json_graph.node_link_graph(data, directed=True, multigraph=True, edges="links")
```

Lee un grafo node-link (el gold o uno del pipeline).

```python
if __name__ == "__main__":
    ...
    G = cargar(sys.argv[1] if len(sys.argv) > 1 else gold)
```

Sin argumentos usa el gold. `parents[3]` sube cuatro niveles desde `esquema_v1_1/` hasta `Tesis_herramientas/`. Imprime un resumen de cuántos ADR responden cada CQ y lista los temas en conflicto. Salida actual con el gold:

```
45 ADR, 26 temas
CQ1 con decision: 43
CQ2 con alternativas: 19
CQ3 con estado: 39 | con fecha de registro: 43
CQ5 pares de reemplazo: 15
CQ6 enmiendas: 0
CQ8 temas con una sola vigente: 26 / 26
```

Se puede pasar un grafo del pipeline como argumento para comparar a ojo, aunque los grafos del pipeline son uno por ADR; para A8 habrá que unirlos en un único grafo del corpus.
