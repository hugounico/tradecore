"""SignalJournal — registro (journal) de SignalRecords para trazabilidad (RF-E1-03).

Modulo NUEVO. Se define contra una interfaz ABSTRACTA `JournalWriter` (el journal no
sabe COMO se guardan los registros, solo pide "escribe este SignalRecord"). Esto permite
intercambiar el destino de escritura sin tocar el resto del sistema (bajo acoplamiento).

Implementacion por DEFECTO (para tests y desarrollo): `InMemoryJournalWriter`, una
estructura en memoria (una lista), SIN persistencia a disco. Esto desbloquea las tareas
dependientes sin comprometer aun el formato definitivo de persistencia.

IMPORTANTE — alcance de persistencia [SEGURO]: cualquier escritor persistente (por
ejemplo JSONL — JSON Lines, un registro JSON por linea, en disco) es una decision de
persistencia definitiva marcada [SEGURO]. Se agregara MAS ADELANTE como otra
implementacion de `JournalWriter` (otra subclase); NO es el default y NO se implementa
aqui. El default de pruebas es exclusivamente en memoria.
"""

from abc import ABC, abstractmethod

from src.schemas.signal_record import SignalRecord


class JournalWriter(ABC):
    """Interfaz abstracta: define COMO se escribe un SignalRecord, sin fijar el destino.

    ABC (Abstract Base Class — clase base abstracta): no se instancia directamente; sirve
    de contrato. Cualquier destino concreto (memoria, disco, etc.) implementa `write`.
    """

    @abstractmethod
    def write(self, record: SignalRecord) -> None:
        """Persiste (o almacena) un SignalRecord. Implementado por cada subclase concreta."""
        raise NotImplementedError


class InMemoryJournalWriter(JournalWriter):
    """Escritor por defecto: guarda los SignalRecords en una lista en memoria.

    Sin persistencia a disco. Util para tests y desarrollo. Se pierde al reiniciar el
    proceso — por eso NO es apto para reproducibilidad historica de largo plazo (esa
    necesidad se cubrira con un escritor persistente separado, parte del [SEGURO]).
    """

    def __init__(self) -> None:
        # Lista interna que acumula los registros en el orden en que se escriben.
        self._records: list[SignalRecord] = []

    def write(self, record: SignalRecord) -> None:
        # Almacenamiento trivial: agregar al final de la lista.
        self._records.append(record)

    @property
    def records(self) -> list[SignalRecord]:
        """Devuelve una copia de los registros almacenados (evita mutacion externa)."""
        # Copia defensiva: quien lea no puede alterar la lista interna del writer.
        return list(self._records)


class SignalJournal:
    """Fachada de registro de senales: recibe SignalRecords y los delega al JournalWriter.

    No conoce el destino concreto: depende solo de la interfaz `JournalWriter`. Si no se
    pasa un writer, usa `InMemoryJournalWriter` (el default en memoria para tests/dev).
    """

    def __init__(self, writer: JournalWriter | None = None) -> None:
        # Inyeccion de dependencia: por defecto, escritor en memoria (no persistente).
        self._writer: JournalWriter = writer if writer is not None else InMemoryJournalWriter()

    def record(self, signal_record: SignalRecord) -> None:
        """Registra un SignalRecord delegando en el escritor configurado."""
        self._writer.write(signal_record)

    @property
    def writer(self) -> JournalWriter:
        """Acceso al escritor subyacente (util en tests para inspeccionar lo registrado)."""
        return self._writer
