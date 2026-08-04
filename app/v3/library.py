"""V3.2 library snapshot resolution (orchestrator stage 0B).

Loads the current published+validated snapshot, or the last validated
published snapshot as an authorized degraded fallback. Never touches V2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.v3.models import LibrarySnapshot


class LibrarySnapshotUnavailableError(RuntimeError):
    """Raised when no published+validated snapshot exists at all."""


@dataclass(frozen=True, slots=True)
class ResolvedLibrarySnapshot:
    """V3.2 — snapshot handle threaded through every retrieval stage."""

    snapshot_id: str
    library_version: str
    content_hash: str
    status: str
    validated_at: datetime | None
    published_at: datetime | None
    fallback_snapshot_used: bool


async def load_current_published_validated_snapshot(
    session: AsyncSession,
    *,
    preferred_library_version: str | None = None,
) -> ResolvedLibrarySnapshot:
    """Primary path: current published + validated snapshot."""

    stmt = (
        select(LibrarySnapshot)
        .where(
            LibrarySnapshot.status == "PUBLISHED",
            LibrarySnapshot.validated_at.is_not(None),
            LibrarySnapshot.published_at.is_not(None),
        )
        .order_by(LibrarySnapshot.published_at.desc())
    )
    if preferred_library_version:
        preferred = (
            await session.execute(
                select(LibrarySnapshot).where(
                    LibrarySnapshot.library_version == preferred_library_version,
                    LibrarySnapshot.status == "PUBLISHED",
                    LibrarySnapshot.validated_at.is_not(None),
                    LibrarySnapshot.published_at.is_not(None),
                )
            )
        ).scalar_one_or_none()
        if preferred is not None:
            return _resolve(preferred, fallback=False)

    row = (await session.execute(stmt.limit(1))).scalar_one_or_none()
    if row is None:
        raise LibrarySnapshotUnavailableError("NO_PUBLISHED_VALIDATED_SNAPSHOT")
    return _resolve(row, fallback=False)


async def load_last_published_validated_snapshot(
    session: AsyncSession,
) -> ResolvedLibrarySnapshot:
    """Authorized degraded path when the current snapshot is unavailable."""

    row = (
        await session.execute(
            select(LibrarySnapshot)
            .where(
                LibrarySnapshot.status == "PUBLISHED",
                LibrarySnapshot.validated_at.is_not(None),
                LibrarySnapshot.published_at.is_not(None),
            )
            .order_by(LibrarySnapshot.published_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise LibrarySnapshotUnavailableError(
            "NO_LAST_VALIDATED_SNAPSHOT_SERVICE_NOT_OPERATIONAL"
        )
    return _resolve(row, fallback=True)


def _resolve(
    row: LibrarySnapshot,
    *,
    fallback: bool,
) -> ResolvedLibrarySnapshot:
    return ResolvedLibrarySnapshot(
        snapshot_id=str(row.snapshot_id),
        library_version=row.library_version,
        content_hash=row.content_hash,
        status=row.status,
        validated_at=row.validated_at,
        published_at=row.published_at,
        fallback_snapshot_used=fallback,
    )


__all__ = [
    "LibrarySnapshotUnavailableError",
    "ResolvedLibrarySnapshot",
    "load_current_published_validated_snapshot",
    "load_last_published_validated_snapshot",
]
