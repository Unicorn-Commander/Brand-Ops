"""GET /api/assets pagination + cap, and conditional-GET (ETag/304) on the file route.

These run the in-process TestClient against a synthesized tmp kit (its own brand
dir plus generated assets), so they touch nothing in the repo and need no DB: with
AUTH off the request is unscoped, the catalog source-of-truth flag is off, so
``read_assets`` walks the on-disk sidecars under the monkeypatched kits root.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from brand_ops import brand_loader
from brand_ops.api.app import create_app

_BRAND = "pagination-test-brand"


def _place_assets(kits_dir: Path, count: int) -> None:
    """Drop ``count`` generated logo assets (bytes + sidecar) into a tmp kit.

    Each sidecar carries a distinct, lexically sortable ``created_at`` so the
    newest-first order the catalog applies is deterministic (asset-004 first).
    """
    asset_dir = kits_dir / _BRAND / "generated" / "logo"
    asset_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        name = f"asset-{i:03d}.png"
        (asset_dir / name).write_bytes(b"\x89PNG\r\n" + bytes([i]))
        (asset_dir / f"{name}.json").write_text(
            json.dumps(
                {
                    "brand": _BRAND,
                    "asset_type": "logo",
                    "mime": "image/png",
                    "created_at": f"2026-06-21-00-00-{i:02d}",
                }
            )
        )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(brand_loader, "DEFAULT_KITS_DIR", tmp_path)
    _place_assets(tmp_path, 5)
    with TestClient(create_app()) as test_client:
        yield test_client


def test_list_defaults_to_one_bounded_page(client: TestClient) -> None:
    body = client.get("/api/assets", params={"brand": _BRAND}).json()
    assert len(body["assets"]) == 5
    assert body["total"] == 5
    assert body["limit"] == 1000  # default is the hard ceiling, so the list is bounded
    assert body["offset"] == 0
    assert body["has_more"] is False


def test_list_pages_with_limit_and_offset(client: TestClient) -> None:
    first = client.get(
        "/api/assets", params={"brand": _BRAND, "limit": 2, "offset": 0}
    ).json()
    # newest first: highest created_at label leads
    assert [a["file"] for a in first["assets"]] == ["asset-004.png", "asset-003.png"]
    assert first["total"] == 5
    assert first["limit"] == 2
    assert first["offset"] == 0
    assert first["has_more"] is True

    middle = client.get(
        "/api/assets", params={"brand": _BRAND, "limit": 2, "offset": 2}
    ).json()
    last = client.get(
        "/api/assets", params={"brand": _BRAND, "limit": 2, "offset": 4}
    ).json()
    assert len(last["assets"]) == 1
    assert last["assets"][0]["file"] == "asset-000.png"
    assert last["has_more"] is False

    # the three pages tile the full set exactly: no overlap, no gap
    seen = [a["file"] for a in first["assets"] + middle["assets"] + last["assets"]]
    assert len(seen) == len(set(seen)) == 5


def test_list_rejects_out_of_range_paging(client: TestClient) -> None:
    assert client.get("/api/assets", params={"brand": _BRAND, "limit": 0}).status_code == 422
    assert (
        client.get("/api/assets", params={"brand": _BRAND, "limit": 1001}).status_code == 422
    )
    assert client.get("/api/assets", params={"brand": _BRAND, "offset": -1}).status_code == 422


def test_asset_file_sets_etag_and_304s_on_match(client: TestClient) -> None:
    params = {"brand": _BRAND, "asset": "logo", "file": "asset-000.png", "kind": "generated"}
    served = client.get("/api/assets/file", params=params)
    assert served.status_code == 200
    etag = served.headers["etag"]
    assert etag
    assert "max-age" in served.headers.get("cache-control", "")

    # a conditional re-request with the same validator gets a bodiless 304
    not_modified = client.get(
        "/api/assets/file", params=params, headers={"If-None-Match": etag}
    )
    assert not_modified.status_code == 304
    assert not_modified.content == b""
    assert not_modified.headers["etag"] == etag

    # a non-matching validator still serves the bytes
    assert (
        client.get(
            "/api/assets/file", params=params, headers={"If-None-Match": '"stale"'}
        ).status_code
        == 200
    )

    # If-None-Match: * matches any existing representation
    assert (
        client.get(
            "/api/assets/file", params=params, headers={"If-None-Match": "*"}
        ).status_code
        == 304
    )
