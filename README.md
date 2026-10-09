# Grafo de decisiones de arquitectura frente a RAG vectorial

Herramientas del proyecto integrador de Andrés Vega (USFQ): un pipeline multiagente en LangGraph sobre Qwen3-4B-Instruct construye un grafo de decisiones (NetworkX) a partir de Architecture Decision Records (ADR), y se compara con una línea base RAG vectorial plana (bge-base-en-v1.5 + ChromaDB) usando el mismo modelo, las mismas preguntas y el mismo presupuesto de contexto.

## Estructura

| Carpeta | Contenido |
|---|---|
| `Extraccion_ADR/` | Recolección del corpus desde GitHub, selección del subconjunto de tesis y exclusión de ADR generados con IA |
| `Extraccion_ADR/corpus_tesis/` | Manifiesto de los 140 ADR, cadenas de reemplazo (ground truth), fechas de git y licencias, partición desarrollo/prueba |
| `Extraccion_ADR/corpus_tesis/anotacion/` | Hoja de anotación del gold (45 ADR), guía, conversor a grafo y herramienta local de anotación |
| `Ontologia/` | Implementación de CQbyCQ (Saeedizade y Blomqvist, ESWC 2024) con Gemini |
| `Ontologia/outputs/esquema_v1*/` | Esquemas del grafo congelados (v1.0 y v1.1), validador de instancias y consultas CQ1–8 |
| `Ontologia/outputs/esquema_plan/`, `esquema_madr/` | Corridas CQbyCQ de las que se derivan la v1.0 y la v1.1 |
| `Prototipo/` | Línea base RAG, pipeline LangGraph, evaluación del bloque 1 |
| `Prototipo/explicaciones/` | Explicación línea por línea de los archivos principales |

## Textos de los ADR

Los textos **no se incluyen**: 41 de los 140 ADR vienen de repositorios sin licencia, y que un repositorio sea público no autoriza a redistribuirlo. Se publican los identificadores (repositorio, ruta y commit de cada ADR en `fechas_git.csv`), las anotaciones y el procedimiento para reconstruir el corpus:

```
cd Extraccion_ADR/corpus_tesis
python descargar_adr.py          # crea adr/ con los 140 ADR en la versión anotada
```

`descargar_adr.py` descarga cada fichero en el commit en que coincide con la copia anotada (`sha_corpus`), no en el `HEAD` actual: tres ADR se editaron en GitHub después de la recolección.

## Reproducir

```
pip install -r Prototipo/requirements.txt
ollama pull qwen3:4b-instruct

cd Extraccion_ADR/corpus_tesis && python descargar_adr.py && cd anotacion && python csv_a_grafo.py
cd ../../../Prototipo
python rag.py indexar
python pipeline.py --particion desarrollo
python evaluar_b1.py --particion desarrollo
```

Configuración fija: temperatura 0, semilla 42, `num_ctx` 16384, presupuesto de contexto B = 2048 tokens de Qwen en ambas condiciones.
