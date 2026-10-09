# VERIFICACION DE PREGUNTA DE COMPETENCIA

Actuas como evaluador de ontologias. NO estas modelando: estas comprobando si
una ontologia ya construida permite responder una pregunta de competencia.

## Criterio

La CQ se considera **cubierta** si es posible escribir una consulta sobre el
grafo que recupere lo que la CQ pide, usando exclusivamente las clases,
propiedades, restricciones y atributos que ya existen en la ontologia. Si para
responderla hace falta inventar una clase o una propiedad que no esta, la CQ
**no** esta cubierta.

Juzga la estructura, no los datos: que no haya individuos de ejemplo no impide
que la CQ este cubierta, siempre que el esquema permita expresar la consulta.

## Pregunta de competencia

{{cq_id}}: {{cq_text}}

## Historia (contexto)

{{story_text}}

## Ontologia a evaluar

```python
{{ontology}}
```

## Convencion del grafo

`G` es un `networkx.MultiDiGraph`.

```
{{convention_reference}}
```

- Las propiedades son nodos; su dominio y su rango son aristas `domain` y
  `range` que salen del nodo de la propiedad.
- Un individuo es un nodo cuyo `type` es el nombre de una clase declarada; sus
  literales son atributos del nodo y sus relaciones son aristas directas cuya
  `relation` es el nombre de una propiedad de objeto.

## Salida

Devuelve **solo** un objeto JSON, sin texto alrededor, con esta forma:

```json
{
  "answerable": true,
  "query": "def consulta(G): ...",
  "used_terms": ["Clase1", "propiedad1"],
  "missing_terms": [],
  "minor_issue": false,
  "minor_issue_kind": null,
  "justification": "una o dos frases"
}
```

Reglas para los campos:

- `answerable`: `true` solo si la consulta se puede escribir por completo con
  los terminos existentes.
- `query`: la funcion Python sobre `G` que responderia la CQ (o la que la
  responderia si se anadiese lo que falta).
- `missing_terms`: los terminos que faltan, si los hay.
- `minor_issue`: `true` cuando falta **exactamente una** propiedad (de objeto o
  de datos) y, anadiendola, la consulta quedaria correcta con un solo paso mas.
  Ese es el unico caso de "problema menor"; si falta una clase, o falta mas de
  una propiedad, es `false`.
- `minor_issue_kind`: `"missing_property"` o `null`.
