"""Suite-wide defaults.

``AUTH_REQUIRED`` used to default to false in code, so the test suite got the
anonymous, unscoped principal simply by not setting it. That default is now
fail-closed (true): a node that configures nothing refuses callers instead of
serving every tenant's brands to anybody who can reach the port.

The tests still want the open, anonymous path -- they are the CLI-shaped, DB-less
surface the anonymous principal exists for -- so they opt out EXPLICITLY here,
once, and unconditionally. A test that is about the authenticated surface
overrides it with its own ``monkeypatch.setenv("AUTH_REQUIRED", "true")``, which
runs after the fixture and therefore wins; a test that is about the DEFAULT
deletes the variable and asserts what the code does with nothing set.
"""
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# At IMPORT time as well as per-test: several test modules build the FastAPI app
# (which runs validate_runtime_config) at module scope, i.e. during collection,
# before any fixture has run.
#
# This is a plain ASSIGNMENT, deliberately, and it used to be a ``setdefault``
# whose comment claimed that an operator running the suite with AUTH_REQUIRED
# deliberately on would keep that. It never could:
#
#   * the autouse ``_auth_off_by_default`` fixture below monkeypatches the
#     variable to "false" for EVERY test regardless, so the inherited value would
#     have survived only until the first fixture ran; and
#   * with AUTH_REQUIRED=true and no DATABASE_URL, ``validate_runtime_config``
#     (which runs at brand_ops.api.app IMPORT time) raises ConfigError, so
#     honouring the inherited value would break COLLECTION, not run the suite
#     under it.
#
# So the promise was doubly false. The mechanism is now what the suite actually
# needs and what the fixture already enforced: the tests pin AUTH_REQUIRED=false,
# at import and per test, because the anonymous/DB-less path is the surface they
# exercise. A test about the AUTHENTICATED surface sets "true" itself with
# monkeypatch (which runs after the fixture and wins); a test about the DEFAULT
# deletes the variable and asserts what the code does with nothing set. Neither
# depends on the ambient environment, which is the point: the suite's verdict
# must not change with the shell it was launched from.
os.environ["AUTH_REQUIRED"] = "false"


@pytest.fixture(autouse=True)
def _auth_off_by_default(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    yield
