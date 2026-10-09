# PIE DE PAGINA

Aqui tienes algunos errores que podrias cometer. Revisa tu salida contra todas
estas listas antes de responder.

## Errores generales

1. Olvidar la cabecera del script (`import networkx as nx` y
   `G = nx.MultiDiGraph()`) al principio del codigo.
2. Olvidar declarar las clases pivote al principio, antes de empezar a usarlas.
3. Tu salida tiene que **partir del grafo anterior y concatenarle** la
   respuesta a la pregunta de competencia: tu salida crea **y** fusiona.
4. En tu salida pon **todas** las clases, relaciones, restricciones y atributos
   anteriores, y anade los tuyos. Tu salida se pasa a la etapa siguiente y va a
   **sustituir** al grafo anterior, asi que no elimines nada de lo que ya
   estaba.
5. Sueles olvidar escribir al principio el nombre de la clase pivote
   (reificacion) que vas a crear.
6. Devolver texto explicativo, introducciones o bloques conversacionales. La
   salida es **solo** el bloque de codigo Python ejecutable. Si necesitas
   justificar algo, hazlo en un comentario `#` dentro del bloque.
7. Reinicializar el grafo perdiendo lo anterior: `G = nx.MultiDiGraph()` va al
   principio, pero a continuacion tienes que volver a crear todos los nodos y
   aristas previos.
8. Dejar aristas sin atributo `relation` o nodos sin atributo `type`.

## Errores al extraer clases

1. No extraer todas las clases y dejarte muchas. Las clases estan en la
   historia, en la pregunta de competencia y en las restricciones.
2. Devolver una respuesta vacia.
3. Dar comentarios o explicaciones fuera del bloque de codigo.
4. Extraer como clases cosas como `Fecha` o `Entero`: son propiedades de datos,
   no clases.
5. No usar reificacion: no extraer clases pivote para modelar una relacion
   entre mas de dos clases, o entre mas de una clase y un tipo de dato.
6. Extraer como clases los individuos que aparecen en el texto.
7. Crear la clase pivote sin conectarla con sus componentes.

## Errores en la jerarquia

1. Crear jerarquia para clases que no existen: inventar una hoja nueva y
   expandirla hasta la raiz.
2. Devolver una respuesta vacia o demasiado corta.
3. Dar comentarios o explicaciones fuera del bloque de codigo.
4. Meter en la jerarquia atributos como fechas, horas o nombres, que son
   propiedades de datos.
5. Olvidar las comillas alrededor de las cadenas.

## Errores en las propiedades de objeto

1. Declarar como `ObjectProperty` algo que no es una relacion entre clases.
2. Devolver una respuesta vacia o demasiado corta.
3. Dar comentarios o explicaciones fuera del bloque de codigo.
4. Cuando creas una clase pivote, **todas** las clases relacionadas tienen que
   apuntar a ella: la direccion de la relacion va desde las clases (dominio)
   **hacia** la clase pivote (rango).
5. Olvidar el `domain` o el `range` de una propiedad.
6. Usar una propiedad como arista directa entre dos clases. Entre clases, una
   propiedad se declara como nodo con `domain` y `range`; las aristas directas
   con el nombre de la propiedad son solo entre individuos.

## Errores en las propiedades de datos

1. Declarar como `DatatypeProperty` algo que no es un atributo literal.
2. Devolver una respuesta vacia o demasiado corta.
3. Dar comentarios o explicaciones fuera del bloque de codigo.
4. Poner una clase como `range` de una propiedad de datos: el rango tiene que
   ser un `xsd:*`. Si de verdad apunta a una clase, es una `ObjectProperty`.

## Errores en relaciones temporales y argumentativas

1. Simplificar en una sola arista una relacion que depende del tiempo (un
   cambio de estado en una fecha concreta) o una evaluacion argumentativa (los
   pros y contras de una alternativa dentro de un registro). Implementa el nodo
   intermedio de reificacion para preservar el contexto.
