# TradeCore · Paso 2 Login · Tasks

Plan de implementación derivado de §15 de `design.md`. Ninguna tarea está autorizada por el
solo hecho de figurar aquí: las marcadas `GATE` requieren aprobación explícita de Hugo antes
de ejecutarse; `STOP` detiene y obliga a revisar el Design si la prueba indicada falla.
Una casilla se marca `[x]` solo tras la auditoría de Claude en GitHub; se anota el commit.

**Reglas transversales de pruebas (parche 2026-11-03, aprobado por Hugo):**
- Ninguna prueba de auth se conecta a Databento, Supabase, AWS ni a otro servicio externo.
- Las pruebas de T4–T6 usan una app ASGI de prueba propia (AuthGate + router + rutas falsas); no importan ni ejecutan `src/api/app.py`.
- Nunca `with TestClient(<app real>)`: entrar al contexto ejecuta el `lifespan` (arranque de Databento). La app real solo se usa en T7/T9 sin contexto.
- Parámetros de prueba de más de ~50 caracteres llevan `ids=` cortos (Windows limita `PYTEST_CURRENT_TEST` a 32767 caracteres).
- Para alterar un valor base64url en una prueba, no se toca su último carácter (bits de relleno; lección T2.1).
- Archivos en LF (`.gitattributes`); las variables `TC_AUTH_*`/`TC_SESSION_*` nunca van en `.env` (rompen `Settings` de `src/config.py`).

Convención de pruebas: las pruebas se escriben PRIMERO y deben fallar antes de implementar
(según §14). Archivos nuevos/modificados según §12. Componentes que NO se tocan:
`signal_engine.py`, `throttled_pusher.py`, `dashboard/index.html`, `styles.css`,
`src/config.py`, `Dockerfile`, Databento.

---

- [x] **T0 · GATE — Rama y spec** — hecho: `54d0532`.
  - Objetivo: crear la rama `feat/auth-login` desde `7c743fc` y los 3 archivos de spec (`design.md`, `requirements.md`, `tasks.md`); 1 commit y 1 push.
  - Dependencias: ninguna (punto de partida).
  - Crea/modifica: `.kiro/specs/auth-login/design.md`, `requirements.md`, `tasks.md`. No toca código.
  - Pruebas primero: no aplica (solo documentación); validación por hash, finales de línea LF y ausencia de patrones secretos.
  - Aceptación: árbol limpio; rama en `7c743fc`; `design.md` idéntico al aprobado (hash coincide); commit con 3 archivos; SHA remoto = local.
  - Cubre: la base de todos los REQ (documento fuente).

- [x] **T1 · Configuración de acceso + pruebas** — hecho: `4b45e2d` + T1.1 `f09a8f1` (enteros ≤ 10 dígitos, TTL ≤ 28800, JWKS ≤ 16384 caracteres, listas sin recortar `\n`/`\t`).
  - Objetivo: implementar `AuthSettings` con validación y estados OK/INVALID (fail-closed).
  - Dependencias: T0.
  - Crea/modifica: `src/api/auth/__init__.py`, `src/api/auth/config.py`; `tests/unit/test_auth_config.py`.
  - Pruebas primero: cada variable faltante o inválida → INVALID con el campo correcto; `TC_SESSION_KEY` corta o con caracteres fuera del conjunto → INVALID (deben fallar antes de implementar).
  - Aceptación: todas las pruebas de config pasan; la ausencia de variables no impide arrancar ni afecta Databento/señales.
  - Cubre: REQ-043, REQ-044, REQ-045.

- [x] **T2 · Verificación del token + pruebas** — hecho: `a07230c` + T2.1 `ebd1a13` (pruebas deterministas). Dependencias fijadas: PyJWT 2.15.1, cryptography 50.0.2, cffi 2.1.1, pycparser 3.12.
  - Objetivo: implementar el canje y la validación del access_token (forma, firma ES256, claims, ventana de canje).
  - Dependencias: T1.
  - Crea/modifica: `src/api/auth/tokens.py`; `tests/unit/test_auth_tokens.py`; `requirements.txt` y `requirements-ci.txt` reciben `PyJWT[crypto]` en versión exacta (GATE del diff de requirements al momento de tocarlos).
  - Pruebas primero: token válido; firma alterada; `alg` HS256 y `none`; `kid` desconocido; forma inválida (≠ 3 segmentos, > 4 KB) rechazada sin verificar firma; `exp` vencido; `iat` futuro > 30 s; t − `iat` = 121 s; `iss`/`aud`/`role` incorrectos; `sub` fuera de lista; claim faltante; doble canje dentro de la ventana → mismo token verificado 2 veces (la emisión de 2 cookies se prueba en T6).
  - Aceptación: todas las pruebas de token pasan; el token nunca se guarda ni se registra.
  - Cubre: REQ-017, REQ-022, REQ-023, REQ-024, REQ-025, REQ-026, REQ-053, REQ-054.

- [x] **T3 · Cookie de sesión + pruebas** — hecho: `2feb2f3`.
  - Objetivo: emisión y revalidación de la cookie `__Host-tc_session` (HMAC-SHA256, sin renovación).
  - Dependencias: T1.
  - Crea/modifica: `src/api/auth/session.py`; `tests/unit/test_auth_session.py`.
  - Pruebas primero: cookie válida; firma alterada; vencida; época antigua; `sub` retirado; formato corrupto; clave distinta; sin función de renovación. (La prueba "ninguna respuesta emite `Set-Cookie` salvo `/auth/enter` y `/auth/logout`" pasa a T6, donde existen las rutas.)
  - Aceptación: todas las pruebas de cookie pasan; `exp` fijo al emitir; `verify` no renueva ni emite cookies. La exclusividad de `Set-Cookie` por ruta se valida en T6.
  - Cubre: REQ-027, REQ-028, REQ-029, REQ-030, REQ-031, REQ-032.

- [ ] **T4 · Límite de tasa + middleware HTTP + pruebas**
  - Objetivo: límite de tasa global (token bucket) y la parte HTTP del middleware AuthGate (deny-by-default + cabeceras).
  - Dependencias: T1, T2, T3.
  - Crea/modifica: `src/api/auth/ratelimit.py`, `src/api/auth/middleware.py` (parte HTTP); `tests/unit/test_auth_ratelimit.py`; `tests/unit/test_auth_middleware_http.py` (nuevo, fuera de la lista de §12: matriz HTTP sobre app de prueba).
  - Pruebas primero: 20 solicitudes inmediatas aceptadas, la 21.ª → 429; recarga a 5/s; config INVALID rechaza antes del límite. Matriz HTTP sobre la app de prueba (sin `app.py`): rutas públicas por ruta Y método exactos (§6); método no listado en ruta pública → protegido; protegida sin cookie → `Accept` text/html → 303 `/login`, resto → 401; `/static/*`, `/docs`, `/redoc`, `/openapi.json` y rutas desconocidas protegidas; config INVALID → ninguna protegida pasa aunque la cookie sea válida; cookie válida → pasa con cabeceras de §11 y `Cache-Control: no-store`; redirecciones con `Location` relativa fija sin leer `Host`/`X-Forwarded-*`.
  - Aceptación: ruta pública pasa; protegida sin cookie → §6; con config OK y cookie válida → pasa con cabeceras; cabeceras y `Cache-Control` correctos.
  - Cubre: REQ-006, REQ-007, REQ-008, REQ-009, REQ-019, REQ-033, REQ-034, REQ-047, REQ-048, REQ-051.

- [ ] **T5 · STOP — Middleware WebSocket + pruebas ASGI + prueba con uvicorn real**
  - Objetivo: la parte WebSocket del middleware (opción A': cierre real 4401, un solo `send_lock`, sin cancelar `receive`).
  - Dependencias: T4.
  - Crea/modifica: `src/api/auth/middleware.py` (parte WebSocket); `tests/unit/test_auth_ws_gate.py`; `tests/integration/test_auth_ws_uvicorn.py`; `requirements-ci.txt` recibe el cliente `websockets` para la prueba real si no existe (versión exacta).
  - Las pruebas usan una app ASGI mínima con un WebSocket equivalente a `/ws/chart` y el `ThrottledPusher` real; no importan `app.py` ni conectan a Databento. La prueba uvicorn usa solo loopback local (127.0.0.1) y la app mínima, nunca la app real. Importar `src.api.throttled_pusher` carga `src/api/__init__.py` y con él el módulo `app.py`, sin ejecutar su `lifespan` ni conectar (ya ocurre en todas las pruebas de T1–T3).
  - Pruebas primero (ASGI directo, reloj inyectado): (a) vencimiento con `receive` pendiente → close 4401 y el endpoint recibe el disconnect; (b) broadcast concurrente en el instante del vencimiento → ningún dato posterior; el pusher recibe `ClosedByGate` y da de baja; (c) desconexión antes del plazo → sin 4401; (d) el `send` del close lanza → el endpoint igual recibe disconnect; (e) cancelación externa → tarea hija cancelada y `CancelledError` propagada, sin "Task was destroyed"; (f) mensajes antes del plazo idénticos byte a byte.
  - Prueba con uvicorn real: cierre 4401 al vencer en ≤ deadline + 1 s; sin frames de datos tras el plazo; log "WebSocket connection cleaned up"; pusher con 0 conexiones; limpieza ≤ 30 s. **STOP:** si esta prueba falla, detener y revisar el Design (§9.3, §14).
  - Aceptación: todas las pruebas ASGI y la prueba con uvicorn real pasan.
  - Cubre: REQ-035, REQ-036, REQ-037, REQ-038, REQ-039, REQ-040, REQ-041, REQ-058.

- [ ] **T6 · Rutas y páginas + pruebas**
  - Objetivo: implementar `/`, `/login`, `/auth/login.js`, `/auth/enter`, `/auth/logout` y la página de login (ingreso, cambio de contraseña, sesión activa).
  - Dependencias: T2, T3, T4, T5.
  - Crea/modifica: `src/api/auth/routes.py`, `src/api/auth/static/login.html`, `src/api/auth/static/login.js`; `tests/integration/test_auth_gate.py`.
  - Las pruebas usan una app de prueba con AuthGate + router de auth (sin `app.py`).
  - Pruebas primero: `/` y `/login` con y sin cookie; ninguna respuesta emite `Set-Cookie` salvo `/auth/enter` y `/auth/logout` (movida desde T3); mismo token canjeado 2 veces dentro de la ventana → 2 cookies (R1/REQ-026); `/login` con sesión muestra "Cerrar sesión"; en la app de prueba `/health` permanece público con config OK e INVALID (la equivalencia con el health real se comprueba en T7/T9 y staging); `/auth/enter` válido → Set-Cookie con atributos exactos + 303, sin Origin → 403, Origin ajeno → 403, > 8 KB → 413, content-type distinto → 400, ráfaga → 429; `/auth/logout` con Origin → Max-Age=0 + 303, sin Origin → 403; logs sin token/cookie/cuerpo.
  - Aceptación: todas las pruebas de integración de rutas pasan.
  - Cubre: REQ-001, REQ-002, REQ-003, REQ-004, REQ-005, REQ-010, REQ-011, REQ-012, REQ-013, REQ-014, REQ-015, REQ-018, REQ-020, REQ-021, REQ-049, REQ-050, REQ-052.

- [ ] **T7 · GATE — Diff de `app.py`**
  - Objetivo: registrar el middleware (`app.add_middleware(AuthGate)`) y el router (`app.include_router(auth_router)`) con sus imports.
  - Dependencias: T6.
  - Crea/modifica: `src/api/app.py` (archivo protegido nivel 1; el diff exacto se aprueba en este GATE).
  - Pruebas primero: importar `src.api.app` no conecta a Databento: el arranque vive en el `lifespan` y el módulo ya se importa en todas las pruebas de T1–T3 vía `src/api/__init__.py` (verificado por Claude). Si el diff de T7 agregara código que se ejecute al importar, STOP. Prueba de cableado sobre la app real `src.api.app.app` SIN entrar a su `lifespan` (nunca `with TestClient(app)`): `AuthGate` figura en `app.user_middleware`, las rutas de auth figuran en `app.routes`, una petición sin cookie a `/static/index.html` (cliente sin contexto) responde 303/401 y `/health` conserva su comportamiento. Debe fallar antes del diff.
  - Aceptación: con el diff aplicado, las pruebas de integración pasan y el pipeline de Databento/señales no cambia. No se ejecuta sin aprobación de Hugo del diff.
  - Cubre: REQ-033, REQ-046.

- [ ] **T8 · GATE — Diff de `dashboard/app.js`**
  - Objetivo: aplicar el diff de §9.4 en `ws.onclose` (navegar a `/login` en 4401, no reconectar en 4403, resto igual).
  - Dependencias: T7.
  - Crea/modifica: `dashboard/app.js` (archivo protegido nivel 1; diff exacto de §9.4 aprobado en este GATE).
  - Pruebas primero: verificación local/estática del manejador `ws.onclose` para 4401 → `/login`, 4403 → sin reintentos y corte de red → reintentos previos. La prueba manual en staging (§14) se ejecuta durante T10 (Hito A), una vez creado el servicio; no es precondición de T8.
  - Aceptación local: diff exacto de §9.4 y verificación local/estática aprobados; el resto de `app.js` sin cambios. Aceptación de comportamiento en staging diferida a T10 (Hito A). No se ejecuta el diff sin aprobación de Hugo.
  - Cubre: REQ-016, REQ-042.

- [ ] **T9 · Propiedades + cobertura + regresión**
  - Objetivo: prueba de propiedades del invariante y verificación de cobertura de rutas; suite completa de regresión.
  - Dependencias: T1–T8.
  - Crea/modifica: `tests/property/test_auth_invariant.py`; (verificación de cobertura dentro de `tests/integration/test_auth_gate.py`).
  - Pruebas primero: para combinaciones aleatorias de config (incluidas inválidas) y cookies, una ruta protegida nunca responde 200 salvo config OK y cookie válida; toda ruta de `app.routes` de la app real (importada sin `lifespan`) fuera de la lista pública rechaza sin cookie (lista explícita y métodos de §14).
  - Aceptación: propiedades y cobertura pasan; la regresión completa queda verde (SUITE LANZADA → LEE JUNIT).
  - Cubre: REQ-046, REQ-056, REQ-057, REQ-055 (cierre de la batería unitaria).

- [ ] **T10 · GATE — M1–M5 + Hito A (staging)**
  - Objetivo: provisionar staging y validar en entorno real.
  - Dependencias: T9.
  - Nota (parche 2026-11-03): el servicio `tradecore-staging` NO existe (borrado el 25-oct, A5.1c-3); la URL `tr-fd6c9aff…` de §22 de `design.md` es histórica y no se usa. Staging se CREA: antes de M5 se verifica el flujo de creación de ECS Express Gateway con `--generate-cli-skeleton`; el origen de staging (`TC_AUTH_ALLOWED_ORIGINS`) es la URL que AWS asigne al crear el servicio, por lo que se registra una revisión de TD con ese origen y se actualiza el servicio (M5b/M5c, cada una con su GATE). El execution role lo fija la TD de staging que registramos en M4: `executionRoleArn` = `ecsTaskExecutionRole` (el mismo de la TD de producción, verificado en A5 y P2-INFRA; `tradecore-staging:1` se derivó de ella). Antes de M3 se confirma por lectura que ese rol existe y que el JSON de la TD de M4 lo usa; después de M5 se confirma por lectura que el servicio creado corre con esa TD.
  - Operaciones AWS (cada una con su propio GATE y evidencia; ninguna se repite sin verificar antes por lectura):
    - [ ] **M1 · GATE** — crear secreto `tradecore/staging/session-key` (Hugo, consola AWS). Evidencia: `describe-secret` (solo metadatos) muestra el ARN. Recuperación: programar borrado (ventana 7 días).
    - [ ] **M2 · GATE** — crear secreto `tradecore/session-key` de producción (Hugo, consola AWS). Igual evidencia/recuperación que M1.
    - [ ] **M3 · GATE** — `put-role-policy ecsTaskExecutionRole read-session-secrets` (permiso `GetSecretValue` sobre los 2 ARN exactos). Precondición: M1, M2, `get-role` de `ecsTaskExecutionRole` OK y el JSON de la TD de M4 preparado con `executionRoleArn` = ese rol; si no → STOP. Evidencia: `get-role-policy`. Recuperación: `delete-role-policy`.
    - [ ] **M4 · GATE** — `register-task-definition` de staging con las variables de §10. Evidencia: revisión nueva listada. Recuperación: `deregister-task-definition`.
    - [ ] **M5 · GATE** — crear el servicio Express de staging con M4 (no existe: no es `update-service`). Evidencia: servicio ACTIVE, 1 tarea, health OK, URL asignada. Recuperación: borrar el servicio de staging.
    - [ ] **M5b · GATE** — `register-task-definition` de staging con `TC_AUTH_ALLOWED_ORIGINS` = URL de M5. Evidencia: revisión nueva. Recuperación: `deregister-task-definition`.
    - [ ] **M5c · GATE** — actualizar el servicio de staging a M5b. Evidencia: 1 deployment PRIMARY con M5b y health OK. Recuperación: volver a M4.
  - Pruebas primero: las pruebas §14 se ejecutan en el entorno real de staging (Hito A).
  - Aceptación (Hito A): `TC_AUTH_ALLOWED_ORIGINS` = URL de staging; cuentas Supabase creadas; pruebas §14 verdes en staging, incluida la prueba frontend manual de T8 (4401, 4403 y reconexión por corte de red) y comprobación del execution role real del servicio creado.
  - Cubre: la verificación en entorno real de REQ-001…REQ-058 (evidencia operativa, no un REQ nuevo).

- [ ] **T11 · GATE — M6–M7 + Hito B (producción)**
  - Objetivo: desplegar la versión autenticada a producción y validar el gate de despliegue.
  - Dependencias: T10 (Hito A aprobado).
  - Operaciones AWS (cada una con su propio GATE y evidencia):
    - [ ] **M6 · GATE** — `register-task-definition` de producción = TD :19 + variables de §10 + secreto. Evidencia: revisión nueva listada. Recuperación: `deregister-task-definition`.
    - [ ] **M7 · GATE** — `update-service` de producción a M6. Evidencia: §13.4 (1 solo deployment PRIMARY con M6, `runningCount` = 1). Recuperación/rollback: §13.5 (R3 automático a :19 y R4 manual, ambos aprobados).
  - Pruebas primero: pruebas negativas de referencia sobre TD :19 antes de M7 (hoy 200).
  - Aceptación (Hito B): tras M7, pruebas negativas desde fuera 3 veces separadas ≥ 1 min (`/`, `/static/index.html`, `/static/app.js`, `/docs`, `/openapi.json`, ruta desconocida → 303/401; WS sin cookie → 4401; `/health` → 200) y pruebas positivas (login real, dashboard y WS en vivo, señales fluyendo). Recién entonces se declara Hito B.
  - Cubre: la puesta en producción verificada de REQ-001…REQ-058 (evidencia operativa) y los riesgos R3/R4 (REQ-059).

---

## Trazabilidad REQ → Tarea (resumen)

- REQ-001..REQ-005, REQ-010..REQ-015, REQ-018, REQ-020, REQ-021, REQ-049, REQ-050, REQ-052 → T6
- REQ-006..REQ-009, REQ-019, REQ-033, REQ-034, REQ-047, REQ-048, REQ-051 → T4
- REQ-016, REQ-042 → T8
- REQ-017, REQ-022..REQ-026, REQ-053, REQ-054 → T2
- REQ-027..REQ-032 → T3
- REQ-035..REQ-041, REQ-058 → T5
- REQ-043..REQ-045 → T1
- REQ-033, REQ-046 → T7 (REQ-046 también en T9)
- REQ-055 → T1, T2, T3, T4, T5, T9
- REQ-056, REQ-057 → T9
- REQ-059 → T11 (riesgos aceptados que condicionan el despliegue)
