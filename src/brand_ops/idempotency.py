"""Idempotency-Key handling for cost-incurring POSTs (image generation).

A client that retries a generation with the same ``Idempotency-Key`` header must
get the original result back, not a second generation and a second charge. The
durable claim lives in the workspace-scoped ``idempotency_key`` table (migration
0006); the flow is claim -> do the work -> complete, with a concurrent or repeat
caller replaying the stored response, getting 409 while it is in progress, or 422 on
a key reused with different parameters.

Like the catalog dual-write and the cost meter beside it, this runs on a throwaway
``asyncio.run`` loop inside a synchronous FastAPI threadpool route, so it uses the
NullPool bridge sessionmaker (never the pooled engine's loop-bound connections). It
is best-effort on the INFRASTRUCTURE boundary only: with no active workspace, no
``DATABASE_URL``, or a transient DB error, idempotency degrades to "no dedup" (the
request proceeds normally) rather than failing, the same posture as the metering
bridge. The dedup guarantee holds on the happy path (DB reachable, request scoped).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

_log = logging.getLogger("brand_ops.idempotency")


@dataclass(frozen=True)
class IdemOutcome:
    """The result of claiming an Idempotency-Key. Exactly one state is meaningful.

    - ``claimed``: this request won the claim; do the work, then complete/release.
    - ``replay`` (+ ``replay_status``): a prior identical request completed; return it.
    - ``in_progress``: another request holds the claim right now (-> 409).
    - ``conflict``: the key was used before with a DIFFERENT request (-> 422).
    - ``skip``: idempotency could not be applied (no workspace/DB/transient error);
      proceed WITHOUT dedup.
    """

    claimed: bool = False
    replay: dict | None = None
    replay_status: int | None = None
    in_progress: bool = False
    conflict: bool = False
    skip: bool = False


_SKIP = IdemOutcome(skip=True)


def fingerprint(path: str, payload: dict) -> str:
    """A stable sha256 of the request that must match for a replay to be valid.

    Namespaced by ``path`` so the same key on two endpoints is a conflict, and taken
    over the canonical (sorted-key) JSON of the body so a reused key with different
    parameters is detected.
    """
    blob = json.dumps({"path": path, "body": payload}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _running_loop() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def begin_sync(workspace_id: str | None, key: str | None, fp: str) -> IdemOutcome:
    """Claim ``key`` for this workspace, or report the existing claim's state.

    Degrades to ``skip`` (proceed without dedup) on the infrastructure boundary: no
    workspace, no key, a running loop, no DB, or any error.
    """
    if not workspace_id or not key or _running_loop():
        return _SKIP
    import asyncio

    async def _run() -> IdemOutcome:
        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from .db.base import get_bridge_sessionmaker, workspace_session
        from .db.models import IdempotencyKey

        sm = get_bridge_sessionmaker()  # raises if DATABASE_URL is unset
        async with sm() as session:
            async with workspace_session(session, workspace_id) as scoped:
                # Atomic claim: RETURNING tells us whether WE inserted the row or
                # lost the race (a concurrent insert blocks here until it commits,
                # then this conflicts and returns nothing).
                claim = (
                    pg_insert(IdempotencyKey)
                    .values(
                        workspace_id=workspace_id,
                        idem_key=key,
                        request_fingerprint=fp,
                        status="in_progress",
                    )
                    .on_conflict_do_nothing(index_elements=["workspace_id", "idem_key"])
                    .returning(IdempotencyKey.id)
                )
                if (await scoped.execute(claim)).first() is not None:
                    return IdemOutcome(claimed=True)
                # Lost the claim: read the existing row to decide what to return.
                row = (
                    await scoped.execute(
                        select(IdempotencyKey).where(
                            IdempotencyKey.workspace_id == workspace_id,
                            IdempotencyKey.idem_key == key,
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    return _SKIP  # vanished under us; proceed without dedup
                if row.request_fingerprint != fp:
                    return IdemOutcome(conflict=True)
                if row.status == "completed":
                    return IdemOutcome(
                        replay=row.response_body or {},
                        replay_status=row.response_status or 200,
                    )
                return IdemOutcome(in_progress=True)

    try:
        return asyncio.run(_run())
    except Exception:  # noqa: BLE001 - best-effort on the infra boundary
        _log.debug("idempotency begin skipped", exc_info=True)
        return _SKIP


def complete_sync(
    workspace_id: str | None, key: str | None, *, status: int, body: dict
) -> None:
    """Mark a claimed key ``completed`` with the response to replay. Best-effort."""
    if not workspace_id or not key or _running_loop():
        return
    import asyncio

    async def _run() -> None:
        from sqlalchemy import func, update

        from .db.base import get_bridge_sessionmaker, workspace_session
        from .db.models import IdempotencyKey

        sm = get_bridge_sessionmaker()
        async with sm() as session:
            async with workspace_session(session, workspace_id) as scoped:
                await scoped.execute(
                    update(IdempotencyKey)
                    .where(
                        IdempotencyKey.workspace_id == workspace_id,
                        IdempotencyKey.idem_key == key,
                    )
                    .values(
                        status="completed",
                        response_status=status,
                        response_body=body,
                        completed_at=func.now(),
                    )
                )

    try:
        asyncio.run(_run())
    except Exception:  # noqa: BLE001 - best-effort; a missed complete just means a
        # retry re-does the work rather than replaying.
        _log.debug("idempotency complete skipped", exc_info=True)


def release_sync(workspace_id: str | None, key: str | None) -> None:
    """Release a claimed-but-unfinished key so a retry can re-attempt. Best-effort."""
    if not workspace_id or not key or _running_loop():
        return
    import asyncio

    async def _run() -> None:
        from sqlalchemy import delete

        from .db.base import get_bridge_sessionmaker, workspace_session
        from .db.models import IdempotencyKey

        sm = get_bridge_sessionmaker()
        async with sm() as session:
            async with workspace_session(session, workspace_id) as scoped:
                await scoped.execute(
                    delete(IdempotencyKey).where(
                        IdempotencyKey.workspace_id == workspace_id,
                        IdempotencyKey.idem_key == key,
                        IdempotencyKey.status == "in_progress",
                    )
                )

    try:
        asyncio.run(_run())
    except Exception:  # noqa: BLE001 - best-effort
        _log.debug("idempotency release skipped", exc_info=True)
