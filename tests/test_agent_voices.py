"""Agent spoken-voice tests: catalog, binding, inheritance, manifest, preview.

The mutating paths run against a tmp kits dir seeded with a real brand.yaml (the
established agent-identity test pattern) so the repo's brand-kits are never
touched. The catalog and preview endpoints are exercised through the in-process
TestClient with auth off. No network: orator is either unconfigured (the default,
so preview reports unavailable) or its HTTP client is stubbed.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from brand_ops import agent_identity, voices
from brand_ops.api.app import create_app
from brand_ops.api.schemas import AgentIdentityOut
from brand_ops.config import get_settings

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def kits(tmp_path: Path) -> Path:
    src = _REPO / "brand-kits" / "magic-unicorn" / "brand.yaml"
    d = tmp_path / "magic-unicorn"
    d.mkdir()
    shutil.copy(src, d / "brand.yaml")
    return tmp_path


def _set_brand_voice(kits: Path, voice_id: str) -> None:
    """Declare a signature voice on the seeded brand.yaml (for inheritance)."""
    p = kits / "magic-unicorn" / "brand.yaml"
    data = yaml.safe_load(p.read_text())
    data.setdefault("voice", {})["voice_id"] = voice_id
    p.write_text(yaml.safe_dump(data))


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_catalog_roster_is_well_formed() -> None:
    roster = voices.list_voices()
    assert roster, "catalog should not be empty"
    ids = [v["id"] for v in roster]
    assert len(ids) == len(set(ids)), "voice ids must be unique"
    for v in roster:
        assert v["id"] and v["label"]
        assert v["engine"] in (voices.ENGINE_POCKET, voices.ENGINE_KOKORO)
        assert v["kind"] in ("custom", "built-in")


def test_is_known_and_engine_for() -> None:
    assert voices.is_known("af_sky")
    assert voices.engine_for("af_sky") == voices.ENGINE_KOKORO
    assert voices.engine_for("alba") == voices.ENGINE_POCKET
    assert not voices.is_known("nope")
    assert voices.engine_for("nope") is None
    assert not voices.is_known(None)


# ---------------------------------------------------------------------------
# Binding + inheritance
# ---------------------------------------------------------------------------


def test_new_agent_is_unvoiced_when_brand_has_no_signature(kits: Path) -> None:
    ident = agent_identity.create_agent("magic-unicorn", "Iris", "director", kits_dir=kits)
    assert ident["voice"] is None


def test_new_agent_inherits_brand_signature_voice(kits: Path) -> None:
    _set_brand_voice(kits, "af_sky")
    ident = agent_identity.create_agent("magic-unicorn", "Iris", "director", kits_dir=kits)
    assert ident["voice"] is not None
    assert ident["voice"]["voice_id"] == "af_sky"
    # Engine is resolved from the catalog even though the brand only named the id.
    assert ident["voice"]["engine"] == voices.ENGINE_KOKORO


def test_set_agent_voice_binds_and_persists(kits: Path) -> None:
    agent_identity.create_agent("magic-unicorn", "Iris", "director", kits_dir=kits)
    ident = agent_identity.set_agent_voice("magic-unicorn", "iris", "alba", kits_dir=kits)

    assert ident["voice"] == {"voice_id": "alba", "engine": voices.ENGINE_POCKET, "speed": 1.0}
    # Persisted to agent.json.
    meta = kits / "magic-unicorn" / "agents" / "iris" / "agent.json"
    assert json.loads(meta.read_text())["voice"]["voice_id"] == "alba"
    # Round-trips through the wire schema.
    assert AgentIdentityOut(**ident).voice.voice_id == "alba"


def test_set_agent_voice_clamps_speed(kits: Path) -> None:
    agent_identity.create_agent("magic-unicorn", "Iris", "director", kits_dir=kits)
    fast = agent_identity.set_agent_voice(
        "magic-unicorn", "iris", "af_sky", speed=9.0, kits_dir=kits
    )
    assert fast["voice"]["speed"] == 2.0
    slow = agent_identity.set_agent_voice(
        "magic-unicorn", "iris", "af_sky", speed=0.1, kits_dir=kits
    )
    assert slow["voice"]["speed"] == 0.5


def test_set_agent_voice_rejects_unknown(kits: Path) -> None:
    agent_identity.create_agent("magic-unicorn", "Iris", "director", kits_dir=kits)
    with pytest.raises(agent_identity.AgentIdentityError):
        agent_identity.set_agent_voice("magic-unicorn", "iris", "bogus", kits_dir=kits)


def test_set_agent_voice_clear(kits: Path) -> None:
    agent_identity.create_agent("magic-unicorn", "Iris", "director", kits_dir=kits)
    agent_identity.set_agent_voice("magic-unicorn", "iris", "af_sky", kits_dir=kits)
    cleared = agent_identity.set_agent_voice("magic-unicorn", "iris", "", kits_dir=kits)
    assert cleared["voice"] is None


def test_set_agent_voice_missing_agent(kits: Path) -> None:
    with pytest.raises(agent_identity.AgentIdentityError):
        agent_identity.set_agent_voice("magic-unicorn", "ghost", "af_sky", kits_dir=kits)


# ---------------------------------------------------------------------------
# Persona edit
# ---------------------------------------------------------------------------


def test_update_agent_role_and_bio(kits: Path) -> None:
    agent_identity.create_agent("magic-unicorn", "Iris", "director", "old bio", kits_dir=kits)
    out = agent_identity.update_agent(
        "magic-unicorn", "iris", role="brand steward", bio="new bio", kits_dir=kits
    )
    assert out["role"] == "brand steward"
    assert out["bio"] == "new bio"
    # An empty bio clears it; an empty role is rejected.
    assert agent_identity.update_agent("magic-unicorn", "iris", bio="", kits_dir=kits)["bio"] == ""
    with pytest.raises(agent_identity.AgentIdentityError):
        agent_identity.update_agent("magic-unicorn", "iris", role="  ", kits_dir=kits)


def test_update_agent_leaves_voice_untouched(kits: Path) -> None:
    agent_identity.create_agent("magic-unicorn", "Iris", "director", kits_dir=kits)
    agent_identity.set_agent_voice("magic-unicorn", "iris", "af_sky", kits_dir=kits)
    out = agent_identity.update_agent("magic-unicorn", "iris", bio="edited", kits_dir=kits)
    assert out["voice"]["voice_id"] == "af_sky"


# ---------------------------------------------------------------------------
# Manifest export (what Brigade / Stable / orator consume)
# ---------------------------------------------------------------------------


def test_manifest_voice_has_tone_and_tts(kits: Path) -> None:
    agent_identity.create_agent("magic-unicorn", "Iris", "director", kits_dir=kits)
    before = agent_identity.build_manifest("magic-unicorn", "iris", kits_dir=kits)
    assert "tone" in before["voice"]
    assert before["voice"]["tts"] is None  # unvoiced until assigned

    agent_identity.set_agent_voice("magic-unicorn", "iris", "alba", kits_dir=kits)
    after = agent_identity.build_manifest("magic-unicorn", "iris", kits_dir=kits)
    assert after["voice"]["tts"]["voice_id"] == "alba"
    assert after["voice"]["tts"]["engine"] == voices.ENGINE_POCKET


# ---------------------------------------------------------------------------
# Preview proxy (best-effort; no real network)
# ---------------------------------------------------------------------------


def test_preview_unconfigured_raises() -> None:
    with pytest.raises(voices.VoicePreviewError):
        voices.synthesize_preview("af_sky", settings=SimpleNamespace(orator_base_url=""))


def test_preview_unknown_voice_raises() -> None:
    with pytest.raises(voices.VoicePreviewError):
        voices.synthesize_preview(
            "bogus", settings=SimpleNamespace(orator_base_url="http://orator.test")
        )


def test_preview_success_returns_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        content = b"RIFFstubaudio"
        headers = {"content-type": "audio/wav"}

        def raise_for_status(self) -> None:
            pass

    class _Client:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *a: object) -> bool:
            return False

        def post(self, url: str, json: dict | None = None) -> _Resp:
            # Targets only the configured orator, never a caller-supplied host.
            assert url == "http://orator.test/v1/audio/speech"
            assert json and json["voice"] == "af_sky"
            return _Resp()

    monkeypatch.setattr("httpx.Client", _Client)
    audio, content_type = voices.synthesize_preview(
        "af_sky", text="hello", settings=SimpleNamespace(orator_base_url="http://orator.test/")
    )
    assert audio == b"RIFFstubaudio"
    assert content_type == "audio/wav"


# ---------------------------------------------------------------------------
# API surface (catalog + preview), auth off
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ORATOR_BASE_URL", raising=False)
    get_settings.cache_clear()
    app = create_app()
    yield TestClient(app)
    get_settings.cache_clear()


def test_api_get_voices(client: TestClient) -> None:
    resp = client.get("/api/voices")
    assert resp.status_code == 200
    body = resp.json()
    assert body["voices"], "catalog should not be empty"
    assert body["preview_available"] is False  # no orator configured in tests
    first = body["voices"][0]
    assert {"id", "label", "engine", "kind"} <= set(first)


def test_api_preview_unconfigured_is_503(client: TestClient) -> None:
    resp = client.post("/api/voices/preview", json={"voice_id": "af_sky"})
    assert resp.status_code == 503


def test_api_preview_unknown_voice_is_422(client: TestClient) -> None:
    resp = client.post("/api/voices/preview", json={"voice_id": "bogus"})
    assert resp.status_code == 422
