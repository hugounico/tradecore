# TradeCore · Paso 2 Login · DESIGN v1.4 (documento autocontenido)

Fecha: 1-nov-2026 · Autor: Claude · Estado: `READY_FOR_APPROVAL` (pendiente de aprobación de Hugo; no autoriza Tasks ni implementación).
Historial: v1.1 (31-oct) consolidó decisiones · v1.2 incorporó P2-INFRA · v1.3 corrigió auditoría #6 · v1.4 corrige auditoría #7 de ChatGPT (§20).
Referencia única: reemplaza a `design_paso2_login_v1`, `decisiones_paso2_login_v3`, `decisiones_paso2_login_v3_1`, `supabase_tradecore-auth_config`, `auditoria_preflight_paso2`.
Marcas: **[FACT]** evidencia verificada · **[DEC]** decisión aprobada por Hugo · **[APROBAR]** requiere decisión de Hugo antes de Tasks.

---

## 1. Objetivo y alcance
Proteger el dashboard de TradeCore (hoy accesible por cualquiera con la URL) con un login real para 3 usuarios (Hugo, Felipe, Carlos), como base permanente que luego crecerá a suscriptores.
Fuera de alcance: pagos, suscripciones, roles, recuperación de contraseña olvidada self-service, botón de logout dentro del dashboard, licencia CME/Databento (en paralelo; un login no otorga derechos de distribución).

## 2. Arquitectura definitiva propuesta [DEC v3, 29-oct]
```
Landing comercial (separada) ──"Entrar"──▶ https://<app>/
TradeCore /login (HTML + login.js, sin librerías)
   1. fetch POST a Supabase /auth/v1/token?grant_type=password  (contraseña: navegador → Supabase)
   2. formulario POST same-origin a /auth/enter con access_token
TradeCore /auth/enter: verifica firma ES256 + claims + lista de UUID → cookie propia 8 h → 303 dashboard
Middleware ASGI deny-by-default (HTTP + WebSocket) revalida la cookie en cada petición
```
Principios: (1) TradeCore nunca ve ni guarda contraseñas. (2) Cero llamadas a Supabase en el camino de cada petición (clave pública fijada). (3) Una falla de autenticación cierra el acceso, nunca el pipeline de Databento/señales. (4) Landing separada.

## 3. Hechos del código y la infraestructura [FACT]
Código de referencia: árbol `5b12ddbee8815ed100b5d6cc7591540243653f23` = `7c743fc` = `origin/main` (refs locales sin fetch) = producción TD :19.

| # | Hecho | Evidencia |
|---|---|---|
| F1 | Solo 3 rutas propias: `GET /health`, mount `/static/*` (index.html, app.js, styles.css), `WS /ws/chart` | app.py:520, 601-603, 606 |
| F2 | `/docs`, `/redoc`, `/openapi.json` activas por defecto, sin control | app.py:517 |
| F3 | No existe `/`; no hay rutas HTTP no-GET; el WS, tras el envío inicial, queda en `while True: await ws.receive_text()` | app.py:672-676 |
| F4 | 1 proceso uvicorn, 1 worker: el pipeline de Databento/señales y la web comparten el mismo bucle de eventos | Dockerfile:20 |
| F5 | Sin middleware (solo lifespan), sin CORS, sin cabeceras de seguridad, sin cookies, sin auth | preflight C8, C9 |
| F6 | `StaticFiles` sin `html=True`: dashboard en `/static/index.html` | app.py:603 |
| F7 | WS: `WebSocketDisconnect` y cualquier excepción se capturan; la baja ocurre siempre en `finally` con `_pusher.remove_connection(ws)` | app.py:674-683 |
| F8 | `ThrottledPusher`: `remove_connection` idempotente; `Lock` propio por conexión; `_send` devuelve `False` ante excepción o timeout (5 s) y `_broadcast` elimina al cliente | throttled_pusher.py:43, 97-101, 168-177, 257-263 |
| F9 | `app.js`: `ws.onclose = function () {…}` sin parámetro; `scheduleReconnect()` reintenta sin tope con backoff `[1000…30000]` ms + jitter | app.js:33, 298-303, 312-327 |
| F10 | WS no valida `Origin` | app.py:606-684 |
| F11 | Config por pydantic `BaseSettings`, instanciada en el lifespan | config.py:3-65; app.py:311 |
| F12 | Access log de uvicorn activo (método, ruta, query) | Dockerfile:20 |
| F13 | La "descarga firmada" de A5 es SALIENTE (S3 prefirmado); no es una ruta servida | file_replay_source.py:34-67 |
| F14 | Sin dependencias JWT/`cryptography` | P2-INFRA K2 |
| F15 | Prod: 1 deployment PRIMARY, TD :19, CANARY 5 %, bake 3+3 min, circuit breaker con rollback + RollbackAlarm | AWS W2 |
| F16 | Express: health `/health`, puerto 8000, una sola entrada pública `https://tr-5c04918a5f4b40d7b065e1c92d5428d0.ecs.us-east-1.on.aws` | AWS W6 |
| F17 | Secreto Databento en Secrets Manager (`tradecore/databento-api-key-ldATB6`); `ecsTaskExecutionRole` con `AmazonECSTaskExecutionRolePolicy` + inline `read-databento-secret` | AWS W7-W9 |
| F18 | `main` local `13e7fa88` es ancestro antiguo: `7c743fc` = `main` + 42 commits, sin divergencia | P2-INFRA K1 |

## 4. Supabase [FACT, verificado con pantallas 30–31 oct]
| Dato | Valor |
|---|---|
| Proyecto | `tradecore-auth`, Free, us-east-1, org "BestAIApps" (transferible) |
| URL / ref | `https://svvzrmhvsjalhpitjcvy.supabase.co` / `svvzrmhvsjalhpitjcvy` |
| `iss` | `https://svvzrmhvsjalhpitjcvy.supabase.co/auth/v1` |
| Clave actual | ES256 (P-256), `kid 0d63f10f-0d16-48e4-8b53-baf614b574cc` |
| Clave anterior | HS256 legado sin revocar (irrelevante: solo se acepta ES256) |
| Registro | público OFF · Confirm email ON · Email ON · Phone/anónimo/manual linking OFF |
| Publishable key | `sb_publishable_SeQlUSsK9WIH8Gag8NjyXA_mD7eq…` (completa en Tasks; pública) |
| Tablas | Ninguna (solo Auth); si se crean, con RLS |
| Ajuste previo a Tasks | Longitud mínima de contraseña = 12 (Authentication → Sign In / Providers → Email) |

JWKS fijado (`TC_AUTH_JWKS_JSON`):
```json
{"keys":[{"alg":"ES256","crv":"P-256","ext":true,"key_ops":["verify"],"kid":"0d63f10f-0d16-48e4-8b53-baf614b574cc","kty":"EC","use":"sig","x":"JPdlg8l77lcPf4wxSMnACUPQ616wVqX2Uu9Q6ZJGLqU","y":"AkjFprNzyDruo5hL-u0_gZ15ELaRcmKb1rGj-ckl8oE"}]}
```
Disponibilidad: Free pausa proyectos inactivos ~1 semana → login de prueba el día de cada demo; Pro (~US$25/mes) al comercializar; sin "ping". Supabase caído/pausado: fallan solo logins nuevos y cambios de contraseña; sesiones vigentes y pipeline siguen.

## 5. Flujo de usuario
1. `GET /` → sin sesión: 303 `/login`; con sesión: 303 `/static/index.html`.
2. `GET /login` → sin sesión: formulario de ingreso + enlace "Cambiar contraseña". Con sesión válida: página "Sesión activa" con "Ir al dashboard" (enlace) y "Cerrar sesión" (formulario POST a `/auth/logout`). Logout voluntario = abrir `/login` y pulsar "Cerrar sesión".
3. **Ingreso** (`login.js`): `fetch` POST a `<SUPABASE_URL>/auth/v1/token?grant_type=password`, cabecera `apikey: <publishable key>`, cuerpo `{email, password}`, `credentials: 'omit'`, `cache: 'no-store'`.
   - 200 → toma solo `access_token`; arma un formulario oculto y lo envía por POST same-origin a `/auth/enter`. Nada se escribe en localStorage, sessionStorage, IndexedDB ni cookies; sin `console.log` de la respuesta. El `refresh_token` nunca se lee ni se copia y se descarta al navegar.
   - 400 → "Credenciales inválidas". Red/5xx → "Servicio de acceso no disponible, intenta más tarde".
4. **Cambio de contraseña** (`login.js`, todo navegador ↔ Supabase; TradeCore no participa): formulario email + contraseña actual + nueva + confirmación. Paso 1: password grant como en 3 → `access_token`. Paso 2: `fetch` PUT `<SUPABASE_URL>/auth/v1/user` con `apikey`, `Authorization: Bearer <access_token>`, cuerpo `{password: nueva}`. 200 → "Contraseña actualizada; ingresa con la nueva" (se descartan los tokens; no se crea sesión de TradeCore). Error → mensaje genérico.
5. `POST /auth/enter` → §7 → éxito: cookie (§8) + 303 `/static/index.html`; fallo: 303 `/login?e=1` (mensaje genérico; motivo solo en log); 429 si se excede el límite (§11).
6. Dashboard: el navegador envía la cookie en cada petición y en el handshake WS.
7. Sesión vencida o revocada: WS cerrado con 4401 → `app.js` navega a `/login` (§9.3–9.4).

## 6. Matriz de rutas
| Ruta | Método | Acceso | Sin sesión válida |
|---|---|---|---|
| `/health` | GET | Pública, sin cambios | 200 igual que hoy |
| `/` (nueva) | GET | Pública | 303 `/login` |
| `/login` (nueva) | GET | Pública | formulario |
| `/auth/login.js` (nueva) | GET | Pública | JS estático |
| `/auth/enter` (nueva) | POST | Pública; exige `Origin` + token; límite de tasa | — |
| `/auth/logout` (nueva) | POST | Pública; exige `Origin` | borra cookie, 303 `/login` |
| `/static/*` (index.html, app.js, styles.css y cualquier archivo futuro) | GET/HEAD | Protegida | `Accept` con text/html → 303 `/login`; resto → 401 |
| `/ws/chart` | WS | Protegida + `Origin` | accept + close 4401 (Origin: 4403) |
| `/docs`, `/redoc`, `/openapi.json` | GET | Protegidas | como `/static/*` |
| Cualquier otra ruta o método | * | Protegida (deny-by-default) | como `/static/*`; con sesión → respuesta normal del app (404/405) |
Reglas: la lista pública coincide por ruta exacta Y método exacto sobre `scope["path"]` decodificado por el servidor; un método no listado en una ruta pública (p. ej. `POST /health`) se trata como protegido. Redirecciones con `Location` relativa fija (`/login`, `/static/index.html`): no dependen de `Host`, `X-Forwarded-*` ni esquema; no se acepta destino como parámetro.

## 7. Canje del token de Supabase (solo en `POST /auth/enter`)
Orden de validación: de lo más barato a lo más caro; la criptografía solo se ejecuta si todo lo anterior pasa.
1. Config OK; si no → rechazo (`config_invalid`) sin procesar el cuerpo.
2. Límite de tasa (§11) → 429 con `Retry-After`.
3. `Origin` presente y exactamente igual a un valor de `TC_AUTH_ALLOWED_ORIGINS`; ausente o distinto → 403.
4. `Content-Type` `application/x-www-form-urlencoded` (si no → 400); cuerpo ≤ 8 KB leído por tramos (exceso → 413); campo `access_token` único (si no → 400).
5. Forma del token: 3 segmentos base64url, longitud ≤ 4 KB; cabecera con `alg` = ES256 y `kid` presente en el JWKS fijado (si no → rechazo sin verificar firma).
6. Firma ES256 (PyJWT) con la clave del `kid`.
7. Claims: `iss` = `<TC_AUTH_SUPABASE_URL>/auth/v1`; `aud` = `authenticated`; `role` = `authenticated`; requeridos `exp`, `iat`, `sub`, `role`; `exp` > t; `nbf` (si existe) ≤ t + 30 s; `iat` ≤ t + 30 s; **ventana de canje** t − `iat` ≤ 120 s; `sub` ∈ `TC_AUTH_ALLOWED_SUBS`.
El token no se guarda ni se registra.

**Reutilización (riesgo R1, redacción corregida):** el servidor no impone uso único. Durante 120 s desde su emisión, una copia de un access token válido puede canjearse más de una vez y cada canje emite una cookie de TradeCore para ese usuario. Poseer el access token NO permite pedir tokens nuevos a Supabase (para eso se necesita el refresh token, que nunca sale de la memoria de la página); sí permite operar contra Supabase como ese usuario hasta su `exp`. Se acepta temporalmente porque no hay almacenamiento de tokens consumidos y el transporte está restringido a POST same-origin por HTTPS (nunca URL ni logs).

**Rotación de claves de Supabase** (manual, la ejecuta Hugo):
1. Supabase → JWT Keys → *Create Standby Key*. Verificar en `<URL>/auth/v1/.well-known/jwks.json` que aparecen ambas claves.
2. `TC_AUTH_JWKS_JSON` con ambas claves; desplegar staging, probar login; desplegar producción, probar login.
3. Rotar en Supabase. Probar login (tokens con el `kid` nuevo).
4. Esperar ≥ 10 min y retirar la clave anterior de la config; desplegar. Fundamento: TradeCore solo canjea tokens de ≤ 120 s (+30 s de reloj); ningún token firmado con la clave anterior es canjeable después de ~150 s. 10 min es margen operativo.
5. Rollback del procedimiento: volver a la TD anterior restaura el JWKS previo.
**Rotación inesperada:** todos los logins nuevos fallan con `unknown_kid`; sesiones vigentes siguen. Recuperación: copiar el JWKS público actual a la config y desplegar.

## 8. Sesión: cookie propia sin estado
- `__Host-tc_session`; `HttpOnly; Secure; SameSite=Lax; Path=/`; sin `Domain`; `Max-Age` = `TC_SESSION_TTL_SECONDS` (28800 = 8 h).
- Valor: `v1.<b64url(json{sub,iat,exp,ep})>.<b64url(HMAC-SHA256(TC_SESSION_KEY, parte1))>`; `hmac.compare_digest`; solo biblioteca estándar.
- `exp` = `iat` + TTL, fijado al emitir la cookie en `/auth/enter`. **No existe renovación:** ninguna ruta reemite ni extiende la cookie; la única forma de obtener una nueva es un nuevo canje en `/auth/enter` (nuevo login con contraseña).
- Revalidación en cada petición HTTP y en el handshake WS: firma, `exp` > t, `ep` = `TC_SESSION_EPOCH`, `sub` ∈ lista.
- **Revocación diferida por despliegue.** Autoridad: variables de la TD (`TC_AUTH_ALLOWED_SUBS`, `TC_SESSION_EPOCH`, `TC_SESSION_KEY`), idénticas en todas las tareas de una TD, cambian solo con un despliegue. Sin estado en memoria.
| Caso | Efecto | Cuándo es efectivo |
|---|---|---|
| Logout voluntario | Borra la cookie en ese navegador | Inmediato en ese navegador; **copias de la cookie siguen válidas hasta `exp`** |
| Copia robada de la cookie | Válida hasta `exp` (≤ 8 h) | Solo la corta un despliegue de revocación |
| Baja individual | Quitar UUID + desplegar (+ deshabilitar en Supabase) | Al terminar el despliegue |
| Revocación global | Subir `TC_SESSION_EPOCH` o rotar `TC_SESSION_KEY` + desplegar | Al terminar el despliegue |
| Reinicio de tarea | Sin efecto (no hay estado) | — |
| Durante el canary | Tareas de la TD anterior aceptan la cookie revocada | Hasta el reemplazo total (minutos) |
| WS abierto | El reemplazo de tareas cierra todas las conexiones | Al terminar el despliegue |
- Deshabilitar o borrar la cuenta en Supabase NO revoca cookies de TradeCore. Esta limitación debe revisarse antes de abrir el servicio a suscriptores.

## 9. Middleware ASGI `AuthGate`
### 9.1 Alcance
Envuelve toda la app, incluido el mount `/static`. Scopes `http` y `websocket` → reglas; `lifespan` → pasa sin tocar. Solo usa `scope["path"]`, `scope["method"]` y las cabeceras `cookie`, `origin`, `accept`, `content-type`, `content-length`; nunca deriva URL ni esquema de cabeceras del cliente.
### 9.2 HTTP
Ruta pública → pasa (las rutas de auth validan lo suyo). Resto: config OK **y** cookie válida → pasa + cabeceras (§11); cualquier otro caso → §6.
### 9.3 WebSocket
**Comparación de opciones (pedida por la auditoría #7):**
| | A' · Middleware con cierre real (elegida) | B · Supervisor en el handler |
|---|---|---|
| Archivos protegidos | Ninguno | `websocket_chart` en `app.py` (nivel 1) |
| Serialización cierre ↔ envíos del pusher | Un solo `send_lock` del middleware cubre TODOS los envíos (pusher y cierre) | El cierre del supervisor no comparte el `Lock` privado del pusher (F8): envíos y cierre pueden intercalarse, salvo que también se modifique `throttled_pusher.py` |
| Cancelaciones | Ninguna | Tarea supervisora que cancelar en cada salida |
| Mensajes sintéticos | Ninguno | Ninguno |
| Limpieza | `finally` existente (F7) | `finally` existente (F7) |
B no reduce riesgo: agrega cambios protegidos y deja una carrera de escritura que A' no tiene.

**A' — protocolo:**
Handshake (antes de llamar al endpoint): si config INVALID, cookie inválida/ausente u `Origin` no permitido → el middleware espera `websocket.connect`, envía `websocket.accept` y luego `websocket.close` con 4401 (4403 si el motivo es `Origin`) y retorna. El endpoint nunca se ejecuta; no se envía ningún dato. Si todo es válido: `deadline = exp` de la cookie; se llama al endpoint con `receive` y `send` envueltos.

Estado por conexión (local a la llamada): `close_sent = False`, `send_lock = asyncio.Lock()`, `recv_task = None`.

`send` envuelto:
```
async with send_lock:
    if close_sent: raise ClosedByGate
    if now >= deadline:
        await _send_close_locked()
        raise ClosedByGate
    await send(message)
    if message["type"] == "websocket.close": close_sent = True
```
`receive` envuelto:
```
if recv_task is None: recv_task = create_task(receive())     # una sola tarea de recepción viva
try:
    if not close_sent:
        remaining = deadline - now
        if remaining > 0:
            done, _ = await asyncio.wait({recv_task}, timeout=remaining)   # wait NO cancela la tarea
            if recv_task in done:
                t, recv_task = recv_task, None
                return t.result()                # propaga excepciones tal cual
        async with send_lock:
            await _send_close_locked()           # cierre real 4401
    t, recv_task = recv_task, None
    return await t                               # el servidor completa con websocket.disconnect
except asyncio.CancelledError:
    if recv_task is not None: recv_task.cancel()
    raise                                        # la cancelación se propaga, nunca se oculta
```
`_send_close_locked` (con `send_lock` tomado): si no `close_sent`: `send({"type":"websocket.close","code":4401})`; si esa llamada lanza (cliente ya caído) se registra en debug y se continúa; `close_sent = True`.

Por qué es correcto:
- **Sin carreras de escritura:** todo envío (pusher, endpoint, cierre) pasa por `send_lock`. Después del cierre, cualquier envío lanza `ClosedByGate`; `ThrottledPusher._send` lo captura y da de baja al cliente (F8).
- **Sin filtración tras el vencimiento:** un `send` con `now >= deadline` cierra y lanza en lugar de enviar. No sale ningún dato después del plazo.
- **Sin cancelaciones ni mensajes inventados:** al vencer, el middleware envía un `websocket.close` real (mensaje ASGI estándar de la aplicación). Según la especificación ASGI, el servidor cierra la conexión y entrega `websocket.disconnect` en el `receive` pendiente, que es la misma tarea que el middleware sigue esperando. El endpoint recibe ese disconnect real → `WebSocketDisconnect` → `finally` → `remove_connection` (F7).
- **Desconexión simultánea:** si el cliente cierra antes del plazo, `recv_task` termina con `websocket.disconnect` y se devuelve tal cual; no se envía 4401.
- **Cierre imposible de transmitir:** si el socket ya murió, el servidor entrega igualmente `websocket.disconnect` en `recv_task`.
- **Cancelación externa** (apagado de la tarea): se cancela la tarea hija y se propaga `CancelledError`.
- **Límite de tiempo hasta la limpieza:** lo fija el cierre del servidor (handshake de cierre o su timeout en uvicorn). Se mide en la prueba con servidor real (§14) y debe ser ≤ 30 s; si no lo es, la tarea se detiene y se revisa el Design.
- Ningún archivo protegido cambia; el formato de mensajes no cambia.
### 9.4 `app.js` (nivel 1) [APROBAR]
Diff exacto sobre app.js:298-303:
```js
// antes
ws.onclose = function () {
    if (thisWs !== ws) { return; }
    clearWatchdog();
    refreshStatus();
    scheduleReconnect();
};
// después
ws.onclose = function (ev) {
    if (thisWs !== ws) { return; }
    clearWatchdog();
    refreshStatus();
    if (ev && ev.code === 4401) { window.location.href = '/login'; return; }
    if (ev && ev.code === 4403) { return; }
    scheduleReconnect();
};
```

## 10. Configuración aislada y fail-closed
Clase nueva `AuthSettings(BaseSettings)` en `src/api/auth/config.py`; `src/config.py` NO se modifica. Variables opcionales en el esquema: su ausencia nunca impide arrancar ni afecta Databento/señales.
| Variable | Contenido | Sensible | Origen AWS |
|---|---|---|---|
| `TC_AUTH_SUPABASE_URL` | `https://svvzrmhvsjalhpitjcvy.supabase.co` | No | env TD |
| `TC_AUTH_PUBLISHABLE_KEY` | `sb_publishable_…` | No | env TD |
| `TC_AUTH_JWKS_JSON` | JWKS de §4 | No | env TD |
| `TC_AUTH_ALLOWED_SUBS` | UUID separados por coma | Dato personal | env TD |
| `TC_AUTH_ALLOWED_ORIGINS` | prod: URL de F16; staging: su URL | No | env TD |
| `TC_SESSION_KEY` | 64 caracteres `[A-Za-z0-9]` aleatorios (≈ 381 bits) | **Sí** | Secrets Manager `tradecore/session-key` (prod), `tradecore/staging/session-key` (staging) |
| `TC_SESSION_EPOCH` | entero, inicial `1` | No | env TD |
| `TC_SESSION_TTL_SECONDS` | `28800` (default) | No | env TD |
`TC_SESSION_KEY` se usa como clave HMAC en bytes UTF-8 (HMAC acepta claves de cualquier longitud; la seguridad depende de la entropía, no de que sea binaria). Validación: longitud ≥ 43 y solo `[A-Za-z0-9_-]`; si no → INVALID.
Estados: **OK** (todas presentes y válidas) o **INVALID** (cualquier otra combinación: falta, JWKS mal formado o sin claves ES256, clave fuera de formato, lista vacía o UUID mal formado, URL u origen no https, TTL ≤ 0). INVALID → log único `ERROR AUTH_CONFIG_INVALID campos=[…]`; `/login` muestra "Acceso no disponible"; `/auth/enter` rechaza antes de procesar; `/health` sin cambios.
**Invariante:** una ruta protegida solo llega al app si `estado == OK` **y** la cookie es válida. No existe variable ni valor que desactive el gate (prueba de propiedades, §14).

## 11. Seguridad adicional
- Cabeceras en todas las respuestas que pasan por el gate: `Strict-Transport-Security: max-age=31536000`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `X-Frame-Options: DENY`.
- `Cache-Control: no-store` en `/login`, `/auth/*`, en las respuestas 303/401 del gate y en todas las respuestas protegidas.
- CSP estricta solo en `/login` y `/auth/login.js`: `default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src https://svvzrmhvsjalhpitjcvy.supabase.co; form-action 'self'; frame-ancestors 'none'; base-uri 'none'`.
- CSRF: no hay operaciones autenticadas que cambien estado salvo logout. `/auth/enter` y `/auth/logout` exigen `Origin` exacto (ausente → 403); cookie `SameSite=Lax`.
- **Protección contra abuso de `/auth/enter`:** (1) validaciones baratas antes de la criptografía (§7, pasos 1–5); (2) límite de tasa global en memoria (token bucket: capacidad 20, recarga 5 por segundo; exceso → 429 con `Retry-After: 1`). Motivo: la web y el pipeline de señales comparten un solo proceso (F4); una ráfaga de verificaciones criptográficas no debe quitarle tiempo de CPU al pipeline. Es global (no por IP) porque `X-Forwarded-For` lo controla el cliente; el estado se reinicia con la tarea (aceptable, F4). Costo real de la verificación: se mide en las pruebas (informativo). Riesgo R8: una ráfaga puede bloquear temporalmente logins legítimos, nunca el dashboard de sesiones vigentes ni el pipeline.
- Logs: ingreso OK (`sub`, hora); rechazo con código (`bad_sig`, `expired`, `stale`, `bad_iss`, `bad_aud`, `bad_role`, `unknown_kid`, `not_allowed`, `bad_origin`, `bad_body`, `bad_format`, `rate_limited`, `config_invalid`). Excepciones del módulo de auth sin argumentos de la petición. Nunca token, cookie, contraseña ni cuerpo.
- Administración: MFA en la cuenta de Supabase de Hugo; verificar MFA en AWS y GitHub.

## 12. Archivos
| Archivo | Cambio | Protección |
|---|---|---|
| `src/api/auth/__init__.py`, `config.py`, `tokens.py`, `session.py`, `ratelimit.py`, `middleware.py`, `routes.py` | Nuevos | — |
| `src/api/auth/static/login.html`, `login.js` | Nuevos | — |
| `src/api/app.py` | `app.add_middleware(AuthGate)` + `app.include_router(auth_router)` + imports | Nivel 1 (autorizado en principio 26-oct; diff exacto a aprobar en Tasks) |
| `dashboard/app.js` | Diff §9.4 | Nivel 1 [APROBAR] |
| `requirements.txt` | `PyJWT[crypto]` versión exacta | — |
| `requirements-ci.txt` | `PyJWT[crypto]` + cliente `websockets` para la prueba con servidor real (versiones exactas; si `websockets` ya existe, se reutiliza) | — |
| `tests/unit/test_auth_tokens.py`, `test_auth_session.py`, `test_auth_config.py`, `test_auth_ratelimit.py`, `test_auth_ws_gate.py`; `tests/property/test_auth_invariant.py`; `tests/integration/test_auth_gate.py`, `test_auth_ws_uvicorn.py` | Nuevos | — |
No se tocan: `signal_engine.py`, `throttled_pusher.py`, `dashboard/index.html`, `styles.css`, `src/config.py`, `Dockerfile`, Databento. Aceptación/cierre del WS = comportamiento nivel 2 aprobado el 26-oct [DEC].

## 13. Git, infraestructura y despliegue
### 13.1 Git
Rama `feat/auth-login` desde `7c743fc`. Antes de crearla (escritura, con aprobación): árbol limpio, `git rev-parse 7c743fc^{tree}` = `5b12ddbe…`. Flujo: PR a `develop` → PR a `main`.
### 13.2 Operaciones AWS mutantes
Inventario, NO autorización: cada operación tiene su propio gate de aprobación y evidencia. Ninguna se repite si su resultado queda UNKNOWN sin verificarlo antes con lecturas.
| # | Operación | Quién | Precondición | Evidencia de éxito | Recuperación |
|---|---|---|---|---|---|
| M1 | Crear secreto `tradecore/staging/session-key` | Hugo, consola AWS | El nombre no existe | Kiro: `describe-secret` (solo metadatos) muestra el ARN | Programar borrado (ventana 7 días) |
| M2 | Crear secreto `tradecore/session-key` (prod) | Hugo, consola AWS | Igual | Igual | Igual |
| M3 | `put-role-policy ecsTaskExecutionRole read-session-secrets` (`GetSecretValue` sobre los 2 ARN exactos) | Kiro | M1, M2; exec role de staging = `ecsTaskExecutionRole` | `get-role-policy` muestra el documento | `delete-role-policy` |
| M4 | `register-task-definition` staging (+ variables §10) | Kiro | M1, M3 | Revisión nueva listada | `deregister-task-definition` |
| M5 | `update-service` staging a M4 | Kiro | M4 | 1 deployment PRIMARY con M4, health OK | `update-service` a la TD anterior de staging |
| M6 | `register-task-definition` prod = TD :19 + variables §10 + secreto | Kiro | Hito A aprobado | Revisión nueva listada | `deregister-task-definition` |
| M7 | `update-service` prod a M6 | Kiro | M6 | §13.4 | §13.5 |
**Generación e inyección del secreto (M1, M2):** Hugo genera el valor en su gestor de contraseñas (64 caracteres, letras y dígitos) y lo pega en la consola de AWS (*Store a new secret* → *Other type of secret* → *Plaintext*). El valor nunca pasa por la línea de comandos, el historial del shell, la salida de Kiro, el chat ni archivos. Kiro solo verifica metadatos. Costo: 2 secretos ≈ US$0,80/mes.
### 13.3 Hito A — staging
`TC_AUTH_ALLOWED_ORIGINS` = URL de staging; cuentas Supabase creadas; pruebas §14 en entorno real.
### 13.4 Hito B — producción (gate de despliegue inicial)
1. Antes de M7: pruebas negativas de referencia sobre la TD :19 (hoy devuelven 200).
2. Durante el canary: la TD :19 sigue sirviendo sin login. No se comunica "protegido" a nadie.
3. Tras M7: 1 solo deployment, PRIMARY, con M6, y `runningCount` = 1 con esa TD.
4. Pruebas negativas desde fuera, 3 veces separadas ≥ 1 min: `/`, `/static/index.html`, `/static/app.js`, `/docs`, `/openapi.json`, una ruta desconocida → 303/401; WS sin cookie → 4401; `/health` → 200.
5. Pruebas positivas: login real, dashboard y WS en vivo, señales fluyendo.
6. Recién entonces se declara Hito B y se informa a socios.
### 13.5 Rollback
- **Automático durante el primer despliegue** (circuit breaker / RollbackAlarm, F15): vuelve a TD :19 (pública) sin intervención. [APROBAR, R3]
- **Manual a TD :19 después de Hito B:** regresión de seguridad; solo con autorización explícita de Hugo en ese momento, registrada. [APROBAR, R4]
- **Desde Hito B**, la referencia de rollback seguro es M6 (primera TD autenticada).
### 13.6 Cuentas Supabase
Hugo crea cada cuenta con *Auto Confirm User* y una contraseña inicial temporal aleatoria (≥ 20 caracteres, gestor de contraseñas), entregada una sola vez por un canal efímero distinto del email. Cada usuario la cambia en su primer ingreso con "Cambiar contraseña" (§5.4); desde ese momento Hugo ya no la conoce. Olvido de contraseña: Hugo asigna una nueva temporal por el mismo procedimiento. No se usan invitaciones por email (§18). Registro público OFF. Procedimiento temporal: antes de abrir a suscriptores se diseñará el alta y la recuperación self-service.
Baja de usuario: quitar UUID (despliegue) + deshabilitar en Supabase.

## 14. Pruebas críticas de seguridad y regresión
**Unitarias** (sin red; par de claves EC del test):
- Token: válido; firma alterada; `alg` HS256 y `none`; `kid` desconocido; forma inválida (≠ 3 segmentos, > 4 KB) rechazada sin llamar a la verificación de firma; `exp` vencido; `iat` futuro > 30 s; t − `iat` = 121 s; `iss`, `aud`, `role` incorrectos; `sub` fuera de lista; claim faltante; doble canje dentro de la ventana → 2 cookies (comportamiento documentado).
- Cookie: válida; firma alterada; vencida; época antigua; `sub` retirado; formato corrupto; clave distinta; ninguna respuesta del gate emite `Set-Cookie` salvo `/auth/enter` y `/auth/logout` (sin renovación).
- Config: cada variable faltante o inválida → INVALID con el campo correcto; `TC_SESSION_KEY` corta o con caracteres fuera del conjunto → INVALID.
- Límite de tasa: 20 solicitudes inmediatas aceptadas, la 21.ª → 429; recarga a 5/s; config INVALID rechaza antes del límite.
- WS gate (ASGI directo, `send`/`receive` simulados, reloj inyectado): (a) vencimiento con `receive` pendiente → se envía close 4401 y el `receive` del endpoint devuelve el `websocket.disconnect` que entrega el simulador; (b) broadcast concurrente en el instante del vencimiento → ningún mensaje de datos posterior al plazo; el pusher recibe `ClosedByGate` y da de baja; (c) cliente se desconecta antes del plazo → sin 4401; (d) el `send` del close lanza → el endpoint igual recibe disconnect; (e) cancelación externa → la tarea hija queda cancelada y `CancelledError` se propaga; sin advertencias "Task was destroyed"; (f) mensajes antes del plazo idénticos byte a byte.
**Propiedades:** para combinaciones aleatorias de config (incluidas inválidas) y cookies (válidas, alteradas, vencidas), una ruta protegida nunca responde 200 salvo con config OK y cookie válida.
**Integración (TestClient):**
- Cobertura: toda ruta de `app.routes` fuera de la lista pública rechaza sin cookie; lista explícita: `/static/index.html`, `/static/app.js`, `/static/styles.css`, `/static/inexistente`, `/docs`, `/redoc`, `/openapi.json`, `/x`, `/static/../health`, `//static/index.html`, `/STATIC/index.html`, `/static/%2e%2e/health`; métodos `POST`, `PUT`, `DELETE`, `OPTIONS`, `HEAD` sobre `/static/index.html`, `/health` y `/`.
- `/` y `/login` con y sin cookie; `/login` con sesión muestra "Cerrar sesión".
- `/health` idéntico a hoy con config OK e INVALID.
- `/auth/enter`: válido → Set-Cookie con atributos exactos (`Max-Age` = TTL) + 303; sin `Origin` → 403; `Origin` ajeno → 403; > 8 KB → 413; content-type distinto → 400; ráfaga → 429.
- `/auth/logout`: con `Origin` → `Max-Age=0` + 303; sin `Origin` → 403.
- Cabeceras de seguridad, `Cache-Control: no-store` y CSP donde corresponde.
- WS: sin cookie → 4401 sin datos; `Origin` ajeno → 4403; con cookie → mensajes idénticos a hoy.
- Logs: ninguna línea contiene token, cookie ni el valor de `access_token`.
**Integración con servidor real (`test_auth_ws_uvicorn.py`):** uvicorn real en `127.0.0.1` (puerto libre) con la app y TTL de cookie de 3 s; cliente `websockets` con cookie y `Origin` válidos: recibe mensajes; al vencer recibe cierre 4401 en ≤ deadline + 1 s; ningún frame de datos después del plazo; el log muestra "WebSocket connection cleaned up"; el pusher queda con 0 conexiones; tiempo hasta la limpieza ≤ 30 s.
**Frontend (manual en staging):** 4401 → `/login`; 4403 → sin reintentos; corte de red → reintenta como hoy; logout + botón "atrás" no muestra el dashboard; cambio de contraseña funciona y la anterior deja de servir.
**Regresión** completa (SUITE LANZADA → LEE JUNIT).
**Staging (Hito A) y producción (Hito B):** §13.3–13.4.

## 15. Secuencia de implementación (para Tasks)
T0 rama `feat/auth-login` (aprobación) → T1 config + tests → T2 tokens + tests → T3 session + tests → T4 ratelimit + middleware HTTP + tests → T5 middleware WS + tests ASGI + prueba con uvicorn real (si falla: detener y revisar Design) → T6 routes + login.html/js (ingreso, cambio de contraseña, sesión activa) + tests → T7 diff `app.py` (aprobación) → T8 diff `app.js` (aprobación) → T9 propiedades + cobertura + regresión → T10 M1–M5 + Hito A → T11 M6–M7 + Hito B.

## 16. Garantías que ofrece el diseño
1. Con config OK, sin cookie válida ninguna ruta salvo las 6 públicas entrega contenido; el WS no envía datos.
2. Ninguna configuración, completa o incompleta, abre el acceso (invariante §10).
3. TradeCore no ve ni guarda contraseñas; no persiste ni registra tokens.
4. Una falla de autenticación o de Supabase no afecta Databento, velas ni señales; el límite de tasa protege la CPU del proceso compartido.
5. Un WS autenticado no recibe ningún dato después del vencimiento de su cookie y queda limpio (pusher sin la conexión) en ≤ 30 s.
6. La revocación es efectiva al terminar un despliegue (diferida), en todas las tareas.
7. Después del primer ingreso, Hugo no conoce las contraseñas de los demás usuarios.
**No garantiza:** uso único del access token; invalidación inmediata de una cookie copiada; logout global; protección durante el canary inicial ni tras un rollback a :19; disponibilidad del login ante una ráfaga deliberada.

## 17. Riesgos residuales que requieren aprobación
| # | Riesgo | Propuesta | Estado |
|---|---|---|---|
| R1 | Una copia de un access token válido puede canjearse varias veces durante 120 s | Aceptar temporalmente | [APROBAR] |
| R2 | Cookie copiada válida hasta 8 h; logout local; revocación diferida por despliegue | Aceptar para 3 usuarios; revisar antes de suscriptores | [APROBAR] |
| R3 | Rollback automático del primer despliegue vuelve a :19 (público) | Aceptar como "despliegue fallido" | [APROBAR] |
| R4 | Rollback manual a :19 tras Hito B | Solo con autorización explícita en el momento | [APROBAR] |
| R5 | Hugo conoce la contraseña inicial hasta que cada usuario la cambia | Aceptar con canal efímero y cambio en el primer ingreso | [APROBAR] |
| R6 | Cambio de 2 líneas en `app.js` (nivel 1) | Aprobar diff §9.4 | [APROBAR] |
| R7 | Supabase Free puede pausarse | Login de prueba el día de cada demo | Vigente |
| R8 | Una ráfaga contra `/auth/enter` puede bloquear logins nuevos por segundos | Aceptar (protege el pipeline) | [APROBAR] |

## 18. Archivos protegidos afectados
`src/api/app.py` (nivel 1: registrar middleware y router) y `dashboard/app.js` (nivel 1: diff §9.4). Comportamiento WS nivel 2 (aceptación y cierre) ya aprobado el 26-oct. Ningún otro.

## 19. Bloqueadores reales antes de Tasks
1. Aprobación de R1–R6 y R8.
2. Publishable key completa y URL de staging.
3. Longitud mínima de contraseña = 12 en Supabase (§4) y confirmar que staging usa `ecsTaskExecutionRole` (lectura al inicio de Tasks).

## 20. Alternativas descartadas (no reabrir sin evidencia técnica nueva)
| Alternativa | Por qué se descartó |
|---|---|
| Usuarios y contraseñas dentro de TradeCore | Contraseñas en la app de trading; habría que rehacerlo con suscriptores. |
| Cognito en el ALB | ECS Express administra el balanceador; cambios externos pueden revertirse. Despriorizado, no imposible. |
| Login en la landing + Edge Function + JWT HMAC propio | Secreto compartido y más piezas; innecesario con claves asimétricas. |
| Token en la URL | Historial y access log de uvicorn. |
| JWT de Supabase en cada petición | El WS del navegador no envía `Authorization`; `<script>`/CSS no llevan cabeceras; obliga a tocar el dashboard y renovar cada hora. |
| Sesiones opacas en memoria del servidor | 401 aleatorios en canary; cada reinicio cierra sesiones; exige Redis/PostgreSQL. |
| Registro de tokens consumidos | Exige estado compartido; la ventana de 120 s acota el riesgo para 3 usuarios. |
| JWKS descargado en ejecución con caché | Red, caché y modos de falla para una rotación manual y rara. |
| Autorizar por email | Cambia; el UUID es estable. |
| Invitaciones por email | SMTP por defecto de Supabase restringe destinatarios; página extra. |
| supabase-js por CDN | `fetch` basta; sin CDN/SRI; CSP más estricta. |
| Timeout por inactividad | Pantalla en vivo con WS permanente. |
| "Ping" al plan Free | No garantiza evitar la pausa. |
| Modo "auth desactivado" | Riesgo de activarlo por error; los tests construyen la config. |
| Límite de tasa por IP | `X-Forwarded-For` lo controla el cliente; global es más simple y suficiente. |
| Redis para el límite de tasa | Un solo proceso (F4): el estado en memoria basta. |
| Token CSRF | No hay operaciones autenticadas que cambien estado salvo logout; `Origin` exacto + SameSite cubren login/logout. |
| Rol `dashboard_viewer` | Un solo nivel de acceso: la lista de UUID es el rol. |
| Auditar rutas de red alternativas | El candado vive en la app; W6 confirma una sola entrada. |
| Rama base desde `main` local | 42 commits atrás (F18). |
| Modificar `read-databento-secret` | Mezcla permisos; política nueva acotada. |
| Supervisor WS en el handler (opción B) | Toca `app.py` y no comparte el lock del pusher: carrera de escritura (§9.3). |
| Cancelar el `receive` e inyectar `websocket.disconnect` (v1.3) | Innecesario: el cierre real 4401 hace que el servidor entregue el disconnect auténtico. |
| Crear el secreto por CLI con el valor como argumento | Expone el valor en la línea de comandos e historial; se usa la consola de AWS. |

## 21. Cambios respecto de v1.3 (auditoría #7 de ChatGPT)
| # | Punto de ChatGPT | Resolución |
|---|---|---|
| 1 | Algoritmo WS no demostrado; cancelación y disconnect artificial riesgosos | Aceptado. Se eliminan la cancelación y el disconnect inventado: al vencer se envía un cierre real 4401 y se sigue esperando la misma tarea de `receive`, que el servidor completa con el disconnect auténtico. Un único lock cubre todos los envíos. `CancelledError` se propaga. Prueba nueva con uvicorn real (§9.3, §14). |
| 2 | Comparar con supervisor en el handler (B) | Comparado; B descartado: toca `app.py` y deja una carrera con el lock privado del pusher (§9.3). |
| 3 | Secreto como argumento de CLI | Aceptado. Hugo lo crea en la consola de AWS desde su gestor de contraseñas; nunca pasa por terminal, Kiro ni chat (§13.2). Formato y validación definidos (§10). |
| 4 | "ES256 cuesta como `/health`" sin medición | Aceptado: afirmación eliminada. Se agregan validaciones baratas antes de la criptografía y un límite de tasa global en memoria, justificado por el proceso compartido con el pipeline (§7, §11). |
| 5 | Access token ≠ poder pedir tokens nuevos | Aceptado: R1 redactado con exactitud (§7). |
| 6 | TTL absoluto sin renovación accidental | Explícito: `exp` fijo al emitir, ninguna ruta reemite la cookie; prueba de ausencia de `Set-Cookie` (§8, §14). |
| 7 | Usuarios deberían elegir su contraseña | Aceptado: "Cambiar contraseña" en `/login`, navegador ↔ Supabase, sin tocar TradeCore; R5 reducido al período hasta el primer ingreso (§5.4, §13.6). |
| 8 | Rollback automático requiere aceptación expresa | Ya figuraba como [APROBAR] R3; se mantiene. |
| 9 | Las 7 operaciones AWS no son autorización | Explícito en §13.2. |

Resultado: `AUTH_P2_DESIGN_V14 = READY_FOR_APPROVAL`

## 22. Aprobación y datos finales (1-nov-2026)
- Estado: APROBADO por Hugo el 1-nov-2026. Riesgos R1–R8 aprobados (R7 = pausa del plan Free de Supabase, mitigado con login de prueba el día de cada demo; R4: rollback manual a :19 después de Hito B solo con autorización explícita de Hugo en ese momento). Las marcas [APROBAR] de este documento quedan resueltas. Esta sección es un anexo: no modifica el texto de §1–§21.
- `TC_AUTH_PUBLISHABLE_KEY` = `sb_publishable_SeQlUSsK9WIH8Gag8NjyXA_mD7eqrRm`
- `TC_AUTH_ALLOWED_ORIGINS` staging = `https://tr-fd6c9aff20f74dffa0a4616dbd2456f9.ecs.us-east-1.on.aws` (servicio `tradecore-staging`; dashboard en `/static/index.html`)
- `TC_AUTH_ALLOWED_ORIGINS` producción = `https://tr-5c04918a5f4b40d7b065e1c92d5428d0.ecs.us-east-1.on.aws`
- Supabase: longitud mínima de contraseña = 12 (configurado por Hugo).
- Pendientes: exec role de staging (se verifica en T0); UUID de las 3 cuentas (antes de T10).
