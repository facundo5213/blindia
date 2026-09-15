"""Exceptions shared by the Blind IA capture pipeline.

Callers (e.g. the future physical-button handler) can catch the common
`BlindIAError` base to report any capture failure over audio without
needing to know which stage of the pipeline failed.
"""


class BlindIAError(Exception):
    """Base class for all Blind IA application errors."""


class CameraUnavailableError(BlindIAError):
    """The camera could not be opened: not connected, or already in use."""


class CaptureFailedError(BlindIAError):
    """The camera was open but failed to deliver a usable frame."""


class ImageSaveError(BlindIAError):
    """A captured frame could not be persisted to disk."""


class OcrUnavailableError(BlindIAError):
    """The OCR engine failed to load, or was used before `start()`."""


class OcrFailedError(BlindIAError):
    """The OCR engine was loaded but failed to process an image."""
