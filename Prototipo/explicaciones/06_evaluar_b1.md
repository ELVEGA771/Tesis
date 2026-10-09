# `Prototipo/evaluar_b1.py` — bloque 1: fidelidad de construcción

**Qué mide.** La planificación (§5, bloque 1, solo para la etapa multiagente): precisión, exhaustividad y F1 de los nodos de **alternativas y decisiones** extraídos frente al subconjunto anotado a mano, y la **tasa de nodos no fundamentados** (proporción de nodos cuyo contenido no se puede rastrear a un fragmento del ADR de origen). Además calcula tres métricas complementarias que salen gratis: alternativa elegida, estado normalizado y relaciones.

**Uso:**
```
python evaluar_b1.py --particion desarrollo
python evaluar_b1.py --particion prueba      # solo cuando el pipeline esté congelado
```

**Repaso de P, R y F1:**
- **Precisión (P)** = aciertos / lo que predijo el sistema. «De lo que extrajo, ¿cuánto estaba bien?» Baja si inventa.
- **Exhaustividad (R)** = aciertos / lo que hay en el gold. «De lo que había, ¿cuánto encontró?» Baja si omite.
- **F1** = 2·P·R / (P + R), la media armónica. Solo es alta si ambas lo son.

Lo delicado no es la fórmula, sino **decidir cuándo un nodo predicho «es» un nodo del gold**: el modelo y el anotador no escriben exactamente lo mismo. Ese criterio de emparejamiento es lo que hay que justificar en la tesis.

---

## 1. Importaciones (líneas 12–25)

```python
import networkx as nx
from comun import CORPUS, DATOS, particion, texto_adr

sys.path.insert(0, str(CORPUS / "anotacion"))
from csv_a_grafo import adr_id as nodo_adr  # noqa: E402
from conformidad import comprobar  # noqa: E402
```

Las mismas piezas que el pipeline: el id de nodo de `csv_a_grafo` y el validador. La tasa de no fundamentados se calcula **con el mismo validador** que usa el pipeline, para que «fundamentado» signifique lo mismo en los dos sitios.

## 2. Utilidades de grafo (líneas 28–33)

```python
def _g(data):
    return nx.node_link_graph(data, directed=True, multigraph=True, edges="links")
```

Convierte el JSON node-link (gold o pipeline) en grafo NetworkX.

```python
def _out(G, n, rel):
    return [v for _, v, d in G.out_edges(n, data=True) if d.get("relation") == rel] if n in G else []
```

Los vecinos de salida de `n` por una relación concreta (por ejemplo, las alternativas de un ADR por `considersAlternative`). `if n in G else []` evita un error si el nodo no existe, por ejemplo si el pipeline no llegó a crear el ADR.

## 3. Localizar una evidencia en el texto (líneas 36–40)

```python
def _span(texto, ev):
    ev = re.sub(r"\s+", " ", ev or "").strip()
    t = re.sub(r"\s+", " ", texto)
    i = t.find(ev) if ev else -1
    return (i, i + len(ev)) if i >= 0 else None
```

- Normaliza los espacios en blanco (saltos de línea, tabuladores, espacios dobles → un espacio) en la evidencia y en el texto. Así una evidencia que el modelo copió uniendo dos líneas sigue encontrándose.
- `find` devuelve la posición del primer carácter, o −1 si no está.
- Devuelve el intervalo `(inicio, fin)` en caracteres, o `None`.
- **Diferencia con el validador (anotada en el índice):** aquí **sí** se distinguen mayúsculas; `conformidad.py` no. Una evidencia que solo difiera en mayúsculas cuenta como fundamentada, pero `_span` no la ubica y no podrá emparejarse por solape. Arreglo sencillo: aplicar `.lower()` a `ev` y a `t`.
- **Si la evidencia aparece varias veces**, se toma la primera aparición. Con fragmentos cortos y repetidos (un nombre de opción) esto puede ubicar la evidencia en un sitio distinto al que pensaba el anotador. Por eso las alternativas también se emparejan por nombre (sección 6).

## 4. ¿Se solapan dos evidencias? (líneas 43–48)

```python
def _solapa(texto, a, b):
    sa, sb = _span(texto, a), _span(texto, b)
    if not sa or not sb:
        return False
    inter = min(sa[1], sb[1]) - max(sa[0], sb[0])
    return inter > 0 and inter >= 0.5 * min(sa[1] - sa[0], sb[1] - sb[0])
```

**El criterio central de emparejamiento.** Como pipeline y anotador copian fragmentos **literales del mismo documento**, se pueden comparar por **posición** y no por parecido de palabras:
- `inter`: cuántos caracteres comparten los dos intervalos. Si es ≤ 0, no se tocan.
- Se exige que el solape cubra al menos **el 50 % del fragmento más corto**.

**Por qué así:** el anotador puede haber marcado «We will use Contentful CMS for content storage» y el modelo «We will use Contentful CMS for content storage. It is the mandated…». No son iguales, pero señalan **el mismo lugar** del documento. Tomar el 50 % del más corto acepta que uno contenga al otro y rechaza coincidencias marginales (dos frases contiguas que solo comparten un trozo). El umbral es una decisión que hay que declarar; si el tribunal lo pregunta, se puede mostrar la sensibilidad con 30 % y 70 %.

## 5. Normalizar nombres y emparejar (líneas 51–62)

```python
def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
```

Minúsculas y todo lo que no sea letra o número convertido en espacio: «Ruby-on-Rails» → «ruby on rails». Así diferencias de guiones o puntuación no impiden emparejar.

```python
def _empareja(pred, gold, igual):
    usados, tp = set(), 0
    for p in pred:
        for j, g in enumerate(gold):
            if j not in usados and igual(p, g):
                usados.add(j)
                tp += 1
                break
    return tp
```

**Emparejamiento uno a uno, voraz:**
- Recorre los nodos predichos; a cada uno le asigna el **primer** nodo del gold compatible que no esté ya usado.
- `usados` impide que dos predicciones cuenten contra el mismo nodo del gold. Si el modelo extrae dos veces la misma decisión, solo una es acierto y la otra baja la precisión, que es lo correcto.
- Devuelve los **verdaderos positivos** (`tp`).
- **Limitación:** voraz no siempre da el emparejamiento máximo (eso lo haría el algoritmo húngaro). Con 1–5 nodos por ADR y criterios bastante estrictos, la diferencia es despreciable; se puede declarar.

```python
def prf(tp, np_, ng):
    p = tp / np_ if np_ else (1.0 if not ng else 0.0)
    r = tp / ng if ng else 1.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)
```

- `np_`: número de predicciones; `ng`: número de nodos del gold.
- Casos borde (para no dividir entre cero):
  - Sin predicciones y sin gold → P = 1 (no inventó nada).
  - Sin predicciones pero con gold → P = 0.
  - Sin gold → R = 1 (no había nada que encontrar).
- Se calcula sobre los **totales** de todos los ADR (micro-promedio): un ADR con 5 alternativas pesa más que uno con 1. Es lo habitual en extracción de información y hay que decirlo en la tesis.

## 6. Programa principal (líneas 65–137)

```python
    gold = _g(json.load(open(CORPUS / "anotacion" / "gold_anotado.json", encoding="utf-8")))
    part = particion()
    acc = {k: [0, 0, 0] for k in ("decision", "alternativa", "relacion")}
    elegida_ok = elegida_n = estado_ok = estado_n = 0
    fund_ok = fund_tot = 0
    filas = []
```

- Carga el gold **regenerado por `csv_a_grafo.py`**: refleja la hoja en el momento en que se ejecutó por última vez. Si sigues anotando, hay que volver a correr `csv_a_grafo.py` antes de evaluar.
- `acc`: acumuladores `[tp, predichos, gold]` por tipo de nodo.
- Contadores de las métricas de exactitud y de fundamentación.

```python
    for clave, p in part.items():
        repo, archivo = clave.split("|")
        a = nodo_adr(repo, archivo)
        if p != args.particion or not _out(gold, a, "hasDecisionStatement"):
            continue
```

Solo se evalúan los ADR que (1) están en la partición pedida y (2) tienen **decisión anotada** en el gold, que es el criterio de «ADR anotado» que usa `csv_a_grafo.py`. Los no anotados no tienen referencia contra la que medir. Por eso en desarrollo se evalúan 9 y no 10: `remix-run/react-router 0016-plan-remove-agnostic-types.md` es uno de los 2 ADR que todavía no has empezado a anotar. Cuando lo anotes y regeneres el gold, entrará solo.

```python
        f = DATOS / "grafos" / args.particion / f"{a}.json"
        if not f.exists():
            print("sin corrida:", clave)
            continue
        P = _g(json.load(open(f, encoding="utf-8"))["grafo"])
        texto = texto_adr(repo, archivo)
        ev = lambda G, n: G.nodes[n].get("evidenceText", "")  # noqa: E731
```

- Si el pipeline no procesó el ADR, lo avisa y lo salta. **Ojo:** saltarlo no penaliza. Para el resultado final de prueba hay que comprobar que no aparezca ningún «sin corrida»; si no, se estaría evaluando sobre menos ADR sin darse cuenta.
- `P`: el grafo del pipeline para ese ADR.
- `ev`: atajo para leer la evidencia de un nodo.

### Decisiones

```python
        dp, dg = _out(P, a, "hasDecisionStatement"), _out(gold, a, "hasDecisionStatement")
        tp = _empareja(dp, dg, lambda x, y: _solapa(texto, ev(P, x), ev(gold, y)))
        acc["decision"] = [acc["decision"][0] + tp, acc["decision"][1] + len(dp), acc["decision"][2] + len(dg)]
```

Una decisión predicha acierta si su evidencia se solapa ≥ 50 % con la del gold. Se compara la **evidencia**, no el resumen: el resumen lo redacta cada uno a su manera, mientras que la evidencia es literal y comparable.

### Alternativas

```python
        ap_, ag = _out(P, a, "considersAlternative"), _out(gold, a, "considersAlternative")

        def alt_igual(x, y):
            nx_, ny = _norm(P.nodes[x].get("alternativeName")), _norm(gold.nodes[y].get("alternativeName"))
            return (nx_ and ny and (nx_ == ny or nx_ in ny or ny in nx_)) or _solapa(texto, ev(P, x), ev(gold, y))
        tp = _empareja(ap_, ag, alt_igual)
```

Una alternativa acierta si se cumple **cualquiera** de estas condiciones:
1. Mismo nombre normalizado («Ruby on Rails» = «Ruby-on-Rails»).
2. Uno contenido en el otro («Contentful» ⊂ «Contentful CMS»).
3. Sus evidencias se solapan.

El nombre es la señal principal porque una alternativa es esencialmente su nombre, y su evidencia suele ser muy corta (aparece varias veces en el texto, lo que hace frágil el solape). La variable se llama `nx_` para no tapar el módulo `nx` de networkx.

### Alternativa elegida

```python
        sg, sp = _out(gold, a, "selectsAlternative"), _out(P, a, "selectsAlternative")
        if sg:
            elegida_n += 1
            elegida_ok += bool(sp) and alt_igual(sp[0], sg[0])
```

Solo cuenta los ADR donde el gold marca una elegida. Acierta si el pipeline marcó una y es «la misma» según `alt_igual`. (`True` suma 1 y `False` suma 0.) Corresponde a la segunda mitad de la CQ2.

### Estado

```python
        eg = _out(gold, a, "hasStatusStatement")
        if eg:
            estado_n += 1
            ep = _out(P, a, "hasStatusStatement")
            estado_ok += bool(ep) and _out(P, ep[0], "hasNormalizedStatus") == _out(gold, eg[0], "hasNormalizedStatus")
```

Exactitud del estado **normalizado** (CQ3), comparando el individuo del vocabulario (`status_accepted`…). El literal no se compara: es texto y ya lo cubre la fundamentación.

### Relaciones

```python
        def rels(G):
            out = set()
            for n in _out(G, a, "declaringADR"):
                src = [u for u, _, d in G.in_edges(n, data=True) if d.get("relation") == "sourceADR"]
                dst = [u for u, _, d in G.in_edges(n, data=True) if d.get("relation") == "targetADR"]
                out.add((tuple(_out(G, n, "relationType")), tuple(src), tuple(dst)))
            return out
        rp, rg = rels(P), rels(gold)
        acc["relacion"] = [acc["relacion"][0] + len(rp & rg), acc["relacion"][1] + len(rp), acc["relacion"][2] + len(rg)]
```

- Recorre las relaciones que **este ADR declara** (`declaringADR`) y representa cada una como `(tipo, origen, destino)`.
- `sourceADR` y `targetADR` son aristas **entrantes** al nodo de relación (van del ADR al `RelationshipStatement`), por eso se usa `in_edges`.
- Se usan **tuplas** porque las listas no pueden ir en un conjunto.
- `rp & rg`: intersección de conjuntos = relaciones idénticas en tipo **y** dirección. Si el modelo invierte el sentido («0006 reemplaza a 0011»), no cuenta: es justo el error que importa en el bloque 3.
- La comparación por ids de nodo funciona porque ambos grafos salen de `construir()` con los mismos ids.

### Fundamentación

```python
        _, met = comprobar(P, CORPUS / "adr")
        fund_ok += met["grounded_ok"]
        fund_tot += met["grounded_total"]
        filas.append((archivo, len(dp), len(dg), len(ap_), len(ag)))
```

Pasa el grafo del pipeline por el validador y suma cuántas evidencias aparecen en su ADR. Cuenta **todos** los `Statement` (contexto, consecuencias, argumentos…), no solo decisiones y alternativas: la planificación define la tasa sobre los nodos del grafo. `filas` guarda un resumen por ADR; de momento solo se usa para contar los ADR evaluados.

### Informe

```python
    print(f"Bloque 1 · particion {args.particion} · {len(filas)} ADR anotados")
    for k, (tp, np_, ng) in acc.items():
        p, r, f1 = prf(tp, np_, ng)
        print(f"  {k:<12} P={p:.2f} R={r:.2f} F1={f1:.2f}  (tp={tp}, pred={np_}, gold={ng})")
    print(f"  alternativa elegida correcta: {elegida_ok}/{elegida_n}")
    print(f"  estado normalizado correcto:  {estado_ok}/{estado_n}")
    print(f"  tasa de nodos no fundamentados: {1 - fund_ok / fund_tot if fund_tot else 0:.3f} ({fund_tot - fund_ok}/{fund_tot})")
```

Se imprimen siempre los conteos crudos (`tp`, `pred`, `gold`) junto a las proporciones. Con 9 ADR, «P = 0.89» es 8 de 9; ver el conteo evita sobreinterpretar porcentajes con muestras pequeñas.

Resultado del 09-10 (desarrollo):
```
decision     P=0.89 R=0.89 F1=0.89  (tp=8, pred=9, gold=9)
alternativa  P=0.92 R=1.00 F1=0.96  (tp=22, pred=24, gold=22)
relacion     P=0.80 R=0.80 F1=0.80  (tp=4, pred=5, gold=5)
alternativa elegida correcta: 5/5
estado normalizado correcto:  7/7
tasa de nodos no fundamentados: 0.121 (14/116)
```

## 7. Qué falta para el informe final

- Igualar el tratamiento de mayúsculas en `_span` con el del validador.
- Guardar el resultado en un fichero (JSON o CSV) además de imprimirlo, para que la tabla de la tesis salga de un artefacto versionado.
- Desglose por ADR y por formato (MADR / Nygard) para el análisis de A9.
- Intervalos de confianza (bootstrap por ADR), dado el tamaño de la muestra de prueba (35 ADR).
