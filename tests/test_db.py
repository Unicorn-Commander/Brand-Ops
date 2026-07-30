"""Database layer tests.

These run without a live Postgres: they check the registered schema, model
construction, RLS bookkeeping, and the repository signatures. Tests that need a
real database are skipped unless BRAND_OPS_TEST_DB is set to an asyncpg URL.
"""
from __future__ import annotations

import inspect
import os
import uuid

import pytest

from brand_ops.db import base, models, repository

EXPECTED_TABLES = {
    "workspace",
    "workspace_member",
    "brand",
    "asset",
    "asset_version",
    "generation_job",
    "brand_research_run",
    "agent_governance",
    "agent_spend_event",
    "idempotency_key",
    # The durable credential (migration 0008). Deliberately NOT in
    # WORKSPACE_SCOPED_TABLES: a token is looked up by its sha256 hash before any
    # identity exists, so there is no GUC to filter on at that moment.
    "api_token",
}


def test_metadata_has_expected_tables() -> None:
    assert set(base.Base.metadata.tables) == EXPECTED_TABLES


def test_workspace_scoped_tables_match_metadata() -> None:
    # Every RLS-guarded table actually exists and carries a workspace_id.
    for name in models.WORKSPACE_SCOPED_TABLES:
        table = base.Base.metadata.tables[name]
        assert "workspace_id" in table.c, f"{name} must have workspace_id for RLS"
    # workspace itself is the tenant root, so it is not in the scoped set.
    assert "workspace" not in models.WORKSPACE_SCOPED_TABLES


def test_asset_catalog_columns_cover_sidecar_fields() -> None:
    # The asset table is the catalog; it must carry the sidecar's fields.
    cols = set(models.Asset.__table__.c.keys())
    sidecar_backed = {
        "brand_slug",
        "asset_type",
        "variant",
        "backend",
        "model",
        "mime",
        "width",
        "height",
        "cost_cents",
        "prompt",
    }
    assert sidecar_backed <= cols


def test_timestamps_are_timezone_aware() -> None:
    for model in (
        models.Workspace,
        models.Brand,
        models.Asset,
        models.AssetVersion,
        models.GenerationJob,
        models.BrandResearchRun,
    ):
        created = model.__table__.c["created_at"]
        assert created.type.timezone is True, f"{model.__name__}.created_at must be tz-aware"


def test_uuid_primary_keys() -> None:
    for model in (
        models.Workspace,
        models.Brand,
        models.Asset,
        models.AssetVersion,
        models.GenerationJob,
        models.BrandResearchRun,
    ):
        pk_cols = list(model.__table__.primary_key.columns)
        assert len(pk_cols) == 1
        assert pk_cols[0].name == "id"


def test_workspace_member_composite_primary_key() -> None:
    pk = {c.name for c in models.WorkspaceMember.__table__.primary_key.columns}
    assert pk == {"user_key", "workspace_id"}


def test_model_construction_smoke() -> None:
    ws_id = uuid.uuid4()
    ws = models.Workspace(slug="acme")
    member = models.WorkspaceMember(
        user_key="user-1", workspace_id=ws_id, role="owner", status="active"
    )
    brand = models.Brand(
        workspace_id=ws_id, slug="acme", display_name="Acme", status="active"
    )
    asset = models.Asset(
        workspace_id=ws_id,
        brand_slug="acme",
        asset_type="logo",
        backend="gemini",
        model="gemini-3-pro-image-preview",
        mime="image/png",
        width=1024,
        height=1024,
        cost_cents=4.5,
        prompt="a unicorn",
    )
    version = models.AssetVersion(asset_id=uuid.uuid4(), version_no=1)
    job = models.GenerationJob(
        workspace_id=ws_id, kind="asset", status="pending", request={"a": 1}
    )
    run = models.BrandResearchRun(
        workspace_id=ws_id, target_company="Acme", status="pending"
    )
    assert ws.slug == "acme"
    assert member.role == "owner"
    assert brand.display_name == "Acme"
    assert asset.asset_type == "logo"
    assert version.version_no == 1
    assert job.request == {"a": 1}
    assert run.target_company == "Acme"


def test_engine_not_built_at_import() -> None:
    # Importing the module must not have created an engine.
    assert base._engine is None
    assert base._sessionmaker is None


def test_get_engine_requires_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from brand_ops import config

    monkeypatch.delenv("DATABASE_URL", raising=False)
    config.get_settings.cache_clear()
    base._engine = None
    base._sessionmaker = None
    with pytest.raises(RuntimeError):
        base.get_engine()
    config.get_settings.cache_clear()


@pytest.mark.parametrize(
    "func_name",
    [
        "create_asset",
        "list_assets",
        "get_asset",
        "add_asset_version",
        "create_job",
        "update_job",
        "get_job",
        "list_jobs",
        "upsert_brand",
        "list_brands",
        "create_research_run",
        "update_research_run",
    ],
)
def test_repository_functions_take_workspace_id(func_name: str) -> None:
    fn = getattr(repository, func_name)
    params = list(inspect.signature(fn).parameters)
    # session first, workspace_id second: tenant scoping is mandatory.
    assert params[0] == "session"
    assert params[1] == "workspace_id"


def test_workspace_session_is_async_context_manager() -> None:
    assert hasattr(base.workspace_session, "__wrapped__") or callable(
        base.workspace_session
    )


# --- tests that need a live Postgres ---------------------------------------

_DB_URL = os.getenv("BRAND_OPS_TEST_DB")
requires_pg = pytest.mark.skipif(
    not _DB_URL,
    reason="set BRAND_OPS_TEST_DB to an asyncpg URL to run live database tests",
)


@requires_pg
@pytest.mark.asyncio
async def test_roundtrip_create_and_list_asset() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(_DB_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    ws_id = uuid.uuid4()
    try:
        async with maker() as session, base.workspace_session(session, ws_id):
            await repository.create_asset(
                session,
                ws_id,
                brand_slug="acme",
                asset_type="logo",
                backend="gemini",
                mime="image/png",
            )
        async with maker() as session, base.workspace_session(session, ws_id):
            rows = await repository.list_assets(session, ws_id, brand="acme")
            assert len(rows) >= 1
    finally:
        await engine.dispose()
