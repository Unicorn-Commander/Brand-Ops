"""Suite-wide defaults.

``AUTH_REQUIRED`` used to default to false in code, so the test suite got the
anonymous, unscoped principal simply by not setting it. That default is now
fail-closed (true): a node that configures nothing refuses callers instead of
serving every tenant's brands to anybody who can reach the port.

The tests still want the open, anonymous path -- they are the CLI-shaped, DB-less
surface the anonymous principal exists for -- so they opt out EXPLICITLY here,
once. A test that is about the authenticated surface overrides it with its own
``monkeypatch.setenv("AUTH_REQUIRED", "true")``, which runs after this fixture
and therefore wins; a test that is about the DEFAULT deletes the variable and
asserts what the code does with nothing set.
"""
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# At IMPORT time as well as per-test: several test modules build the FastAPI app
# (which runs validate_runtime_config) at module scope, i.e. during collection,
# before any fixture has run. setdefault, not a plain assignment, so an operator
# running the suite with AUTH_REQUIRED deliberately on still gets that.
os.environ.setdefault("AUTH_REQUIRED", "false")


@pytest.fixture(autouse=True)
def _auth_off_by_default(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    yield
