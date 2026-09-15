"""Interfaz común para cualquier cosa que pueda disparar un evento de captura."""
from abc import ABC, abstractmethod


class Trigger(ABC):
    """Una fuente bloqueante de eventos de captura.

    Las implementaciones difieren solo en *qué* esperan (una tecla
    relayada desde el host, un pulsador físico leído vía el Bridge, un
    request web...). Lo que corre después de que `wait()` retorna (la
    llamada de captura de main.py) nunca necesita cambiar cuando cambia la
    fuente del evento.
    """

    @abstractmethod
    def wait(self) -> None:
        """Bloquea hasta que dispare el próximo evento de trigger."""
        raise NotImplementedError
