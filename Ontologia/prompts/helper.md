# AYUDANTE

Lee estas definiciones para entender los conceptos y la representacion.

## 0. Esqueleto del script

Todo script empieza siempre igual:

```python
import networkx as nx

G = nx.MultiDiGraph()
```

Se usa `MultiDiGraph` y no `DiGraph` porque hace falta poder tener mas de una
arista entre el mismo par de nodos (por ejemplo, un ADR que a la vez
`considersAlternative` y `selectsAlternative` sobre la misma alternativa).

**Regla general de la representacion**: todo nodo lleva un atributo `type` y
toda arista lleva un atributo `relation`. Sin excepciones.

## 1. Clases

Las clases son las palabras clave del dominio que van a ser tipos de nodo en
el grafo de conocimiento. Intenta **extraer todas las clases**; ademas, las
clases tambien se pueden definir para reificar (ver seccion 3).

```python
G.add_node("Profesor", type="Class")
G.add_node("Administrativo", type="Class")
```

Las clases se encuentran en la historia, en la pregunta de competencia y en
las restricciones. **No** son clases las fechas, los enteros, las cadenas ni
los nombres propios concretos: las primeras son propiedades de datos y los
segundos son individuos.

## 2. Jerarquias

La jerarquia es la relacion `subClassOf`. Sirve para clasificar clases
parecidas bajo una superclase comun. Para construirla, busca nodos similares y
crea o reutiliza una clase que sirva de padre. Por ejemplo, en una ontologia
de universidad, anadir la clase `Empleado` es un buen intermediario y
superclase de `Profesor` y de `Administrativo`:

```python
G.add_node("Empleado", type="Class")
G.add_edge("Profesor", "Empleado", relation="subClassOf")
G.add_edge("Administrativo", "Empleado", relation="subClassOf")
```

**Por lo general, cuanto mas larga sea la jerarquia, mejor.** Una via es
categorizar las clases en varios grupos y crear una superclase para cada
grupo.

Ademas de la jerarquia, cada clase puede tener:

```python
# Equivalencia entre dos clases
G.add_edge("ClaseA", "ClaseB", relation="equivalentClass")
# Disjuncion
G.add_edge("ClaseA", "ClaseC", relation="disjointWith")
```

## 3. Reificacion (clases pivote)

En tu modelado, para cada pregunta de competencia, cuando te encuentres
escenarios complejos que involucren **mas de dos entidades**, o una
**combinacion de entidades y tipos de dato**, aplica reificacion:
crea una clase pivote que actue de intermediaria entre esas entidades, para
capturar la relacion con todos sus matices.

Ejemplo. La frase *"un usuario accedio a un recurso en un momento dado"*
parece a primera vista un vinculo directo entre `Usuario` y `Recurso`, pero en
realidad involucra tres cosas: un usuario, un recurso y un instante. Conectar
`Usuario` directamente con `Recurso` y con el instante no captura la frase,
porque se pierde que recurso uso el usuario en que momento concreto. La
solucion es una clase pivote `UsoDeRecurso` que enlaza a `Usuario` y a
`Recurso` e incorpora una propiedad de tiempo:

```python
G.add_node("UsoDeRecurso", type="Class", pivot=True)

G.add_node("usuarioDelUso", type="ObjectProperty")
G.add_edge("usuarioDelUso", "Usuario", relation="domain")
G.add_edge("usuarioDelUso", "UsoDeRecurso", relation="range")

G.add_node("recursoDelUso", type="ObjectProperty")
G.add_edge("recursoDelUso", "Recurso", relation="domain")
G.add_edge("recursoDelUso", "UsoDeRecurso", relation="range")

G.add_node("instanteDeUso", type="DatatypeProperty")
G.add_edge("instanteDeUso", "UsoDeRecurso", relation="domain")
G.add_edge("instanteDeUso", "xsd:dateTime", relation="range")
```

Fijate en la **direccion**: las propiedades van **desde cada clase componente
(dominio) hacia la clase pivote (rango)**. La clase pivote se marca con
`pivot=True`.

Esto aplica a toda relacion que dependa del tiempo, que lleve un rol asociado,
o que sea una evaluacion argumentativa: no la aplanes en una sola arista.

## 4. Propiedades

Las propiedades son **nodos**, no atributos de arista. Se declaran con su tipo
y se conectan a su dominio y a su rango.

### Propiedades de objeto

Van entre clases. Intenta encontrar **todas las relaciones posibles** leyendo
las preguntas de competencia, las restricciones y la historia.

```python
G.add_node("imparte", type="ObjectProperty")
G.add_edge("imparte", "Profesor", relation="domain")
G.add_edge("imparte", "Asignatura", relation="range")
```

### Propiedades de datos

Van entre una clase y un tipo de dato. El rango tiene que ser un `xsd:*`:

```python
G.add_node("numeroEmpleado", type="DatatypeProperty")
G.add_edge("numeroEmpleado", "Profesor", relation="domain")
G.add_edge("numeroEmpleado", "xsd:integer", relation="range")
```

Tipos de dato disponibles: `xsd:string`, `xsd:integer`, `xsd:decimal`,
`xsd:float`, `xsd:double`, `xsd:boolean`, `xsd:byte`, `xsd:date`,
`xsd:dateTime`, `xsd:time`, `xsd:duration`, `xsd:gYear`, `xsd:anyURI`.

### Jerarquias y caracteristicas de propiedades

Puedes crear jerarquias de relaciones y, cuando sea necesario, declarar las
caracteristicas de una propiedad:

```python
G.add_edge("dirigeTesis", "supervisa", relation="subPropertyOf")
G.add_edge("imparte", "esImpartidaPor", relation="inverseOf")
G.add_node("tieneIdentificador", type="DatatypeProperty",
           characteristics=["Functional"])
```

Caracteristicas validas: `Functional`, `InverseFunctional`, `Transitive`,
`Symmetric`, `Asymmetric`, `Reflexive`, `Irreflexive`.

Tienes libertad en dominio y rango: puedes poner `ClaseA` o `ClaseB` como
dominio o rango, declarar disjunciones y declarar la inversa entre relaciones.

## 5. Restricciones

Una restriccion es un nodo `type="Restriction"` que se conecta a la propiedad
sobre la que aplica y a su relleno, y que despues se engancha a una clase.

**Condicion necesaria y suficiente** (equivale a `owl:equivalentClass` con una
`owl:Restriction`): sirve para decir, por ejemplo, que `ClaseC1` es la unica
clase que usa la relacion `R`.

```python
G.add_node("R_soloC1", type="Restriction")
G.add_edge("R_soloC1", "R", relation="onProperty")
G.add_edge("R_soloC1", "ClaseDestino", relation="allValuesFrom")
G.add_edge("ClaseC1", "R_soloC1", relation="equivalentClass")
```

Restriccion mas debil, con `someValuesFrom` en lugar de `allValuesFrom`,
cuando basta con que exista algun valor.

**Axioma de clase general**: cuando quieres restringir la definicion de una
clase a partir de su relacion y la condicion es *necesaria pero no suficiente*
(si fuese suficiente, seria una equivalencia). La restriccion es entonces el
**origen** de un `subClassOf`:

```python
G.add_node("R_tieneAlgunaAlternativa", type="Restriction")
G.add_edge("R_tieneAlgunaAlternativa", "considera", relation="onProperty")
G.add_edge("R_tieneAlgunaAlternativa", "Alternativa", relation="someValuesFrom")
G.add_edge("R_tieneAlgunaAlternativa", "Registro", relation="subClassOf")
```

Rellenos disponibles: `someValuesFrom`, `allValuesFrom`, `hasValue`.

## 6. Individuos (solo si la historia da ejemplos concretos)

Un individuo es un nodo cuyo `type` es el **nombre de una clase ya declarada**.
Sus valores literales van como atributos del propio nodo, y sus relaciones con
otros individuos son aristas directas cuya `relation` es el nombre de una
propiedad de objeto ya declarada:

```python
G.add_node("ADR-001", type="ADR", title="Usar PostgreSQL")
G.add_node("Alt-01", type="Alternativa")
G.add_edge("ADR-001", "Alt-01", relation="selectsAlternative")
```

## 7. Resumen de la convencion

```
{{convention_reference}}
```

No lo olvides: tu salida es una ontologia independiente y completa que va a
sustituir a la version anterior. En este prompt resuelve **unicamente** la
pregunta de competencia dada y no te adelantes a las demas.
