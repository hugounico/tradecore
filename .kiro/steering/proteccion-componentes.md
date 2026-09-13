---
inclusion: always
---

# Proteccion de Componentes Validados — Principio Permanente

Regla de gobernanza que aplica a todo el proyecto TradeCore, independiente de la fase.
Complementa (no reemplaza) las reglas de `dev-rules.md` y las decisiones de
`databento-architecture.md`.

---

## Principio general

Los componentes y comportamientos marcados como **protegidos** en el contexto de la
fase actual del proyecto NO deben modificarse sin aprobacion explicita del usuario —
**incluso si el cambio se realiza de forma indirecta a traves de otro archivo, o de
un archivo nuevo.**

La lista concreta de que esta protegido en cada momento vive en un archivo de Steering
de fase separado (ej. `fase-actual-etapaN.md`), porque cambia entre etapas. Este archivo
define solo el PRINCIPIO, que es permanente.

### Que cuenta como "modificacion a un componente protegido"

- Editar directamente el archivo protegido.
- Cambiar, desde cualquier archivo (incluido uno NUEVO), un comportamiento protegido:
  si tocas un archivo nuevo y eso altera un comportamiento de la lista protegida, se
  trata como una modificacion al componente protegido.
- Modificar un test que sirve como evidencia de un comportamiento protegido.

La proteccion es sobre el COMPORTAMIENTO, no solo sobre el archivo. Un componente nuevo
que reemplaza o altera un comportamiento validado cuenta como modificacion protegida.

---

## Regla de trabajo (secuencia obligatoria)

Antes de modificar cualquier archivo o comportamiento protegido:

1. **DETENTE.** No lo hagas "de paso" ni por considerarlo una mejora de diseno. Solo
   procede si es estrictamente necesario para el objetivo de la fase actual.
2. Si el codigo nuevo requiere integrarse con un componente protegido y esa integracion
   toca lo protegido: **reportalo antes de hacerlo**, no lo ejecutes y luego avises.
3. Si el codigo existente parece bloquear el objetivo de la fase:
   a. NO modifiques el componente protegido.
   b. Documenta el problema y explica por que crees que bloquea el objetivo.
   c. Propon la modificacion MINIMA necesaria.
   d. Espera aprobacion explicita antes de tocar una sola linea.
4. NO asumas que una refactorizacion, simplificacion u optimizacion es necesaria solo
   porque el codigo podria escribirse de otra forma.
5. Los tests existentes son evidencia del comportamiento validado, pero no son
   infalibles. Si un test parece contradecir el comportamiento real y correcto,
   reportalo — NO lo modifiques para que pase sin aprobacion.
6. Todo cambio aprobado a un componente protegido debe ir acompanado de la ejecucion
   de la suite de tests relacionada, para confirmar que no se rompio el comportamiento
   ya validado en produccion.

---

## Alcance

- Este principio NO bloquea la creacion de codigo nuevo e independiente.
- La lista especifica de componentes protegidos y el estado de la fase actual estan en
  el archivo de Steering de fase correspondiente.
- Este principio es permanente; la lista de fase es temporal y se actualiza cuando
  cambia el estado del proyecto.
