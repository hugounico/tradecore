"""TradeCore MVP — Application entry point.

Runs the FastAPI application via uvicorn. All orchestration logic
(component instantiation, simulation/live mode wiring, startup/shutdown)
is handled by the lifespan context manager in src.api.app.

Requirements: RF-01.1, RF-01.4
"""

import uvicorn

from src.api.app import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
