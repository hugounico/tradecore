# TradeCore · Paso 2 Login · Requirements

Derivado exclusivamente de `design.md` (DESIGN v1.4 aprobado). Los requisitos están
redactados en formato EARS (Easy Approach to Requirements Syntax — método de redacción
con frases del tipo "Cuando/Mientras/Si …, el sistema debe …"). Cada requisito es
verificable (describe qué entrada produce qué resultado observable) y cita su origen
`(§n)` en `design.md`. No se agregan valores, comportamientos ni decisiones que no estén
en `design.md`.

Glosario mínimo (los términos se vuelven a explicar la primera vez que aparecen en cada
sección): **login** (ingreso con usuario y contraseña); **cookie** (pequeño dato que el
navegador guarda y reenvía en cada petición para recordar la sesión); **token** (credencial
temporal firmada que prueba quién es el usuario); **sesión** (período en que un navegador
queda reconocido como un usuario autenticado).

---

## Enrutamiento y flujo de usuario (§5, §6)

Términos de esta sección: **ruta** (dirección dentro del sitio, p. ej. `/login`);
**GET/POST** (tipos de petición HTTP — Protocolo de transferencia de datos por internet —:
GET pide datos, POST envía datos); **303** (código HTTP que ordena al navegador ir a otra
dirección); **401** (código HTTP "no autorizado"); **cookie de sesión** (ver glosario);
**WebSocket** (protocolo que mantiene una conexión abierta para enviar datos en tiempo real);
**deny-by-default** (regla de "todo prohibido salvo lo explícitamente permitido");
**mount `/static`** (carpeta de archivos del dashboard servida bajo `/static`).

REQ-001: Cuando un navegador pida `GET /` sin una cookie de sesión válida, el sistema debe responder con un redireccionamiento 303 hacia `/login`. (§5, §6)

REQ-002: Cuando un navegador pida `GET /` con una cookie de sesión válida, el sistema debe responder con un redireccionamiento 303 hacia `/static/index.html` (la página del dashboard). (§5, §6)

REQ-003: Cuando un navegador pida `GET /login` sin una cookie de sesión válida, el sistema debe mostrar el formulario de ingreso, que incluye el enlace "Cambiar contraseña". (§5)

REQ-004: Cuando un navegador pida `GET /login` con una cookie de sesión válida, el sistema debe mostrar la página "Sesión activa", que ofrece "Ir al dashboard" (enlace) y "Cerrar sesión" (formulario que envía POST a `/auth/logout`). (§5)

REQ-005: Cuando un navegador pida `GET /auth/login.js` (el programa JavaScript de la página de ingreso), el sistema debe entregarlo como archivo estático sin exigir sesión. (§6)

REQ-006: Mientras no exista una cookie de sesión válida, si se pide un archivo de `/static/*` con una cabecera `Accept` (preferencia de formato que envía el navegador) que incluya `text/html` (páginas web), el sistema debe redirigir con 303 a `/login`; para cualquier otra preferencia debe responder 401 (no autorizado). (§6)

REQ-007: Si una petición usa una ruta y un método no incluidos en la lista pública exacta (deny-by-default — todo protegido salvo lo permitido), el sistema debe tratarla como protegida: sin cookie válida debe responder como en REQ-006 y, con cookie válida, debe entregar la respuesta normal de la aplicación (por ejemplo 404 "no existe" o 405 "método no permitido"). (§6)

REQ-008: Cuando el sistema emita un redireccionamiento del control de acceso, la dirección de destino debe ser una ruta relativa fija (`/login` o `/static/index.html`) que no dependa de las cabeceras `Host`, `X-Forwarded-*` ni del esquema (http/https), y no debe aceptarse un destino enviado como parámetro. (§6)

REQ-009: Mientras se evalúe si una ruta es pública, la coincidencia debe exigir ruta exacta Y método exacto sobre la ruta ya decodificada por el servidor; un método no listado en una ruta pública (por ejemplo `POST /health`) debe tratarse como protegido. (§6)

REQ-010: Cuando se pida `GET /health` (punto de chequeo de estado que usa la infraestructura), el sistema debe responder exactamente igual que hoy (código 200 "OK"), con o sin sesión y tanto si la configuración de acceso es válida como si es inválida. (§6, §10)

## Ingreso y cambio de contraseña en el navegador (§5)

Términos de esta sección: **Supabase** (servicio externo que guarda los usuarios y verifica
las contraseñas); **access_token** (token — credencial temporal firmada — que Supabase
entrega al ingresar); **refresh_token** (token que permite pedir nuevos access_token; en este
diseño nunca sale de la memoria de la página); **apikey/publishable key** (clave pública que
identifica el proyecto ante Supabase); **same-origin** (petición dirigida al mismo sitio que
sirvió la página); **localStorage/sessionStorage/IndexedDB** (almacenes de datos del navegador);
**grant** (tipo de concesión de credenciales).

REQ-011: Cuando el usuario envíe el formulario de ingreso, `login.js` debe pedir a Supabase, mediante una petición POST a `<SUPABASE_URL>/auth/v1/token?grant_type=password`, con la cabecera `apikey` (clave pública del proyecto) y el cuerpo `{email, password}`, sin enviar cookies (`credentials: 'omit'`) y sin usar caché (`cache: 'no-store'`). (§5)

REQ-012: Cuando Supabase responda con éxito (código 200) al ingreso, `login.js` debe tomar solo el access_token (credencial temporal firmada), enviarlo por POST same-origin (al mismo sitio) a `/auth/enter` mediante un formulario oculto, y no debe guardar nada en localStorage, sessionStorage, IndexedDB ni cookies, ni escribir la respuesta en la consola del navegador; el refresh_token no debe leerse ni copiarse y debe descartarse al navegar. (§5)

REQ-013: Si Supabase responde con error 400 (datos inválidos) al ingreso, el sistema debe mostrar "Credenciales inválidas"; si hay un error de red o un error del servidor (5xx), debe mostrar "Servicio de acceso no disponible, intenta más tarde". (§5)

REQ-014: Cuando el usuario use "Cambiar contraseña", `login.js` debe primero obtener un access_token con el ingreso por contraseña y luego pedir a Supabase, mediante una petición PUT a `<SUPABASE_URL>/auth/v1/user` con la cabecera `apikey`, la autorización `Bearer <access_token>` y el cuerpo `{password: nueva}`, cambiar la contraseña, sin que TradeCore participe en ese intercambio. (§5)

REQ-015: Cuando el cambio de contraseña responda con éxito (200), el sistema debe mostrar "Contraseña actualizada; ingresa con la nueva", descartar los tokens y no crear una sesión de TradeCore; ante cualquier error debe mostrar un mensaje genérico. (§5)

REQ-016: Cuando una sesión venza o sea revocada mientras el WebSocket (conexión en tiempo real del dashboard) está abierto, el sistema debe cerrar ese WebSocket con el código 4401 y `app.js` debe llevar al navegador a `/login`. (§5, §9.4)

## Canje del token de Supabase en `POST /auth/enter` (§7)

Términos de esta sección: **canje** (acto de entregar el access_token a TradeCore para
recibir a cambio una sesión propia); **claim** (dato afirmado dentro del token, p. ej. quién
es el usuario); **firma ES256** (firma digital con curva elíptica P-256 que prueba que el
token lo emitió Supabase y no fue alterado); **JWKS** (JSON Web Key Set — conjunto de claves
públicas con las que se verifica la firma); **kid** (identificador de cuál clave del JWKS
firmó el token); **base64url** (forma de codificar texto admitida en direcciones web);
**Origin** (cabecera que indica desde qué sitio se originó la petición); **UUID**
(identificador único universal — código que identifica de forma exclusiva a cada usuario);
**t** (hora actual del servidor).

REQ-017: Cuando llegue una petición a `/auth/enter`, el sistema debe validar en orden de menor a mayor costo y ejecutar la verificación criptográfica solo si todo lo anterior pasó. (§7)

REQ-018: Si la configuración de acceso no está correcta, el sistema debe rechazar `/auth/enter` con el motivo `config_invalid` sin procesar el cuerpo de la petición. (§7, §10)

REQ-019: Si se supera el límite de peticiones permitidas, el sistema debe responder 429 ("demasiadas peticiones") incluyendo la cabecera `Retry-After` (cuántos segundos esperar). (§7, §11)

REQ-020: Si falta la cabecera `Origin` o no coincide exactamente con alguno de los valores de `TC_AUTH_ALLOWED_ORIGINS` (lista de orígenes permitidos), el sistema debe responder 403 ("prohibido"). (§7)

REQ-021: Si el tipo de contenido no es `application/x-www-form-urlencoded` (formato de formulario), el sistema debe responder 400; si el cuerpo supera 8 KB (leído por partes), debe responder 413 ("cuerpo demasiado grande"); si el campo `access_token` no es único, debe responder 400. (§7)

REQ-022: Si la forma del token no son tres segmentos base64url de longitud máxima 4 KB, o su encabezado no declara algoritmo ES256 con un `kid` presente en el JWKS fijado, el sistema debe rechazarlo sin verificar la firma. (§7)

REQ-023: Cuando la forma del token sea válida, el sistema debe verificar la firma ES256 usando la clave pública del `kid` indicado. (§7)

REQ-024: Cuando la firma sea válida, el sistema debe exigir que el emisor (`iss`) sea `<TC_AUTH_SUPABASE_URL>/auth/v1`, que la audiencia (`aud`) sea `authenticated`, que el rol (`role`) sea `authenticated`, que estén presentes los datos `exp` (vencimiento), `iat` (hora de emisión), `sub` (identificador del usuario) y `role`, que `exp` sea mayor que la hora actual, que `nbf` ("no válido antes", si existe) sea menor o igual a la hora actual + 30 segundos, que `iat` sea menor o igual a la hora actual + 30 segundos, que la ventana de canje (hora actual − `iat`) sea menor o igual a 120 segundos, y que `sub` esté en la lista `TC_AUTH_ALLOWED_SUBS` (usuarios autorizados); ante cualquier incumplimiento debe rechazar. (§7)

REQ-025: Mientras procesa `/auth/enter`, el sistema no debe guardar ni registrar el token. (§7)

REQ-026: Mientras un access_token sea válido (dentro de los 120 segundos desde su emisión), el sistema debe permitir que una copia se canjee más de una vez, y cada canje debe emitir una cookie de sesión de TradeCore para ese usuario; el sistema no impone el uso único del token (riesgo R1 aceptado). (§7, §17)

## Sesión: la cookie propia de TradeCore (§8)

Términos de esta sección: **HMAC-SHA256** (código de autenticación que, con una clave
secreta, prueba que la cookie no fue alterada); **TTL** (Time To Live — tiempo de vida de la
cookie antes de vencer); **HttpOnly** (la cookie no es accesible desde JavaScript);
**Secure** (solo se envía por HTTPS); **SameSite=Lax** (limita el envío de la cookie desde
otros sitios); **época** `ep` (número que permite invalidar en bloque todas las cookies
cuando se incrementa); **despliegue** (publicación de una nueva versión del servicio).

REQ-027: Cuando el canje en `/auth/enter` tenga éxito, el sistema debe emitir la cookie llamada `__Host-tc_session` con los atributos `HttpOnly; Secure; SameSite=Lax; Path=/`, sin `Domain`, y con `Max-Age` (duración) igual a `TC_SESSION_TTL_SECONDS` (28800 segundos = 8 horas). (§8)

REQ-028: Cuando se construya el valor de la cookie, debe tener la forma `v1.<datos en base64url con {sub, iat, exp, ep}>.<firma HMAC-SHA256 en base64url de la primera parte, con la clave TC_SESSION_KEY>`, comparada con `hmac.compare_digest` (comparación segura) y usando solo la biblioteca estándar de Python. (§8)

REQ-029: Cuando se emita la cookie, su vencimiento `exp` debe fijarse en `iat` + TTL, y ninguna ruta debe reemitir ni extender la cookie; la única forma de obtener una cookie nueva debe ser un nuevo canje en `/auth/enter` (es decir, volver a ingresar con la contraseña). (§8)

REQ-030: En cada petición HTTP y en el handshake (apretón de manos inicial) del WebSocket, el sistema debe revalidar la cookie comprobando la firma, que `exp` sea mayor que la hora actual, que la época `ep` sea igual a `TC_SESSION_EPOCH` y que `sub` esté en la lista de usuarios autorizados. (§8)

REQ-031: Cuando cambien `TC_AUTH_ALLOWED_SUBS`, `TC_SESSION_EPOCH` o `TC_SESSION_KEY` mediante un despliegue, el sistema debe aplicar la revocación de forma diferida (efectiva al terminar el despliegue), sin guardar estado en memoria. (§8)

REQ-032: Cuando el usuario cierre sesión voluntariamente, el sistema debe borrar la cookie en ese navegador; las copias de la cookie hechas en otro lugar deben seguir siendo válidas hasta su vencimiento (riesgo R2 aceptado). (§8, §17)

## Middleware de control de acceso AuthGate (§9)

Términos de esta sección: **middleware** (capa de software que intercepta cada petición antes
de que llegue a la aplicación); **ASGI** (Asynchronous Server Gateway Interface — norma de
comunicación entre el servidor y la aplicación Python asíncrona); **scope** (datos de la
conexión que ASGI entrega: ruta, método, cabeceras); **send/receive** (funciones ASGI para
enviar y recibir mensajes); **deadline** (instante límite = vencimiento de la cookie);
**send_lock** (cerrojo que serializa los envíos para que no se solapen); **ClosedByGate**
(error interno que señala "el control de acceso ya cerró la conexión"); **ThrottledPusher**
(componente existente que reparte las actualizaciones del dashboard a los navegadores);
**CancelledError** (señal estándar de cancelación de una tarea asíncrona).

REQ-033: El middleware debe envolver toda la aplicación, incluida la carpeta `/static`; debe aplicar sus reglas a las conexiones HTTP y WebSocket y dejar pasar sin tocar el ciclo de arranque/apagado (`lifespan`); solo debe usar la ruta, el método y las cabeceras `cookie`, `origin`, `accept`, `content-type` y `content-length`, sin deducir la dirección ni el esquema a partir de cabeceras controladas por el cliente. (§9.1)

REQ-034: Para una petición HTTP, cuando la ruta sea pública el middleware debe dejarla pasar; para el resto, si la configuración es correcta y la cookie es válida debe dejarla pasar y añadir las cabeceras de seguridad de §11; en cualquier otro caso debe responder según §6. (§9.2)

REQ-035: Para una conexión WebSocket, si la configuración es inválida, la cookie es inválida o ausente, o el `Origin` no está permitido, el middleware debe esperar el inicio de conexión, aceptarla y luego cerrarla con el código 4401 (o 4403 si el motivo es el `Origin`), sin ejecutar el endpoint ni enviar ningún dato. (§9.3)

REQ-036: Cuando el handshake del WebSocket sea válido, el middleware debe fijar el deadline (instante límite) igual al vencimiento de la cookie y envolver las funciones de enviar y recibir; cualquier intento de enviar cuando la hora actual ya alcanzó el deadline debe cerrar la conexión y lanzar `ClosedByGate` en lugar de enviar el dato. (§9.3)

REQ-037: Cuando se alcance el deadline con una lectura pendiente, el middleware debe enviar un cierre real con código 4401 y seguir esperando la misma tarea de recepción, de modo que el servidor entregue al endpoint la desconexión auténtica, sin cancelar la tarea ni fabricar mensajes. (§9.3)

REQ-038: Cuando el cliente se desconecte antes del deadline, el middleware debe devolver esa desconexión tal cual, sin enviar el cierre 4401. (§9.3)

REQ-039: Si el envío del cierre falla porque la conexión ya murió, el sistema debe registrarlo en nivel de depuración y continuar; el endpoint debe recibir igualmente la desconexión. (§9.3)

REQ-040: Cuando la llamada se cancele desde afuera (por ejemplo, apagado del servicio), el middleware debe cancelar la tarea hija de recepción y propagar la señal `CancelledError` sin ocultarla. (§9.3)

REQ-041: Mientras todo envío (del ThrottledPusher, del endpoint y del cierre) pase por un único `send_lock`, después del cierre cualquier envío debe lanzar `ClosedByGate` para que el ThrottledPusher lo capture y dé de baja a ese cliente. (§9.3)

REQ-042: Cuando se modifique `dashboard/app.js` (§9.4), el manejador de cierre de la conexión debe llevar al navegador a `/login` si el código de cierre es 4401, no reconectar si el código es 4403, y en los demás casos conservar el comportamiento actual de reintento de reconexión. (§9.4)

## Configuración aislada y fail-closed (§10)

Términos de esta sección: **fail-closed** (ante cualquier duda o error de configuración, el
sistema queda cerrado, nunca abierto); **variable de entorno** (dato de configuración que se
inyecta al proceso al arrancar); **BaseSettings** (clase de la librería de configuración que
lee variables de entorno); **estado OK/INVALID** (configuración completa y válida, o no).

REQ-043: El sistema debe leer su configuración de acceso en una clase nueva `AuthSettings` dentro de `src/api/auth/config.py`, sin modificar `src/config.py`; la ausencia de esas variables nunca debe impedir el arranque ni afectar los datos de mercado de Databento ni las señales. (§10)

REQ-044: Cuando falte o sea inválida cualquier variable requerida (por ejemplo JWKS mal formado o sin claves ES256, `TC_SESSION_KEY` con formato incorrecto, lista de usuarios vacía o con UUID mal formado, dirección u origen que no sea https, o TTL menor o igual a 0), el estado debe ser INVALID; en ese estado el sistema debe emitir un único registro `ERROR AUTH_CONFIG_INVALID campos=[…]`, `/login` debe mostrar "Acceso no disponible", `/auth/enter` debe rechazar antes de procesar, y `/health` no debe cambiar. (§10)

REQ-045: El sistema debe validar que `TC_SESSION_KEY` tenga longitud mayor o igual a 43 y solo caracteres `[A-Za-z0-9_-]`, y debe usarla como clave del código HMAC en bytes UTF-8; si no cumple, el estado debe ser INVALID. (§10)

REQ-046 (invariante): Una ruta protegida solo debe llegar a la aplicación si el estado de configuración es OK Y la cookie es válida; no debe existir ninguna variable ni valor que desactive el control de acceso. (§10, §16)

## Seguridad adicional (§11)

Términos de esta sección: **cabecera de seguridad** (instrucción que el servidor envía al
navegador para endurecer su comportamiento); **HSTS** (Strict-Transport-Security — obliga a
usar HTTPS); **CSP** (Content Security Policy — política que limita de dónde puede cargar
recursos la página); **CSRF** (Cross-Site Request Forgery — ataque en que otro sitio provoca
peticiones en nombre del usuario); **token bucket** (mecanismo de límite de tasa con un
"cubo" de fichas que se recargan a ritmo fijo).

REQ-047: En todas las respuestas que pasen por el control de acceso, el sistema debe incluir las cabeceras `Strict-Transport-Security: max-age=31536000`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` y `X-Frame-Options: DENY`. (§11)

REQ-048: El sistema debe enviar `Cache-Control: no-store` (no almacenar en caché) en `/login`, en `/auth/*`, en las respuestas 303/401 del control de acceso y en todas las respuestas protegidas. (§11)

REQ-049: El sistema debe aplicar la CSP estricta `default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src https://svvzrmhvsjalhpitjcvy.supabase.co; form-action 'self'; frame-ancestors 'none'; base-uri 'none'` solamente en `/login` y `/auth/login.js`. (§11)

REQ-050: El sistema debe exigir un `Origin` exacto en `/auth/enter` y `/auth/logout` (si falta → 403) y apoyarse en la cookie `SameSite=Lax` como protección contra CSRF, sin usar un token CSRF adicional. (§11)

REQ-051: El sistema debe proteger `/auth/enter` del abuso con validaciones baratas antes de la verificación criptográfica y con un límite de tasa global en memoria (token bucket de capacidad 20 y recarga de 5 por segundo; al excederse → 429 con `Retry-After: 1`), global (no por dirección IP) y que se reinicia junto con la tarea. (§11)

REQ-052: El sistema debe registrar el ingreso correcto (identificador `sub` y hora) y los rechazos con su código (`bad_sig`, `expired`, `stale`, `bad_iss`, `bad_aud`, `bad_role`, `unknown_kid`, `not_allowed`, `bad_origin`, `bad_body`, `bad_format`, `rate_limited`, `config_invalid`), sin registrar nunca el token, la cookie, la contraseña ni el cuerpo de la petición. (§11)

## Rotación de claves de Supabase (§7)

Términos de esta sección: **rotación de claves** (reemplazo de la clave con la que Supabase
firma los tokens); **standby key** (clave en reserva que se añade antes de activar).

REQ-053: Cuando Hugo ejecute la rotación de claves, el sistema debe seguir aceptando ingresos mientras `TC_AUTH_JWKS_JSON` contenga la clave con la que se firmaron los tokens; después de retirar la clave anterior y esperar al menos 10 minutos, ningún token firmado con ella (que vive 120 segundos más 30 segundos de margen de reloj) debe poder canjearse. (§7)

REQ-054: Si ocurre una rotación inesperada en Supabase, todos los ingresos nuevos deben fallar con el motivo `unknown_kid` mientras las sesiones vigentes siguen funcionando; la recuperación debe consistir en copiar el conjunto de claves públicas actual a la configuración y desplegar. (§7)

## Pruebas exigidas (§14)

Términos de esta sección: **prueba unitaria** (prueba de una pieza aislada, sin red);
**prueba de propiedades** (prueba que verifica una regla general con muchos casos aleatorios);
**prueba de integración** (prueba del sistema completo armado); **TestClient** (cliente de
prueba que llama a la aplicación sin levantar un servidor real); **uvicorn** (servidor web que
ejecuta la aplicación).

REQ-055: El sistema debe contar con pruebas unitarias sin red (con un par de claves de curva elíptica de prueba) que cubran el token, la cookie, la configuración, el límite de tasa y el control de acceso del WebSocket (probado directamente a nivel ASGI con un reloj inyectado), según los casos enumerados en §14. (§14)

REQ-056: El sistema debe contar con una prueba de propiedades que demuestre que, para combinaciones aleatorias de configuración y de cookies, una ruta protegida nunca responde 200 salvo cuando la configuración es OK y la cookie es válida. (§14, §16)

REQ-057: El sistema debe contar con pruebas de integración (con TestClient) que cubran la matriz de rutas, `/auth/enter`, `/auth/logout`, las cabeceras de seguridad, la CSP y `Cache-Control`, el WebSocket con y sin cookie, y la ausencia de token o cookie en los registros. (§14)

REQ-058: El sistema debe contar con una prueba de integración con un servidor uvicorn real (`test_auth_ws_uvicorn.py`, con cookie de vida 3 segundos) que verifique la recepción de mensajes, el cierre con código 4401 al vencer dentro de deadline + 1 segundo, la ausencia de datos después del plazo, el registro "WebSocket connection cleaned up", que el ThrottledPusher quede con 0 conexiones y que la limpieza ocurra en 30 segundos o menos. (§14)

## Riesgos aceptados que condicionan el comportamiento (§17)

REQ-059: El sistema debe operar aceptando explícitamente que no garantiza: el uso único del access_token, la invalidación inmediata de una cookie copiada, el cierre de sesión global, la protección durante el despliegue gradual inicial ni tras una vuelta atrás a la versión pública, ni la disponibilidad del ingreso ante una ráfaga deliberada de peticiones (riesgos R1 a R8 aprobados por Hugo). (§17)

---

## Brechas y contradicciones

Ninguna.
