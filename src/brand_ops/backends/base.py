"""The image backend contract.

Every backend (openrouter, gemini, openai, recraft, flux) is a real adapter that
calls its provider directly. None of them is a wrapper around another script. They all
satisfy the ImageBackend protocol, so the engine, the CLI, the MCP server, the
REST API, and the autonomy agents all drive any backend the same way.

A backend module lives at brand_ops/backends/<name>.py and exposes a
`build(settings) -> ImageBackend` factory. load_backend imports it by name, so
adding a backend never requires editing a shared registry file.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

KNOWN_BACKENDS = ("litellm", "openrouter", "gemini", "openai", "recraft", "flux")


@dataclass
class GenRequest:
    """One generation request, backend agnostic."""

    prompt: str
    width: int
    height: int
    transparent: bool = False
    reference_images: list[Path] = field(default_factory=list)
    count: int = 1
    # Optional knobs a backend may honor or ignore.
    resolution_hint: str | None = None  # e.g. "1K" / "2K" / "4K" for gemini
    vector: bool = False                # request SVG output where supported (recraft)
    extra: dict = field(default_factory=dict)


@dataclass
class GenResult:
    """The output of a generation. images holds raw bytes, one entry per count."""

    images: list[bytes]
    mime: str  # "image/png", "image/svg+xml", ...
    backend: str
    model: str | None = None
    cost_cents: float | None = None
    meta: dict = field(default_factory=dict)


class BackendError(Exception):
    """Any backend failure that should bubble up with a clear message."""


class BackendNotConfigured(BackendError):
    """Required credentials or endpoint are missing. health() returns False."""


@runtime_checkable
class ImageBackend(Protocol):
    name: str

    def health(self) -> bool:
        """Cheap check that this backend can run (credentials or endpoint present)."""
        ...

    def generate(self, req: GenRequest) -> GenResult:
        """Generate images for the request, or raise BackendError."""
        ...


def load_backend(name: str, settings) -> ImageBackend:
    if name not in KNOWN_BACKENDS:
        known = ", ".join(KNOWN_BACKENDS)
        raise BackendError(f"unknown backend {name!r}. Known backends: {known}")
    module = importlib.import_module(f"brand_ops.backends.{name}")
    build = getattr(module, "build", None)
    if build is None:
        raise BackendError(f"backend {name!r} module is missing a build(settings) factory")
    return build(settings)
