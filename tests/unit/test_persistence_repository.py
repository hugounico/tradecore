"""Tests de T3.1 — abstraccion de persistencia (repository) independiente del motor.

Verifica el contrato usando los dobles en memoria:
- guardar y recuperar por id (Capa 1 / Signal Context y LiveSession);
- devolver None cuando el id no existe;
- que los dobles satisfacen estructuralmente los Protocols del contrato.

No se prueba PostgreSQL real, ni migraciones, ni persist-before-publish (Wave 4). No se
modifica `SignalJournal` ni ningun componente protegido.
"""

from src.persistence.repository import (
    InMemoryLiveSessionRepository,
    InMemorySignalContextRepository,
    LiveSessionRepository,
    SignalContextRepository,
)


def test_signal_context_save_and_get() -> None:
    """Un registro de Capa 1 guardado se recupera por su signal_id."""
    repo: SignalContextRepository[dict] = InMemorySignalContextRepository()
    record = {"buy_sell": "BUY", "reference_price": 100.5}
    repo.save("sig-1", record)
    assert repo.get("sig-1") == record


def test_signal_context_get_missing_returns_none() -> None:
    """Recuperar un signal_id inexistente devuelve None (sin excepcion)."""
    repo: SignalContextRepository[dict] = InMemorySignalContextRepository()
    assert repo.get("does-not-exist") is None


def test_signal_context_save_is_idempotent_by_key() -> None:
    """Guardar dos veces el mismo signal_id sobrescribe (misma clave, un registro)."""
    repo: SignalContextRepository[dict] = InMemorySignalContextRepository()
    repo.save("sig-1", {"v": 1})
    repo.save("sig-1", {"v": 2})
    assert repo.get("sig-1") == {"v": 2}


def test_live_session_save_and_get() -> None:
    """Una LiveSession guardada se recupera por su live_session_id."""
    repo: LiveSessionRepository[dict] = InMemoryLiveSessionRepository()
    session = {"instrument": "NQ.c.0", "data_source": "simulation"}
    repo.save("session-1", session)
    assert repo.get("session-1") == session


def test_live_session_get_missing_returns_none() -> None:
    """Recuperar una LiveSession inexistente devuelve None."""
    repo: LiveSessionRepository[dict] = InMemoryLiveSessionRepository()
    assert repo.get("nope") is None


def test_in_memory_doubles_satisfy_contracts_structurally() -> None:
    """Los dobles en memoria cumplen los Protocols runtime_checkable del contrato."""
    assert isinstance(InMemorySignalContextRepository(), SignalContextRepository)
    assert isinstance(InMemoryLiveSessionRepository(), LiveSessionRepository)
