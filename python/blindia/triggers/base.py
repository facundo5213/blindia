"""Common interface for anything that can fire a capture event."""
from abc import ABC, abstractmethod


class Trigger(ABC):
    """A blocking source of capture events.

    Implementations differ only in *what* they wait for (a keypress relayed
    from the host, a physical button read via the Bridge, a web request...).
    Whatever runs after `wait()` returns (main.py's capture call) never
    needs to change when the event source changes.
    """

    @abstractmethod
    def wait(self) -> None:
        """Block until the next trigger event fires."""
        raise NotImplementedError
