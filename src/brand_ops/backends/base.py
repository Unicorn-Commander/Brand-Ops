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


class QuotaExceeded(BackendError):
    """A per-workspace spend cap or an agent's budget would be exceeded.

    Raised by the engine BEFORE any backend call when this workspace's image
    spend over the rolling window already meets or exceeds its cap, or when the
    attributed agent's ``weekly_budget_cents`` is already met. It subclasses
    ``BackendError`` so the existing REST and MCP handlers map it to a clean 4xx
    with no edit to those files; the application factory additionally maps it to
    HTTP 429 (Too Many Requests) at integration so a quota denial reads as a
    rate/quota limit rather than a generic upstream error.
    """


class EntitlementDenied(BackendError):
    """A valid request whose workspace tier does not include hosted generation.

    Raised by the engine BEFORE any backend call when the entitlement gate is
    enforced (``BRAND_OPS_ENTITLEMENT_ENFORCED`` on) and this workspace's tier
    does not permit model-backed generation (a Free tier), or the entitlement
    cannot be proven (fail-closed). Distinct from ``QuotaExceeded``: that guards
    a dollar budget within a paid tier, this is the on/off that separates Free
    from paid. It subclasses ``BackendError`` so the existing MCP and CLI paths
    surface it through their ``BackendError`` handling with no per-surface edit;
    the REST ``/generate`` handler additionally maps it to HTTP 402 (Payment
    Required) so a tier denial reads as needing a paid plan, not an auth failure
    or a transient rate limit. It is dormant by default: the gate is a no-op
    until an operator turns enforcement on.
    """


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
