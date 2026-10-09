# CORRECCION

El script que generaste para **{{cq_id}}** no paso la validacion.

Pregunta de competencia: {{cq_text}}

## Script que enviaste

```python
{{code}}
```

## Problemas detectados

```
{{errors}}
```

## Grafo de la etapa anterior (lo que no puedes perder)

```python
{{previous_graph}}
```

## Resumen de la convencion

```
{{convention_reference}}
```

## Instrucciones

Devuelve el **script completo y corregido**: cabecera (`import networkx as nx`
y `G = nx.MultiDiGraph()`), todo lo que ya habia en el grafo de la etapa
anterior, y lo que anade esta pregunta de competencia, ya arreglado.

Un unico bloque ```python, sin texto fuera de el. Solo `G.add_node(...)` y
`G.add_edge(...)` con argumentos literales, mas comentarios `#`.

Si un problema no se puede arreglar porque el nodo o la arista sobraba,
eliminalo en vez de inventar atributos. Si el problema es que faltaba el
dominio o el rango de una propiedad, anadelos.
