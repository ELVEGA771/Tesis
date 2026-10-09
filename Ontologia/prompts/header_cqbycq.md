# ENCABEZADO

Tu tarea es contribuir a la creacion de informacion ontologica bien
estructurada a partir de la historia dada, sus requisitos y sus restricciones
(si las hay).

La forma de abordarlo es esta: primero tomas la pregunta de competencia que se
te indica en la seccion **TAREA** y lees el codigo Python/NetworkX de la
seccion **GRAFO ACTUAL** para saber cual es la ontologia hasta esta etapa
(al principio puede estar vacia). Despues **agregas o cambias** el codigo
Python/NetworkX para que la ontologia pueda responder a esa pregunta de
competencia.

Tu salida en cada etapa es un **script de Python ejecutable e independiente**:
reescribe el grafo anterior y produce el nuevo, completo. El script que
devuelvas va a **sustituir** al anterior, asi que tiene que contener todo lo
que ya habia (clases, propiedades, aristas, restricciones, atributos) mas lo
que anades ahora. Si borras algo de lo anterior, se pierde.

Solo tienes que resolver la pregunta de competencia indicada: **no toques las
siguientes**, porque pertenecen a las etapas posteriores del desarrollo.

## Formato de la salida

Un unico bloque ```python, sin nada de texto fuera de el:

```python
import networkx as nx

G = nx.MultiDiGraph()

# ¿Es esta CQ respondible con el grafo anterior? -> <si / no, y por que en una linea>
# Clases pivote (reificacion) que voy a crear en esta etapa: <nombres, o "ninguna">

# --- Clases ---
G.add_node("...", type="Class")
...
```

**Importante**: antes de escribir el resto del codigo, escribe esas dos lineas
de comentario. La primera responde a si la CQ ya era respondible con el grafo
anterior (lo habitual, ~90% de las veces, es que **no** lo sea). Si no lo era,
la segunda declara el nombre de las clases pivote que necesitas antes de
usarlas. Si la CQ ya era respondible, limitate a reproducir el grafo anterior
sin cambios.
