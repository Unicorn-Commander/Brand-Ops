"""ORM tables for Brand-Ops.

The ``asset`` table is the catalog: its columns are exactly the facts the
output sidecar records (see ``brand_ops.output.save_result``) plus a storage
key, so a generated asset persists the same way it was authored.

Conventions across every table:
  * uuid primary keys, generated server side with gen_random_uuid().
  * timezone-aware timestamps, defaulted server side with now().
  * JSON payloads for request/result/profile/sources blobs.
  * every workspace-scoped table carries a workspace_id that row level
    security filters on (policies are created in the migration).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

_UUID_PK = text("gen_random_uuid()")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=_UUID_PK,
        default=uuid.uuid4,
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Workspace(Base):
    __tablename__ = "workspace"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    created_at: Mapped[datetime] = _created_at()


class WorkspaceMember(Base):
    __tablename__ = "workspace_member"

    user_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    is_default: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false"), default=False
    )


class Brand(Base):
    __tablename__ = "brand"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_brand_workspace_slug"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_slug: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tagline: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Asset(Base):
    __tablename__ = "asset"
    # The catalog source-of-truth key: one asset row per stored file, keyed by its
    # workspace-relative ``storage_key`` (``<slug>/<kind>/<asset_type>/<file>``).
    # Partial-unique (only when storage_key is set) so the dual-write and the
    # backfill can upsert idempotently on it, while pre-SoR rows that never carried
    # a key (or a future keyless row) are unconstrained. A null storage_key is
    # already distinct under Postgres unique semantics; the partial predicate makes
    # that explicit and keeps the index small.
    __table_args__ = (
        Index(
            "uq_asset_ws_storage_key",
            "workspace_id",
            "storage_key",
            unique=True,
            postgresql_where=text("storage_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    brand_slug: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # generated | imported -- the catalog "kind", mirrors the on-disk subtree and
    # the AssetRecord.kind the read path returns. Nullable for pre-SoR rows.
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    variant: Mapped[str | None] = mapped_column(String(120), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    backend: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime: Mapped[str | None] = mapped_column(String(64), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transparent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cost_cents: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    prompt: Mapped[str | None] = mapped_column(String, nullable=True)
    # The sidecar's reference_images list (relative paths) and the lifecycle +
    # compliance stamp, so a DB-served record reconstructs the same AssetRecord the
    # sidecar read path returns. JSON to match the request/result/profile blobs.
    reference_images: Mapped[list | None] = mapped_column(JSON, nullable=True)
    lifecycle_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    compliance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The sidecar's ``created_at`` string label (``YYYY-MM-DD-HH-MM-SS``), the value
    # the read path sorts on and returns verbatim; distinct from the row's own
    # server-set ``created_at`` timestamp. Kept so DB reads sort identically to
    # sidecar reads.
    created_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    # Soft-delete tombstone: set to the delete instant when an asset is removed,
    # null while live. A soft-deleted row is hidden from every catalog read
    # (list_assets filters deleted_at IS NULL) but its bytes stay on disk until the
    # retention sweeper hard-purges it past BRAND_OPS_RETENTION_DAYS (migration
    # 0007). Recoverable until then, and the data-retention audit gap closes.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    versions: Mapped[list[AssetVersion]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="AssetVersion.version_no",
    )


class AssetVersion(Base):
    __tablename__ = "asset_version"
    __table_args__ = (
        UniqueConstraint("asset_id", "version_no", name="uq_asset_version_no"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    asset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("asset.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = _created_at()

    asset: Mapped[Asset] = relationship(back_populates="versions")


class GenerationJob(Base):
    __tablename__ = "generation_job"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True
    )
    request: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class BrandResearchRun(Base):
    __tablename__ = "brand_research_run"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True
    )
    profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sources: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class AgentGovernance(Base):
    """Per-agent governance for a workspace: posture, orders, budget, model.

    One row per (workspace, agent_kind, agent_ref). A missing row means "the
    agent's defaults"; the API synthesizes a default-shaped record for the
    well-known agents so the Crew page always renders. ``autonomy`` is one of
    governance.AUTONOMY_LEVELS, ``weekly_budget_cents`` null = no cap (v1 stores
    and displays the cap but does not enforce it), ``model`` null = the agent's
    own default. Workspace-scoped: row level security filters on workspace_id
    (policy created in migration 0003).
    """

    __tablename__ = "agent_governance"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "agent_kind",
            "agent_ref",
            name="uq_agent_governance_ws_kind_ref",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    autonomy: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'propose'"), default="propose"
    )
    standing_orders: Mapped[str | None] = mapped_column(Text, nullable=True)
    weekly_budget_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AgentSpendEvent(Base):
    """One metered agent turn: the append-only spend ledger.

    A row is recorded per metered turn (text chat in v1) carrying the gateway's
    token usage and an ESTIMATED ``cost_cents`` (see governance.estimate_cost_cents).
    ``kind`` is "chat" or "image". Indexed on workspace_id, created_at, and
    (workspace_id, agent_kind, agent_ref) for the per-agent 7-day rollup.
    Workspace-scoped: row level security filters on workspace_id (policy created
    in migration 0003).
    """

    __tablename__ = "agent_spend_event"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    brand_slug: Mapped[str | None] = mapped_column(String(120), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_cents: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class IdempotencyKey(Base):
    """A claimed ``Idempotency-Key`` for a mutating, cost-incurring request.

    A client that retries a POST with the same ``Idempotency-Key`` header gets the
    original result back instead of a second generation (and a second charge). The
    claim is workspace-scoped (RLS on workspace_id, policy created in migration
    0006). The flow: a request INSERTs the key ``in_progress`` (ON CONFLICT DO
    NOTHING) to claim it, does the work, then marks it ``completed`` with the stored
    response; a concurrent or repeat request that loses the claim reads this row to
    replay the response, 409 while it is still in progress, or 422 if the same key
    arrives with a different request fingerprint. Unique on (workspace_id, idem_key)
    so the claim is atomic.
    """

    __tablename__ = "idempotency_key"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idem_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'in_progress'")
    )
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "idem_key", name="uq_idempotency_ws_key"),
    )


class ApiToken(Base):
    """A durable Brand-Ops credential (personal access token, "PAT").

    Brand-Ops's only credentials were short-lived IdP JWTs (Keycloak uchub /
    Brigade), so a headless MCP client -- the stdio transport in particular --
    had nothing it could hold and therefore ran with no identity at all. This
    table is the missing durable credential: a ``brand_pat_``-prefixed secret
    that is issued once, shown once, and stored ONLY as a sha256 hash.

    Posture:
      * ``token_hash`` is ``sha256(raw_token)`` hex. The plaintext is never
        stored, never logged, and cannot be recovered -- a lost token is
        revoked and re-minted, not read back.
      * ``token_prefix`` is a display-only, non-secret fragment
        (``brand_pat_`` + the first 8 characters) so a human can tell two
        tokens apart in a list.
      * ``user_key`` is the OWNING uc_uid. A PAT never carries its own identity
        or its own grants: it authenticates AS its owner and is re-resolved
        through ``workspace_member`` on every use, so revoking the human's
        membership immediately revokes the token's reach.
      * ``workspace_id`` / ``brand_slug`` are OPTIONAL bindings that only ever
        NARROW the owner's reach (never widen it): a workspace-bound token is
        refused outside that workspace, a brand-bound token sees only that one
        brand.
      * ``role`` is the minter's in-workspace role AT MINT TIME, and is the same
        kind of narrowing in the time dimension: every use takes the LOWER of it
        and the owner's live role (``tokens.cap_role``), so a token minted by a
        viewer stays a viewer's token even after its owner is promoted. There is
        deliberately no ``scopes`` column: the one that used to be here was
        stored, carried onto the principal, and enforced by nothing.
      * ``expires_at`` (null = no expiry) and ``revoked_at`` (null = live) are
        both checked on every verification.

    Deliberately NOT row-level-security scoped: verification looks a row up by
    its sha256 hash BEFORE any identity exists, so there is no GUC to filter on
    at that moment. The row is reachable only by presenting the secret whose
    hash it is; every management read filters on ``user_key`` in the query.
    """

    __tablename__ = "api_token"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # The owning suite identity (uc_uid). Indexed for the "my tokens" listing.
    user_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # sha256 hex of the raw token. Unique so a verification is a single indexed
    # equality lookup and two rows can never collide on one secret.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    # The minter's workspace role at mint time; a CAP, never a grant.
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    brand_slug: Mapped[str | None] = mapped_column(String(120), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = _created_at()


# Tables that carry a workspace_id and are guarded by row level security. The
# migration enables RLS and creates a policy on each of these.
WORKSPACE_SCOPED_TABLES = (
    "workspace_member",
    "brand",
    "asset",
    "generation_job",
    "brand_research_run",
    "agent_governance",
    "agent_spend_event",
    "idempotency_key",
)
