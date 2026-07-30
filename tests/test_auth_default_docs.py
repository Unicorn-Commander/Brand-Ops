"""The operator-facing text about ``AUTH_REQUIRED`` must match the code default.

``auth_required()``'s code default was flipped from false to TRUE (fail closed).
``docs/RUNBOOK.md`` and ``docs/HANDOFF.md`` were updated with it; ``docs/deploy.md``
was not, and it kept saying

    | `AUTH_REQUIRED` | Enforce bearer | `true` in prod; `false` (default) runs open |
    Auth is optional and off by default (`AUTH_REQUIRED=false`).

which is the one document an operator reads WHILE deploying. Documentation that
contradicts the code is not a cosmetic defect on a security default: it is the
instruction an operator follows, and this one told them the open posture was the
default they would get by doing nothing.

The remediation text on the boot-time ``ConfigError`` had the same shape of bug
and was worse -- it was actively circular. It said "Set them, or UNSET
AUTH_REQUIRED for local/CLI use", but after the flip, unsetting AUTH_REQUIRED is
precisely what puts the process into the production profile that raises the
error. An operator following the message loops forever.

These tests derive the truth from the CODE (``auth_required()`` with the variable
deleted) and then assert the prose agrees, so the docs cannot drift again without
something going red.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from brand_ops import config
from brand_ops.api.auth import auth_required

REPO = Path(__file__).resolve().parents[1]
OPERATOR_DOCS = ("docs/deploy.md", "docs/RUNBOOK.md", "docs/HANDOFF.md")

# Phrases that were TRUE before the flip and are FALSE now. Matched
# case-insensitively against the whole document.
STALE_CLAIMS = (
    "`false` (default) runs open",
    "auth is optional and off by default",
    "off by default (`auth_required=false`)",
    "auth_required defaults to false",
    "`auth_required` defaults to `false`",
)


def test_the_code_default_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fact the documents have to agree with. Read from the code, not asserted."""
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    assert auth_required() is True


@pytest.mark.parametrize("doc", OPERATOR_DOCS)
def test_no_operator_doc_still_says_auth_is_off_by_default(doc: str) -> None:
    """deploy.md is the one an operator has open while deploying; it lied."""
    text = (REPO / doc).read_text(encoding="utf-8").lower()
    found = [claim for claim in STALE_CLAIMS if claim in text]
    assert found == [], f"{doc} still claims: {found}"


def test_deploy_md_states_the_fail_closed_default_positively() -> None:
    """Absence of the wrong sentence is not the presence of the right one.

    Without this, deleting the row from the env table would "pass" -- and an
    operator reading a table with no default at all is no better served.
    """
    text = (REPO / "docs/deploy.md").read_text(encoding="utf-8").lower()
    assert "auth_required" in text
    assert "defaults to `true`" in text, "deploy.md must state the true default"
    # And it must still tell them how to opt out for local/CLI, explicitly.
    assert "auth_required=false" in text


def test_the_config_error_does_not_tell_the_operator_to_unset_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION-SHAPED PIN: the remediation must not be the cause of the error.

    "unset AUTH_REQUIRED" was the advice, and unsetting it is what SELECTS the
    production profile that raises. The message has to name the value that
    actually exits the loop.
    """
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BRAND_OPS_BOOTSTRAP_WORKSPACE_ID", raising=False)
    config.get_settings.cache_clear()
    try:
        with pytest.raises(config.ConfigError) as excinfo:
            config.validate_runtime_config()
    finally:
        config.get_settings.cache_clear()
    msg = str(excinfo.value)
    assert "AUTH_REQUIRED=false" in msg, msg
    assert "unset AUTH_REQUIRED" not in msg, msg
    # Following the advice must actually clear the error, which is the whole
    # point: this is the loop, closed.
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    config.get_settings.cache_clear()
    try:
        assert config.validate_runtime_config() is not None
    finally:
        config.get_settings.cache_clear()


def test_the_suite_does_not_claim_a_mechanism_it_does_not_have() -> None:
    """``tests/conftest.py`` described its own opt-out incorrectly.

    It used ``os.environ.setdefault("AUTH_REQUIRED", "false")`` and said the
    reason was that "an operator running the suite with AUTH_REQUIRED
    deliberately on still gets that". They never could:

      * the autouse ``_auth_off_by_default`` fixture monkeypatches the variable to
        "false" for every test regardless, so an inherited "true" would not
        survive the first fixture; and
      * with AUTH_REQUIRED=true and no DATABASE_URL, ``validate_runtime_config``
        runs at ``brand_ops.api.app`` IMPORT time and raises ConfigError, so
        honouring an inherited "true" would break COLLECTION rather than run the
        suite under it.

    The FIXTURE is the behaviour the suite needs (a deterministic verdict that
    does not depend on the launching shell), so the mechanism was aligned to it
    -- a plain assignment -- and the claim removed. This pins both halves.
    """
    src = (REPO / "tests/conftest.py").read_text(encoding="utf-8")
    assert 'os.environ["AUTH_REQUIRED"] = "false"' in src
    assert (
        "os.environ.setdefault(" not in src
    ), "the setdefault promise could never be kept"
    assert "deliberately on still gets that" not in src
