# TradeCore MVP — Reglas de Desarrollo

Estas reglas aplican a toda la implementación del MVP (spec `tradecore-mvp`). Complementan los Requirements y Design aprobados — NO los modifican ni amplían el alcance funcional.

---

## 1. Stack Tecnológico

Este MVP usa:
- **Backend:** Python 3.11+ con FastAPI + Uvicorn
- **Frontend:** HTML5 + JavaScript simple (vanilla) + TradingView Lightweight Charts v4 (CDN)
- **Testing:** pytest + Hypothesis (property-based) + httpx
- **Config:** Pydantic Settings + python-dotenv
- **Logging:** Python `logging` estándar (sin dependencias adicionales)
- **Contenedores:** Docker + Docker Compose

**NO reemplazar** FastAPI por Django ni agregar React, Next.js, PostgreSQL, TypeScript, Alpine.js, Tailwind, Redis, Celery ni cualquier otra tecnología no listada en el Design aprobado.

**Roadmap futuro (no aplicar al MVP actual):** Django, PostgreSQL, React, Next.js y TypeScript son el stack priorizado para etapas posteriores por su valor de mercado laboral y portafolio. Al evaluarlos para una etapa futura, considerar simultáneamente: simplicidad para esa etapa, mantenibilidad, escalabilidad, buenas prácticas y valor de aprendizaje — no incorporar una tecnología solo porque sea popular, ni descartarla solo por ser menos simple si la funcionalidad realmente la requiere.

---

## 2. Arquitectura Modular y Reutilizable

- Cada módulo funcional debe ser independiente, con responsabilidades claramente delimitadas y una interfaz limpia hacia el resto del sistema.
- Los módulos deben poder reutilizarse en otro proyecto.
- Para el MVP actual: paquetes de Python bien definidos (`src/connectors/`, `src/pipeline/`, `src/engine/`, `src/api/`, `src/schemas/`). No "Django Apps" — ese patrón se aplicará cuando Django se adopte.
- Evitar acoplamiento innecesario, pero NO crear módulos artificiales: la separación debe corresponder a una responsabilidad funcional real, no a la regla misma.

---

## 3. Aprendizaje Mediante el Código

El código debe ser comprensible y pedagógico para Hugo, quien no es experto en desarrollo ni en trading.

### Comentarios inline

- Toda línea con lógica no trivial debe incluir un comentario explicando **qué hace** y, cuando aporte, **por qué** se usa así.
- Evitar comentarios que solo repitan literalmente el código.
- Convención por lenguaje: Python (`#`), JavaScript (`//`), CSS (`/* */`), HTML (`<!-- -->`), SQL (`--`).
- Priorizar comentarios que ayuden a entender: conceptos de programación, arquitectura, trading, o integración de APIs.

### Explicación de términos técnicos

- Toda sigla o concepto técnico que pueda ser desconocido debe explicarse brevemente donde aparezca, sin asumir conocimiento previo.
- Ejemplos: API, WebSocket, OHLCV, SMA, REST, SDK, ORM, JWT, CI/CD, async/await, dataclass, decorator, generator, iterator.

---

## 4. Desarrollo Incremental — Límites Permanentes

**NO implementar** en esta ni en próximas Tasks del MVP actual:

- Machine Learning / IA predictiva
- Order Book (libro de órdenes)
- TradingView (webhooks, Pine Script)
- QQQ / NDX / opciones / Greeks / GEX
- Backtesting avanzado
- Retraining (reentrenamiento de modelos)
- Ejecución automática de órdenes
- Gestión avanzada de riesgo
- Multi-tenant / autenticación de usuarios
- Microservicios / arquitectura distribuida
- Infraestructura de alta escala (Kafka, Redis pub/sub, Kubernetes)
- Persistencia a largo plazo / base de datos

Estas funcionalidades solo se incorporarán mediante una revisión explícita de Requirements.

**No usar "escalabilidad futura" como excusa para agregar complejidad al MVP actual.**

---

## 5. Rendimiento y Comparación con TradingView

Existe un script de referencia funcionando en TradingView. La comparación se divide en dos niveles:

### a) Rendimiento operacional del sistema (criterio de éxito del MVP)

Disponibilidad, latencia entre cierre de vela y señal disponible, estabilidad, consistencia. Ya cubierto por el diseño actual (WebSocket, throttle 1 seg, reconexión, logging) — no requiere trabajo adicional.

### b) Rendimiento de las señales (etapa futura)

Aciertos/fallos de las señales. Por ahora la comparación es **MANUAL e informal** — Hugo revisa visualmente ambas fuentes.

**NO agregar** al MVP actual: backtesting, seguimiento automatizado de resultados, ni persistencia de señales.

Se puede documentar (por escrito, sin implementar) un protocolo de medición simple como base para una etapa futura, sin inventar valores de referencia que no existan.

---

## 6. Arquitectura de Despliegue — AWS

Priorizar **AWS** como plataforma de despliegue.

### Punto de partida investigado

AWS App Runner despliega directamente desde el Dockerfile existente (tarea 12.2), sin gestión manual de servidores, con HTTPS automático y URL pública estable.

### Antes de implementar

Confirmar contra alternativas (ECS/Fargate) evaluando: simplicidad, costo, seguridad, mantenibilidad, capacidad de ejecutar FastAPI con WebSocket abierto, y posibilidad de evolucionar después. Consultar documentación oficial vigente — no asumir de memoria.

### Requisito operativo

El servicio debe quedar **"siempre activo" (always-on)**, no "scale-to-zero", para que el link esté disponible sin demora cada vez que un socio lo consulte.

---

## 7. Buenas Prácticas de Seguridad en AWS

Aplicar según corresponda:

- Principio de mínimo privilegio en IAM (permisos de identidad y acceso)
- Manejo seguro de secretos y variables de entorno
- Logs centralizados
- Health checks
- Separación entre desarrollo y producción
- Imágenes de contenedor reproducibles
- Apagado correcto de procesos (graceful shutdown)
- Monitoreo básico
- Configuración de red apropiada

**NUNCA almacenar** API Keys, contraseñas, tokens ni credenciales de AWS dentro del código fuente, Steering, Requirements, Design o el repositorio. Usar siempre variables de entorno o servicios de secretos.

---

## 8. Uso de Herramientas de Kiro

No agregar MCP, Powers, Skills u otras herramientas externas solo por estar disponibles — evaluar primero su utilidad real para la tarea concreta frente al costo de contexto y complejidad que agregan.

---

## 9. Regla Permanente de Decisiones Técnicas

Ante cualquier decisión técnica relevante:

1. Identificar las alternativas
2. Explicar brevemente las diferencias
3. Seleccionar una
4. Justificar la elección
5. Verificar que sea coherente con Requirements y Design antes de aplicarla

**No tomar decisiones arquitectónicas importantes en silencio.**

---

## 10. Regla de Consistencia

Si en cualquier momento se detecta una contradicción entre estas reglas de Steering y lo aprobado en Requirements o Design:

1. NO resolver automáticamente ni cambiar Requirements/Design
2. Identificar la contradicción
3. Explicarla claramente
4. Esperar confirmación del usuario antes de implementar algo incompatible
