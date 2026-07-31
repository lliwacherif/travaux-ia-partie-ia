"""Snapshot-aware semantic response cache."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.v3.models import SemanticCache
from app.v3.prompts import prompt_hash
from app.v3.ssot import SEMANTIC_MODEL, SSOT_VERSION

ContractT = TypeVar("ContractT", bound=BaseModel)


def payload_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


class SemanticCacheRepository:
    def __init__(self, session: AsyncSession, *, ttl_hours: int = 24 * 30) -> None:
        self.session = session
        self.ttl_hours = ttl_hours

    async def get(
        self,
        *,
        input_hash: str,
        response_kind: str,
        contract: type[ContractT],
        library_version: str,
    ) -> ContractT | None:
        now = datetime.now(timezone.utc)
        stmt = select(SemanticCache).where(
            SemanticCache.input_hash == input_hash,
            SemanticCache.prompt_hash == prompt_hash(),
            SemanticCache.ssot_version == SSOT_VERSION,
            SemanticCache.library_version == library_version,
            SemanticCache.semantic_model == settings.V3_OPENAI_SEMANTIC_MODEL,
            SemanticCache.response_kind == response_kind,
            SemanticCache.status == "READY",
            (SemanticCache.expires_at.is_(None) | (SemanticCache.expires_at > now)),
        )
        record = (await self.session.execute(stmt)).scalar_one_or_none()
        if record is None:
            return None
        raw = json.dumps(record.response_payload, ensure_ascii=False)
        return contract.model_validate_json(raw)

    async def put(
        self,
        *,
        input_hash: str,
        config_hash: str,
        response_kind: str,
        value: BaseModel,
        library_version: str,
    ) -> None:
        payload = value.model_dump(mode="json")
        existing_stmt = select(SemanticCache).where(
            SemanticCache.input_hash == input_hash,
            SemanticCache.prompt_hash == prompt_hash(),
            SemanticCache.ssot_version == SSOT_VERSION,
            SemanticCache.library_version == library_version,
            SemanticCache.semantic_model == settings.V3_OPENAI_SEMANTIC_MODEL,
            SemanticCache.response_kind == response_kind,
        )
        record = (await self.session.execute(existing_stmt)).scalar_one_or_none()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=self.ttl_hours)
        if record is None:
            record = SemanticCache(
                input_hash=input_hash,
                prompt_hash=prompt_hash(),
                config_hash=config_hash,
                ssot_version=SSOT_VERSION,
                library_version=library_version,
                semantic_model=settings.V3_OPENAI_SEMANTIC_MODEL,
                response_kind=response_kind,
                response_payload=payload,
                response_hash=payload_hash(payload),
                version=1,
                status="READY",
                expires_at=expires_at,
            )
            self.session.add(record)
        else:
            record.response_payload = payload
            record.response_hash = payload_hash(payload)
            record.status = "READY"
            record.expires_at = expires_at
        await self.session.flush()


__all__ = ["SemanticCacheRepository", "payload_hash"]
