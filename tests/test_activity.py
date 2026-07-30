"""Activity / provenance feed + lifecycle transition tests.

Two surfaces under test, both network- and LLM-free:

* the ``brand_ops.activity`` module — an append-only JSONL feed: append+read
  round-trip newest-first, the brand filter, the limit, and tolerance of a
  corrupt line. These run entirely inside ``tmp_path`` (their own kits dir) and
  touch nothing in the repo.
* the ``compliance.transition`` helper and the REST surface (POST
  /api/assets/transition, POST /api/assets/approve, GET /api/activity). The
  transition unit test runs inside a synthesized ``tmp_path`` kit; the REST tests
  exercise the in-process TestClient against the real ``brand-kits/magic-unicorn``
  tree and so DELETE every sidecar they create, and redirect the feed to a
  ``tmp_path`` file (monkeypatching ``activity._log_path``) so they never write
  into the repo's runtime feed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from brand_ops import activity
from brand_ops.activity import (
    GENESIS_PREV_HASH,
    append_event,
    read_events,
    reset_current_actor,
    set_current_actor,
    verify_chain,
)
from brand_ops.api.app import create_app
from brand_ops.brand_loader import DEFAULT_KITS_DIR
from brand_ops.compliance import transition

# ─────────────────────────── the activity module ────────────────────────────


def test_append_read_round_trip_newest_first(tmp_path: Path) -> None:
    """Appended events read back newest-first."""
    append_event(
        "you", "human", "uploaded", brand="acme", ts="2026-01-01T00:00:00+00:00", kits_dir=tmp_path
    )
    append_event(
        "iris",
        "agent",
        "routed a pack",
        brand="acme",
        ts="2026-01-02T00:00:00+00:00",
        kits_dir=tmp_path,
    )
    append_event(
        "compliance",
        "system",
        "flagged off-brand",
        brand="acme",
        ts="2026-01-03T00:00:00+00:00",
        kits_dir=tmp_path,
    )

    events = read_events(kits_dir=tmp_path)
    assert [e["action"] for e in events] == ["flagged off-brand", "routed a pack", "uploaded"]
    # Each event carries a unique id and the fields it was given.
    assert len({e["id"] for e in events}) == 3
    assert events[-1]["actor"] == "you"
    assert events[-1]["actor_kind"] == "human"


def test_append_returns_event_with_id_and_ts(tmp_path: Path) -> None:
    """append_event returns the event dict it wrote, with a stamped id."""
    event = append_event(
        "you",
        "human",
        "approved",
        brand="acme",
        target="logo/x.png",
        ts="2026-01-01T00:00:00+00:00",
        kits_dir=tmp_path,
    )
    assert event["id"]
    assert event["ts"] == "2026-01-01T00:00:00+00:00"
    assert event["action"] == "approved"
    assert event["target"] == "logo/x.png"


def test_read_brand_filter(tmp_path: Path) -> None:
    """The brand filter returns only that brand's events."""
    append_event(
        "you", "human", "uploaded", brand="acme", ts="2026-01-01T00:00:00+00:00", kits_dir=tmp_path
    )
    append_event(
        "you",
        "human",
        "uploaded",
        brand="globex",
        ts="2026-01-02T00:00:00+00:00",
        kits_dir=tmp_path,
    )
    append_event(
        "compliance",
        "system",
        "flagged off-brand",
        brand="acme",
        ts="2026-01-03T00:00:00+00:00",
        kits_dir=tmp_path,
    )

    acme = read_events(brand="acme", kits_dir=tmp_path)
    assert {e["brand"] for e in acme} == {"acme"}
    assert len(acme) == 2
    globex = read_events(brand="globex", kits_dir=tmp_path)
    assert len(globex) == 1


def test_read_limit(tmp_path: Path) -> None:
    """The limit caps the read to the most-recent N events."""
    for i in range(5):
        append_event(
            "you", "human", f"act-{i}", ts=f"2026-01-0{i + 1}T00:00:00+00:00", kits_dir=tmp_path
        )

    events = read_events(limit=2, kits_dir=tmp_path)
    assert [e["action"] for e in events] == ["act-4", "act-3"]
    # A non-positive limit returns nothing rather than an unbounded read.
    assert read_events(limit=0, kits_dir=tmp_path) == []


def test_read_missing_feed_is_empty(tmp_path: Path) -> None:
    """A feed that was never written reads as an empty list, not an error."""
    assert read_events(kits_dir=tmp_path) == []


def test_read_tolerates_corrupt_line(tmp_path: Path) -> None:
    """A corrupt/truncated line is skipped; the good events still read back."""
    append_event("you", "human", "uploaded", ts="2026-01-01T00:00:00+00:00", kits_dir=tmp_path)
    # Append a junk line directly, simulating a partial write.
    log = tmp_path / ".activity.jsonl"
    with log.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
        fh.write("\n")  # a blank line, also tolerated
    append_event("iris", "agent", "chatted", ts="2026-01-02T00:00:00+00:00", kits_dir=tmp_path)

    events = read_events(kits_dir=tmp_path)
    assert [e["action"] for e in events] == ["chatted", "uploaded"]


def test_append_never_raises_on_unwritable_path(tmp_path: Path) -> None:
    """A feed whose parent cannot be created still returns the event, no raise."""
    # Point the kits dir at a path under a *file*, so mkdir of the parent fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory")
    event = append_event(
        "you", "human", "uploaded", kits_dir=blocker / "nested", ts="2026-01-01T00:00:00+00:00"
    )
    assert event["action"] == "uploaded"  # returned despite the write failing


# ─────────────────── T2.5: real actor stamp (uc_uid contextvar) ──────────────


def test_uc_uid_defaults_to_request_actor(tmp_path: Path) -> None:
    """When the actor contextvar is set, every event records the caller's uc_uid."""
    token = set_current_actor("uc_aaron")
    try:
        event = append_event(
            "iris", "agent", "generated", brand="acme",
            ts="2026-01-01T00:00:00+00:00", kits_dir=tmp_path,
        )
        # The persona is still the rendered label; uc_uid is the real caller.
        assert event["actor"] == "iris"
        assert event["actor_kind"] == "agent"
        assert event["uc_uid"] == "uc_aaron"
        # And it round-trips on disk.
        on_disk = read_events(kits_dir=tmp_path)[0]
        assert on_disk["uc_uid"] == "uc_aaron"
    finally:
        reset_current_actor(token)


def test_uc_uid_is_null_when_unscoped(tmp_path: Path) -> None:
    """With no actor set (CLI / stdio MCP / AUTH off), uc_uid is null = 'system'."""
    reset_current_actor()  # hard-reset to the unscoped default
    event = append_event(
        "you", "human", "uploaded", brand="acme",
        ts="2026-01-01T00:00:00+00:00", kits_dir=tmp_path,
    )
    assert event["uc_uid"] is None


def test_explicit_uc_uid_overrides_contextvar(tmp_path: Path) -> None:
    """An explicit uc_uid wins over the request-scoped actor."""
    token = set_current_actor("uc_request")
    try:
        event = append_event(
            "iris", "agent", "generated", uc_uid="uc_explicit",
            ts="2026-01-01T00:00:00+00:00", kits_dir=tmp_path,
        )
        assert event["uc_uid"] == "uc_explicit"
    finally:
        reset_current_actor(token)


# ───────────────────── T2.5: tamper-evident hash chain ───────────────────────


def test_chain_links_each_entry_to_the_prior(tmp_path: Path) -> None:
    """Each entry's prev_hash is the previous entry's entry_hash; first = genesis."""
    e1 = append_event("you", "human", "a1", ts="2026-01-01T00:00:00+00:00", kits_dir=tmp_path)
    e2 = append_event("you", "human", "a2", ts="2026-01-02T00:00:00+00:00", kits_dir=tmp_path)
    e3 = append_event("you", "human", "a3", ts="2026-01-03T00:00:00+00:00", kits_dir=tmp_path)

    assert e1["prev_hash"] == GENESIS_PREV_HASH
    assert e2["prev_hash"] == e1["entry_hash"]
    assert e3["prev_hash"] == e2["entry_hash"]
    # Each entry_hash is a 64-char sha256 hex digest and they are distinct.
    for e in (e1, e2, e3):
        assert len(e["entry_hash"]) == 64
    assert len({e1["entry_hash"], e2["entry_hash"], e3["entry_hash"]}) == 3


def test_verify_chain_passes_on_an_untouched_log(tmp_path: Path) -> None:
    """A freshly written chain verifies intact, counting every chained line."""
    for i in range(4):
        append_event("you", "human", f"act-{i}", ts=f"2026-01-0{i + 1}T00:00:00+00:00",
                     kits_dir=tmp_path)
    report = verify_chain(kits_dir=tmp_path)
    assert report["ok"] is True
    assert report["chained"] == 4
    assert report["legacy"] == 0
    assert report["chain_start"] == 0
    assert report["break_at"] is None


def test_verify_chain_catches_an_edited_line(tmp_path: Path) -> None:
    """Editing a field of a past line is detected (entry_hash no longer recomputes)."""
    append_event("you", "human", "a1", ts="2026-01-01T00:00:00+00:00", kits_dir=tmp_path)
    append_event("you", "human", "a2", ts="2026-01-02T00:00:00+00:00", kits_dir=tmp_path)
    append_event("you", "human", "a3", ts="2026-01-03T00:00:00+00:00", kits_dir=tmp_path)

    log = tmp_path / ".activity.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["action"] = "TAMPERED"  # change the body but leave the stale entry_hash
    lines[1] = json.dumps(tampered, ensure_ascii=False)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = verify_chain(kits_dir=tmp_path)
    assert report["ok"] is False
    assert report["break_at"] == 1
    assert report["reason"] == "entry_hash"


def test_verify_chain_catches_a_deleted_line(tmp_path: Path) -> None:
    """Removing a line breaks the prev_hash link of the line that followed it."""
    append_event("you", "human", "a1", ts="2026-01-01T00:00:00+00:00", kits_dir=tmp_path)
    append_event("you", "human", "a2", ts="2026-01-02T00:00:00+00:00", kits_dir=tmp_path)
    append_event("you", "human", "a3", ts="2026-01-03T00:00:00+00:00", kits_dir=tmp_path)

    log = tmp_path / ".activity.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines()
    del lines[1]  # drop the middle entry; a3.prev_hash now points at a missing a2
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = verify_chain(kits_dir=tmp_path)
    assert report["ok"] is False
    assert report["break_at"] == 1  # the now-second line (old a3) fails its prev_hash
    assert report["reason"] == "prev_hash"


def test_verify_chain_tolerates_legacy_unchained_lines(tmp_path: Path) -> None:
    """A pre-feature line (no chain fields) is counted, not a break; chain starts after."""
    log = tmp_path / ".activity.jsonl"
    # A legacy line in the old shape: no prev_hash / entry_hash.
    legacy = {
        "id": "legacy0001", "ts": "2026-01-01T00:00:00+00:00", "actor": "you",
        "actor_kind": "human", "action": "uploaded", "brand": "acme",
        "target": None, "note": None,
    }
    log.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
    # Now append two chained entries; chaining restarts from genesis after legacy.
    append_event("iris", "agent", "generated", brand="acme",
                 ts="2026-01-02T00:00:00+00:00", kits_dir=tmp_path)
    append_event("iris", "agent", "generated", brand="acme",
                 ts="2026-01-03T00:00:00+00:00", kits_dir=tmp_path)

    # The legacy line still reads back unchanged (read is backward-tolerant).
    actions = [e["action"] for e in read_events(kits_dir=tmp_path)]
    assert actions == ["generated", "generated", "uploaded"]

    report = verify_chain(kits_dir=tmp_path)
    assert report["ok"] is True
    assert report["legacy"] == 1
    assert report["chained"] == 2
    assert report["chain_start"] == 1  # the chain begins at the first chained line
    assert report["total"] == 3


def test_verify_chain_empty_log_is_intact(tmp_path: Path) -> None:
    """A log that was never written is a vacuously intact chain."""
    report = verify_chain(kits_dir=tmp_path)
    assert report["ok"] is True
    assert report["total"] == 0
    assert report["chain_start"] is None


def test_append_warns_but_returns_on_write_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A write failure logs a structured warning under 'brand_ops' yet never raises."""
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory")
    with caplog.at_level("WARNING", logger="brand_ops"):
        event = append_event(
            "you", "human", "uploaded", kits_dir=blocker / "nested",
            ts="2026-01-01T00:00:00+00:00",
        )
    assert event["action"] == "uploaded"  # still returned
    assert any("activity append failed" in r.message for r in caplog.records)


# ──────────────── T2.5: generation lands on the provenance feed ───────────────


def test_save_result_emits_a_generation_event(tmp_path: Path) -> None:
    """output.save_result drops a 'generated' event with the actor, asset, brand."""
    from brand_ops.backends.base import GenResult
    from brand_ops.output import save_result
    from brand_ops.prompt_builder import BuiltPrompt

    built = BuiltPrompt(
        text="a mark",
        asset_type="logo",
        variant=None,
        backend="recraft",
        size=(512, 512),
        transparent=False,
        reference_images=[],
        warnings=[],
    )
    result = GenResult(
        images=[b"\x89PNG\r\n\x1a\n"],
        mime="image/png",
        backend="recraft",
        model="recraft-v3",
        cost_cents=4.0,
    )

    token = set_current_actor("uc_creator")
    try:
        paths = save_result(
            "acme", "logo", None, built, result, kits_dir=tmp_path,
            now=datetime(2026, 1, 1, 0, 0, 0),
        )
    finally:
        reset_current_actor(token)
    assert len(paths) == 1

    events = read_events(brand="acme", kits_dir=tmp_path)
    gen = [e for e in events if e["action"] == "generated"]
    assert len(gen) == 1
    assert gen[0]["actor"] == "iris"
    assert gen[0]["actor_kind"] == "agent"
    assert gen[0]["uc_uid"] == "uc_creator"  # the request-scoped caller
    assert gen[0]["brand"] == "acme"
    assert gen[0]["target"] == f"logo/{paths[0].name}"
    # The event is chained like any other line.
    assert verify_chain(brand="acme", kits_dir=tmp_path)["ok"] is True


# ─────────────────────────── compliance.transition ──────────────────────────


def _place_sidecar(kits_dir: Path, *, brand: str, asset_type: str, file: str, status: str) -> Path:
    """Drop a minimal asset sidecar (and placeholder image) into a synth kit."""
    asset_dir = kits_dir / brand / "generated" / asset_type
    asset_dir.mkdir(parents=True, exist_ok=True)
    image_path = asset_dir / file
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    record = {
        "brand": brand,
        "asset_type": asset_type,
        "lifecycle_status": status,
        "created_at": "2026-01-01-00-00-00",
        "reference_images": [],
    }
    image_path.with_suffix(image_path.suffix + ".json").write_text(json.dumps(record, indent=2))
    return image_path


def test_transition_writes_state_and_appends_event(tmp_path: Path) -> None:
    """transition writes the new lifecycle_status AND appends one feed event."""
    image_path = _place_sidecar(
        tmp_path, brand="acme", asset_type="logo", file="mark.png", status="needs_review"
    )

    data = transition(
        "acme",
        "logo",
        "mark.png",
        "generated",
        "changes_requested",
        note="tighten the kerning",
        actor="you",
        ts="2026-01-05T00:00:00+00:00",
        kits_dir=tmp_path,
    )

    # The sidecar carries the new state, other keys preserved.
    assert data["lifecycle_status"] == "changes_requested"
    on_disk = json.loads(image_path.with_suffix(image_path.suffix + ".json").read_text())
    assert on_disk["lifecycle_status"] == "changes_requested"
    assert on_disk["brand"] == "acme"

    # Exactly one event was appended, describing the move.
    events = read_events(brand="acme", kits_dir=tmp_path)
    assert len(events) == 1
    assert events[0]["action"] == "requested changes"
    assert events[0]["actor"] == "you"
    assert events[0]["actor_kind"] == "human"
    assert events[0]["target"] == "logo/mark.png"
    assert events[0]["note"] == "tighten the kerning"


def test_transition_bad_state_raises_value_error(tmp_path: Path) -> None:
    """An unknown target state raises ValueError and writes nothing."""
    _place_sidecar(tmp_path, brand="acme", asset_type="logo", file="mark.png", status="approved")
    with pytest.raises(ValueError):
        transition("acme", "logo", "mark.png", "generated", "bogus", kits_dir=tmp_path)


def test_transition_missing_sidecar_raises_not_found(tmp_path: Path) -> None:
    """A transition against a nonexistent asset raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        transition("acme", "logo", "ghost.png", "generated", "approved", kits_dir=tmp_path)


# ─────────────────────────── REST surface ───────────────────────────────────


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    # Redirect the activity feed to a tmp file so the route-level tests never
    # write into the repo's real brand-kits/.activity.jsonl.
    feed = tmp_path / ".activity.jsonl"
    monkeypatch.setattr(activity, "_log_path", lambda kits_dir=None: feed)
    return TestClient(create_app())


@pytest.fixture
def cleanup_assets() -> Iterator[list[Path]]:
    """Track sidecars written into the real kit and remove each after the test."""
    created: list[Path] = []
    try:
        yield created
    finally:
        for image in created:
            sidecar = image.with_suffix(image.suffix + ".json")
            for path in (sidecar, image):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


def _seed_real_sidecar(
    created: list[Path], *, file_name: str, asset_type: str, status: str
) -> Path:
    asset_dir = DEFAULT_KITS_DIR / "magic-unicorn" / "generated" / asset_type
    asset_dir.mkdir(parents=True, exist_ok=True)
    image_path = asset_dir / file_name
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    created.append(image_path)
    record = {
        "brand": "magic-unicorn",
        "asset_type": asset_type,
        "lifecycle_status": status,
        "created_at": "2026-01-01-00-00-00",
        "reference_images": [],
    }
    image_path.with_suffix(image_path.suffix + ".json").write_text(json.dumps(record, indent=2))
    return image_path


def test_transition_route_writes_state_and_feeds_activity(
    client: TestClient, cleanup_assets: list[Path]
) -> None:
    """POST /api/assets/transition flips the state and GET /api/activity shows it."""
    file_name = "test-activity-transition.png"
    _seed_real_sidecar(
        cleanup_assets, file_name=file_name, asset_type="og_image", status="needs_review"
    )

    resp = client.post(
        "/api/assets/transition",
        json={
            "brand": "magic-unicorn",
            "file": file_name,
            "asset": "og_image",
            "kind": "generated",
            "to": "changes_requested",
            "note": "send it back",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["asset"]["lifecycle_status"] == "changes_requested"

    # The transition appears on the activity feed, newest first.
    feed = client.get("/api/activity", params={"brand": "magic-unicorn"})
    assert feed.status_code == 200
    events = feed.json()["events"]
    assert events, "the transition should have produced an event"
    top = events[0]
    assert top["action"] == "requested changes"
    assert top["target"] == f"og_image/{file_name}"
    assert top["note"] == "send it back"
    assert top["ts"]  # the route stamped an ISO timestamp at the boundary


def test_transition_route_bad_state_is_422(client: TestClient, cleanup_assets: list[Path]) -> None:
    """An invalid target state on POST /api/assets/transition is a 422."""
    file_name = "test-activity-badstate.png"
    _seed_real_sidecar(
        cleanup_assets, file_name=file_name, asset_type="og_image", status="approved"
    )
    resp = client.post(
        "/api/assets/transition",
        json={
            "brand": "magic-unicorn",
            "file": file_name,
            "asset": "og_image",
            "kind": "generated",
            "to": "totally-bogus",
        },
    )
    assert resp.status_code == 422, resp.text


def test_transition_route_missing_sidecar_is_404(client: TestClient) -> None:
    """A transition against a nonexistent asset is a 404."""
    resp = client.post(
        "/api/assets/transition",
        json={
            "brand": "magic-unicorn",
            "file": "does-not-exist.png",
            "asset": "og_image",
            "kind": "generated",
            "to": "approved",
        },
    )
    assert resp.status_code == 404


def test_approve_route_still_works_and_feeds_activity(
    client: TestClient, cleanup_assets: list[Path]
) -> None:
    """POST /api/assets/approve (the alias) flips to approved and feeds activity."""
    file_name = "test-activity-approve.png"
    _seed_real_sidecar(
        cleanup_assets, file_name=file_name, asset_type="og_image", status="needs_review"
    )

    resp = client.post(
        "/api/assets/approve",
        json={
            "brand": "magic-unicorn",
            "file": file_name,
            "asset": "og_image",
            "kind": "generated",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["asset"]["lifecycle_status"] == "approved"

    feed = client.get("/api/activity", params={"brand": "magic-unicorn"})
    assert feed.status_code == 200
    actions = [e["action"] for e in feed.json()["events"]]
    assert "approved" in actions


def test_activity_limit_is_validated(client: TestClient) -> None:
    """GET /api/activity rejects a non-positive limit with a 422."""
    resp = client.get("/api/activity", params={"limit": 0})
    assert resp.status_code == 422
