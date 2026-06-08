"""Image backends. Each adapter calls its provider directly via the ImageBackend
contract in base.py. Adapters use lazy imports for their provider SDKs so this
package imports cleanly without the optional [backends] dependencies installed.
"""
from .base import (
    KNOWN_BACKENDS,
    BackendError,
    BackendNotConfigured,
    GenRequest,
    GenResult,
    ImageBackend,
    load_backend,
)

__all__ = [
    "KNOWN_BACKENDS",
    "BackendError",
    "BackendNotConfigured",
    "GenRequest",
    "GenResult",
    "ImageBackend",
    "load_backend",
]
