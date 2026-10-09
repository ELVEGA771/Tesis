# Explicación del código del prototipo

Un documento por archivo. Cada uno copia el código por bloques y explica **qué hace cada línea y por qué está escrita así**. Cuando una decisión responde a la planificación o a una observación del profesor, se dice cuál.

## Orden de lectura recomendado

| # | Documento | Archivo | Para qué sirve en la tesis |
|---|---|---|---|
| 1 | [01_comun.md](01_comun.md) | `Prototipo/comun.py` | Piezas compartidas: rutas, tokenizador, llamada a Qwen con registro de costo |
| 2 | [02_particion.md](02_particion.md) | `corpus_tesis/particion.py` | Separa desarrollo y prueba por repositorio (retroalimentación del 05-10, punto 2) |
| 3 | [03_fechas_git.md](03_fechas_git.md) | `corpus_tesis/fechas_git.py` | Fecha de registro de cada ADR desde git y licencias (observaciones O1 y O3) |
| 4 | [04_rag.md](04_rag.md) | `Prototipo/rag.py` | Línea base RAG vectorial plana (A7) |
| 5 | [05_pipeline.md](05_pipeline.md) | `Prototipo/pipeline.py` | Pipeline LangGraph de 4 nodos con reintento (A5 + A6) |
| 6 | [06_evaluar_b1.md](06_evaluar_b1.md) | `Prototipo/evaluar_b1.py` | Bloque 1: fidelidad de construcción del grafo frente al gold |
| 7 | [07_consultas.md](07_consultas.md) | `esquema_v1_1/consultas.py` | Las CQ1–8 como funciones: la condición «grafo» y las respuestas esperadas |

## El mapa en una figura

```
                corpus_manifest.csv (140 ADR)      anotaciones.csv (gold, 45 ADR)
                        │                                   │
        fechas_git.py ──┤                                   │ csv_a_grafo.construir()
        particion.py  ──┤                                   ▼
                        │                            gold_anotado.json
       ┌────────────────┴───────────────┐                   │
       ▼                                ▼                   │
   rag.py (A7)                    pipeline.py (A5+A6)       │
   ChromaDB + bge                 Qwen3 → filas → construir()
       │                                │                   │
       │                                ▼                   ▼
       │                       datos/grafos/*.json ──► evaluar_b1.py (bloque 1)
       │                                │
       └──────► (A8) mismas preguntas, mismo B ◄── consultas.py (CQ1–8)
```

La idea central del diseño: **el grafo del pipeline y el gold se construyen con la misma función** (`csv_a_grafo.construir`). Cualquier diferencia entre ambos sale de lo que extrajo Qwen, no de dos programas distintos que escriben el grafo de forma distinta.

## Problemas que encontré al revisar línea por línea

Los dejo aquí para que no te sorprendan; ninguno invalida lo medido el 09-10, pero conviene saberlos:

1. **`evaluar_b1.py` distingue mayúsculas al localizar la evidencia en el texto y `conformidad.py` no.** Si Qwen copia un fragmento con otra capitalización, el validador lo da por fundamentado, pero el evaluador no lo puede ubicar y no lo empareja por solape. Afecta solo a decisiones y alternativas sin coincidencia de nombre. Ver [06_evaluar_b1.md](06_evaluar_b1.md).
2. **`rag.py` puede dejar fragmentos de más de 512 tokens de bge.** Un párrafo de más de 350 tokens de Qwen no se divide, y bge corta su entrada en 512 tokens (de su propio tokenizador), así que el final de ese fragmento no influye en su vector. Ver [04_rag.md](04_rag.md).
3. **`pipeline.py` importa `TESIS` y no lo usa.** No tiene efecto; se puede borrar.
4. **El reintento vuelve a ejecutar también el nodo `decidir`**, aunque los errores sean solo de `extraer`. Es coherente (la alternativa elegida depende de las alternativas extraídas), pero cuesta una llamada más por intento.
