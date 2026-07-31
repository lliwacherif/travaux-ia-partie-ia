"""Hierarchical official-line -> parent-pack retrieval for V3."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.v3.contracts import (
    DemandItem,
    DemandMatrix,
    DemandStatus,
    LineSearchHit,
    PackCandidate,
    TradeArbitration,
)
from app.v3.models import (
    PriceVersion,
    QuotePack,
    QuotePackLine,
    TechnicalDependency,
    TradeCatalog,
    VatRule,
)
from app.v3.ssot import EMBEDDING_DIMENSIONS, SEARCH, SEARCH_WEIGHTS


class EmbeddingUnavailableError(RuntimeError):
    """Signals the authorized lexical-only path."""


@dataclass(frozen=True, slots=True)
class CatalogLine:
    line_id: str
    pack_id: str
    pack_code: str
    pack_version: int
    trade_code: str
    service_code: str | None
    phase: str
    slot_index: int
    designation: str
    normalized_action: str
    object_family: str
    material_family: str | None
    synonym_tags: tuple[str, ...]
    capability_tags: tuple[str, ...]
    exclusion_tags: tuple[str, ...]
    technical_dependency_ids: tuple[str, ...]
    unit: str
    quantity_rule: str
    linear_measurement_mode: str | None
    linear_formula_id: str | None
    linear_params: dict[str, Any] | None
    quantity_precision: int
    rounding_step: float | None
    default_quantity: float
    price_id: str
    price_version: int
    unit_price_cents: int
    vat_rule_id: str
    vat_rule_version: int
    vat_rate: float
    replacement_group: str | None
    replaceable: bool


@dataclass(frozen=True, slots=True)
class CatalogPack:
    pack_id: str
    pack_code: str
    version: int
    flow: str
    trade_code: str
    service_code: str | None
    title: str
    searchable_text: str
    fallback_rank: int | None
    lines: tuple[CatalogLine, ...]


def _item_query(item: DemandItem) -> str:
    return " ".join(
        part
        for part in (
            item.action,
            item.object,
            item.material,
            item.location,
            *item.source_excerpt.split(),
        )
        if part
    )


def reciprocal_rank(rank: int | None, *, k: int | None = None) -> float:
    if rank is None:
        return 0.0
    return 1.0 / ((k or SEARCH.rrf_k) + rank)


def compute_final_score(candidate: PackCandidate) -> float:
    score = (
        SEARCH_WEIGHTS["coverage_score"] * candidate.coverage_score
        + SEARCH_WEIGHTS["rerank_score"] * candidate.rerank_score
        + SEARCH_WEIGHTS["line_parent_score"] * candidate.line_parent_score
        + SEARCH_WEIGHTS["direct_pack_score"] * candidate.direct_pack_score
        + SEARCH_WEIGHTS["object_exactness"] * candidate.object_exactness
        + SEARCH_WEIGHTS["material_compatibility"] * candidate.material_compatibility
        + SEARCH_WEIGHTS["unit_compatibility"] * candidate.unit_compatibility
        + SEARCH_WEIGHTS["context_compatibility"] * candidate.context_compatibility
        + SEARCH_WEIGHTS["dense_score"] * candidate.dense_score
        + SEARCH_WEIGHTS["lexical_score"] * candidate.lexical_score
        - candidate.exclusion_penalty
        - candidate.extra_scope_penalty
    )
    return round(score, 12)


class EmbeddingService:
    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        key = settings.V3_OPENAI_API_KEY or settings.OPENAI_API_KEY
        self._client = client or (AsyncOpenAI(api_key=key) if key else None)

    async def embed(self, text: str) -> list[float]:
        if self._client is None:
            raise EmbeddingUnavailableError("OPENAI_API_KEY_MISSING")
        try:
            response = await self._client.embeddings.create(
                model=settings.V3_OPENAI_EMBEDDING_MODEL,
                input=text,
                dimensions=EMBEDDING_DIMENSIONS,
            )
        except Exception as exc:
            raise EmbeddingUnavailableError(f"{type(exc).__name__}:{exc}") from exc
        embedding = list(response.data[0].embedding)
        if len(embedding) != EMBEDDING_DIMENSIONS:
            raise EmbeddingUnavailableError("INVALID_EMBEDDING_DIMENSION")
        return embedding


class CatalogRepository:
    """Read-only access to one published library snapshot."""

    def __init__(self, session: AsyncSession, library_version: str) -> None:
        self.session = session
        self.library_version = library_version

    def _published_line_base(
        self,
        arbitration: TradeArbitration,
    ) -> Select[Any]:
        return (
            select(QuotePackLine, QuotePack)
            .join(QuotePack, QuotePack.pack_id == QuotePackLine.pack_id)
            .where(
                QuotePack.status == "PUBLISHED",
                QuotePackLine.status == "PUBLISHED",
                QuotePack.library_version == self.library_version,
                QuotePackLine.library_version == self.library_version,
                QuotePack.flow == arbitration.flow.value,
                QuotePack.trade_code == arbitration.primary_trade_code,
                QuotePackLine.active.is_(True),
            )
        )

    async def trade_catalog(self) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                select(TradeCatalog)
                .where(
                    TradeCatalog.status == "PUBLISHED",
                    TradeCatalog.active.is_(True),
                    TradeCatalog.catalog_version == self.library_version,
                )
                .order_by(TradeCatalog.trade_code)
            )
        ).scalars()
        return [
            {
                "trade_code": row.trade_code,
                "flow": row.flow,
                "label": row.label,
                "active": row.active,
                "service_codes": [],
            }
            for row in rows
        ]

    async def trade_lexicon(self) -> dict[str, tuple[str, ...]]:
        catalog = await self.trade_catalog()
        return {
            str(row["trade_code"]): (
                str(row["label"]),
                str(row["trade_code"]).replace("_", " "),
            )
            for row in catalog
        }

    async def lexical_line_hits(
        self,
        item: DemandItem,
        arbitration: TradeArbitration,
        *,
        limit: int = SEARCH.line_top_k_per_request_item,
    ) -> list[tuple[QuotePackLine, QuotePack, float]]:
        query = func.websearch_to_tsquery("french", _item_query(item))
        score = func.ts_rank_cd(QuotePackLine.lexical, query).label("lexical_score")
        stmt = (
            self._published_line_base(arbitration)
            .add_columns(score)
            .where(QuotePackLine.lexical.op("@@")(query))
            .order_by(score.desc(), QuotePackLine.line_id.asc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(row[0], row[1], float(row[2] or 0.0)) for row in rows]

    async def dense_line_hits(
        self,
        embedding: Sequence[float],
        arbitration: TradeArbitration,
        *,
        limit: int = SEARCH.line_top_k_per_request_item,
    ) -> list[tuple[QuotePackLine, QuotePack, float]]:
        distance = QuotePackLine.embedding.cosine_distance(list(embedding)).label(
            "distance"
        )
        stmt = (
            self._published_line_base(arbitration)
            .add_columns(distance)
            .where(QuotePackLine.embedding.is_not(None))
            .order_by(distance.asc(), QuotePackLine.line_id.asc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            (row[0], row[1], max(0.0, 1.0 - float(row[2] or 1.0)))
            for row in rows
        ]

    async def direct_pack_hits(
        self,
        query_text: str,
        arbitration: TradeArbitration,
        embedding: Sequence[float] | None,
    ) -> list[tuple[QuotePack, float, float]]:
        """Return a true lexical+dense RRF union of complete packs."""

        ts_query = func.websearch_to_tsquery("french", query_text)
        lexical = func.ts_rank_cd(QuotePack.lexical, ts_query).label("lexical_score")
        base_filters = (
            QuotePack.status == "PUBLISHED",
            QuotePack.library_version == self.library_version,
            QuotePack.flow == arbitration.flow.value,
            QuotePack.trade_code == arbitration.primary_trade_code,
        )
        lexical_stmt = (
            select(QuotePack, lexical)
            .where(
                *base_filters,
                QuotePack.lexical.op("@@")(ts_query),
            )
            .order_by(lexical.desc(), QuotePack.pack_code.asc())
            .limit(SEARCH.direct_pack_top_k)
        )
        lexical_rows = (await self.session.execute(lexical_stmt)).all()

        dense_rows: list[Any] = []
        if embedding is not None:
            dense_distance = QuotePack.embedding.cosine_distance(
                list(embedding)
            ).label("dense_distance")
            dense_stmt = (
                select(QuotePack, dense_distance)
                .where(*base_filters, QuotePack.embedding.is_not(None))
                .order_by(dense_distance.asc(), QuotePack.pack_code.asc())
                .limit(SEARCH.direct_pack_top_k)
            )
            dense_rows = list((await self.session.execute(dense_stmt)).all())

        lexical_by_id = {
            str(row[0].pack_id): (row[0], float(row[1] or 0.0))
            for row in lexical_rows
        }
        dense_by_id = {
            str(row[0].pack_id): (
                row[0],
                max(0.0, 1.0 - float(row[1] or 1.0)),
            )
            for row in dense_rows
        }
        lexical_rank = {
            str(row[0].pack_id): rank
            for rank, row in enumerate(lexical_rows, start=1)
        }
        dense_rank = {
            str(row[0].pack_id): rank
            for rank, row in enumerate(dense_rows, start=1)
        }
        fused: list[tuple[float, str, QuotePack, float, float]] = []
        for pack_id in set(lexical_by_id) | set(dense_by_id):
            pack = (
                lexical_by_id[pack_id][0]
                if pack_id in lexical_by_id
                else dense_by_id[pack_id][0]
            )
            fused.append(
                (
                    reciprocal_rank(lexical_rank.get(pack_id))
                    + reciprocal_rank(dense_rank.get(pack_id)),
                    pack.pack_code,
                    pack,
                    lexical_by_id.get(pack_id, (None, 0.0))[1],
                    dense_by_id.get(pack_id, (None, 0.0))[1],
                )
            )
        fused.sort(key=lambda row: (-row[0], row[1]))
        return [
            (pack, lexical_score, dense_score)
            for _rrf, _code, pack, lexical_score, dense_score in fused[
                : SEARCH.direct_pack_top_k
            ]
        ]

    async def load_pack(self, pack_id: str) -> CatalogPack | None:
        try:
            parsed_id = uuid.UUID(pack_id)
        except ValueError:
            return None
        pack = await self.session.get(QuotePack, parsed_id)
        if (
            pack is None
            or pack.status != "PUBLISHED"
            or pack.library_version != self.library_version
        ):
            return None
        stmt = (
            select(QuotePackLine, PriceVersion, VatRule)
            .join(
                PriceVersion,
                (PriceVersion.price_id == QuotePackLine.price_id)
                & (PriceVersion.version == QuotePackLine.price_version),
            )
            .join(
                VatRule,
                (VatRule.vat_rule_id == QuotePackLine.vat_rule_id)
                & (VatRule.version == QuotePackLine.vat_rule_version),
            )
            .where(
                QuotePackLine.pack_id == pack.pack_id,
                QuotePackLine.status == "PUBLISHED",
                QuotePackLine.active.is_(True),
            )
            .order_by(QuotePackLine.phase, QuotePackLine.slot_index)
        )
        rows = (await self.session.execute(stmt)).all()
        return CatalogPack(
            pack_id=str(pack.pack_id),
            pack_code=pack.pack_code,
            version=pack.version,
            flow=pack.flow,
            trade_code=pack.trade_code,
            service_code=pack.service_code,
            title=pack.title,
            searchable_text=pack.searchable_text,
            fallback_rank=pack.fallback_rank,
            lines=tuple(
                _catalog_line(line, pack, price, vat) for line, price, vat in rows
            ),
        )

    async def load_fallback(self, arbitration: TradeArbitration) -> CatalogPack | None:
        trade = await self.session.get(TradeCatalog, arbitration.primary_trade_code)
        if trade is None or trade.fallback_pack_id is None:
            return None
        return await self.load_pack(str(trade.fallback_pack_id))

    async def fallback_pack_ids(self, arbitration: TradeArbitration) -> list[str]:
        """Return ordered official fallback pack IDs for the arbitrated trade."""

        trade = await self.session.get(TradeCatalog, arbitration.primary_trade_code)
        ordered: list[str] = []
        if trade is not None and trade.fallback_pack_id is not None:
            ordered.append(str(trade.fallback_pack_id))

        ranked = (
            await self.session.execute(
                select(QuotePack.pack_id)
                .where(
                    QuotePack.status == "PUBLISHED",
                    QuotePack.library_version == self.library_version,
                    QuotePack.flow == arbitration.flow.value,
                    QuotePack.trade_code == arbitration.primary_trade_code,
                    QuotePack.fallback_rank.is_not(None),
                )
                .order_by(QuotePack.fallback_rank.asc(), QuotePack.pack_code.asc())
            )
        ).scalars().all()
        for pack_id in ranked:
            value = str(pack_id)
            if value not in ordered:
                ordered.append(value)
        return ordered

    async def load_repair_lines(
        self,
        *,
        trade_code: str,
        replacement_group: str,
        exclude_pack_id: str,
    ) -> list[CatalogLine]:
        stmt = (
            select(QuotePackLine, QuotePack, PriceVersion, VatRule)
            .join(QuotePack, QuotePack.pack_id == QuotePackLine.pack_id)
            .join(
                PriceVersion,
                (PriceVersion.price_id == QuotePackLine.price_id)
                & (PriceVersion.version == QuotePackLine.price_version),
            )
            .join(
                VatRule,
                (VatRule.vat_rule_id == QuotePackLine.vat_rule_id)
                & (VatRule.version == QuotePackLine.vat_rule_version),
            )
            .where(
                QuotePack.status == "PUBLISHED",
                QuotePackLine.status == "PUBLISHED",
                QuotePack.library_version == self.library_version,
                QuotePack.trade_code == trade_code,
                QuotePackLine.phase == "CORE",
                QuotePackLine.replacement_group == replacement_group,
                QuotePackLine.pack_id != uuid.UUID(exclude_pack_id),
                QuotePackLine.active.is_(True),
            )
            .order_by(QuotePack.pack_code, QuotePackLine.slot_index)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            _catalog_line(line, pack, price, vat)
            for line, pack, price, vat in rows
        ]

    async def load_trade_lines(
        self,
        arbitration: TradeArbitration,
    ) -> list[CatalogLine]:
        stmt = (
            select(QuotePackLine, QuotePack, PriceVersion, VatRule)
            .join(QuotePack, QuotePack.pack_id == QuotePackLine.pack_id)
            .join(
                PriceVersion,
                (PriceVersion.price_id == QuotePackLine.price_id)
                & (PriceVersion.version == QuotePackLine.price_version),
            )
            .join(
                VatRule,
                (VatRule.vat_rule_id == QuotePackLine.vat_rule_id)
                & (VatRule.version == QuotePackLine.vat_rule_version),
            )
            .where(
                QuotePack.status == "PUBLISHED",
                QuotePackLine.status == "PUBLISHED",
                QuotePack.library_version == self.library_version,
                QuotePack.trade_code == arbitration.primary_trade_code,
                QuotePack.flow == arbitration.flow.value,
                QuotePackLine.active.is_(True),
            )
            .order_by(QuotePack.pack_code, QuotePackLine.phase, QuotePackLine.slot_index)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            _catalog_line(line, pack, price, vat)
            for line, pack, price, vat in rows
        ]

    async def load_dependencies(
        self,
        arbitration: TradeArbitration,
    ) -> dict[str, dict[str, Any]]:
        rows = (
            await self.session.execute(
                select(TechnicalDependency).where(
                    TechnicalDependency.trade_code
                    == arbitration.primary_trade_code,
                    TechnicalDependency.status == "PUBLISHED",
                    TechnicalDependency.active.is_(True),
                )
            )
        ).scalars()
        return {
            row.dependency_id: {
                "dependency_id": row.dependency_id,
                "version": row.version,
                "trade_code": row.trade_code,
                "label": row.label,
                "applicability_rule": row.applicability_rule,
                "active": row.active,
            }
            for row in rows
        }

    async def load_registry_records(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        prices = (
            await self.session.execute(
                select(PriceVersion).where(
                    PriceVersion.library_version == self.library_version,
                    PriceVersion.status == "PUBLISHED",
                )
            )
        ).scalars()
        vats = (
            await self.session.execute(
                select(VatRule).where(VatRule.status == "PUBLISHED")
            )
        ).scalars()
        return (
            [
                {
                    "price_id": str(row.price_id),
                    "version": row.version,
                    "unit_price_cents": row.unit_price_cents,
                    "effective_from": row.effective_from,
                    "effective_to": row.effective_to,
                    "active": True,
                }
                for row in prices
            ],
            [
                {
                    "vat_rule_id": row.vat_rule_id,
                    "version": row.version,
                    "rate": float(row.rate),
                    "active": True,
                }
                for row in vats
            ],
        )


def _catalog_line(
    line: QuotePackLine,
    pack: QuotePack,
    price: PriceVersion,
    vat: VatRule,
) -> CatalogLine:
    return CatalogLine(
        line_id=str(line.line_id),
        pack_id=str(line.pack_id),
        pack_code=pack.pack_code,
        pack_version=pack.version,
        trade_code=pack.trade_code,
        service_code=pack.service_code,
        phase=line.phase,
        slot_index=line.slot_index,
        designation=line.designation,
        normalized_action=line.normalized_action,
        object_family=line.object_family,
        material_family=line.material_family,
        synonym_tags=tuple(line.synonym_tags or ()),
        capability_tags=tuple(line.capability_tags or ()),
        exclusion_tags=tuple(line.exclusion_tags or ()),
        technical_dependency_ids=tuple(line.technical_dependency_ids or ()),
        unit=line.unit,
        quantity_rule=line.quantity_rule,
        linear_measurement_mode=line.linear_measurement_mode,
        linear_formula_id=line.linear_formula_id,
        linear_params=line.linear_params,
        quantity_precision=line.quantity_precision,
        rounding_step=float(line.rounding_step) if line.rounding_step else None,
        default_quantity=float(line.default_quantity),
        price_id=str(price.price_id),
        price_version=price.version,
        unit_price_cents=price.unit_price_cents,
        vat_rule_id=vat.vat_rule_id,
        vat_rule_version=vat.version,
        vat_rate=float(vat.rate),
        replacement_group=line.replacement_group,
        replaceable=line.replaceable,
    )


class HierarchicalSearch:
    def __init__(
        self,
        repository: CatalogRepository,
        embeddings: EmbeddingService | None = None,
    ) -> None:
        self.repository = repository
        self.embeddings = embeddings or EmbeddingService()

    async def search_lines(
        self,
        matrix: DemandMatrix,
        arbitration: TradeArbitration,
    ) -> tuple[list[LineSearchHit], bool, str | None]:
        all_hits: list[LineSearchHit] = []
        degraded = False
        reasons: list[str] = []
        required = [
            item for item in matrix.items if item.status is DemandStatus.REQUIRED
        ]
        for item in required:
            lexical = await self.repository.lexical_line_hits(item, arbitration)
            try:
                embedding = await self.embeddings.embed(_item_query(item))
                dense = await self.repository.dense_line_hits(
                    embedding, arbitration
                )
            except EmbeddingUnavailableError as exc:
                dense = []
                degraded = True
                reasons.append(str(exc))

            lexical_rank = {
                str(line.line_id): rank
                for rank, (line, _pack, _score) in enumerate(lexical, start=1)
            }
            dense_rank = {
                str(line.line_id): rank
                for rank, (line, _pack, _score) in enumerate(dense, start=1)
            }
            lexical_by_id = {
                str(line.line_id): (line, pack, score)
                for line, pack, score in lexical
            }
            dense_by_id = {
                str(line.line_id): (line, pack, score)
                for line, pack, score in dense
            }
            for line_id in sorted(set(lexical_by_id) | set(dense_by_id)):
                line, pack, lexical_score = lexical_by_id.get(
                    line_id, dense_by_id[line_id]
                )
                dense_score = dense_by_id.get(line_id, (None, None, 0.0))[2]
                score = reciprocal_rank(lexical_rank.get(line_id)) + reciprocal_rank(
                    dense_rank.get(line_id)
                )
                all_hits.append(
                    LineSearchHit(
                        request_item_id=item.request_item_id,
                        line_id=line_id,
                        pack_id=str(pack.pack_id),
                        pack_version=pack.version,
                        lexical_score=float(lexical_score),
                        dense_score=float(dense_score),
                        rrf_score=float(score),
                        match_reasons=[
                            reason
                            for reason, present in (
                                ("LEXICAL", line_id in lexical_by_id),
                                ("DENSE", line_id in dense_by_id),
                            )
                            if present
                        ],
                    )
                )
        return all_hits, degraded, ";".join(sorted(set(reasons))) or None

    async def direct_pack_candidates(
        self,
        description: str,
        arbitration: TradeArbitration,
    ) -> tuple[list[PackCandidate], bool, str | None]:
        degraded = False
        reason: str | None = None
        try:
            embedding = await self.embeddings.embed(description)
        except EmbeddingUnavailableError as exc:
            embedding = None
            degraded = True
            reason = str(exc)
        rows = await self.repository.direct_pack_hits(
            description, arbitration, embedding
        )
        candidates: list[PackCandidate] = []
        for rank, (pack, lexical_score, dense_score) in enumerate(rows, start=1):
            candidate = PackCandidate(
                pack_id=str(pack.pack_id),
                pack_version=pack.version,
                trade_code=pack.trade_code,
                service_code=pack.service_code,
                matched_line_ids=[],
                matched_request_item_ids=[],
                line_parent_score=0.0,
                direct_pack_score=reciprocal_rank(rank),
                lexical_score=lexical_score,
                dense_score=dense_score,
                rrf_score=reciprocal_rank(rank),
                rerank_score=0.0,
                coverage_score=0.0,
                extra_scope_penalty=0.0,
                final_score=0.0,
                pack_code=pack.pack_code,
                fallback_rank=pack.fallback_rank,
            )
            candidates.append(
                candidate.model_copy(update={"final_score": compute_final_score(candidate)})
            )
        return candidates, degraded, reason


def aggregate_parent_candidates(
    line_hits: Sequence[LineSearchHit],
    matrix: DemandMatrix,
    *,
    trade_code: str,
    service_code: str | None = None,
) -> list[PackCandidate]:
    required_ids = {
        item.request_item_id
        for item in matrix.items
        if item.status is DemandStatus.REQUIRED
    }
    votes: dict[str, dict[str, LineSearchHit]] = defaultdict(dict)
    for hit in line_hits:
        if hit.request_item_id not in required_ids:
            continue
        current = votes[hit.pack_id].get(hit.request_item_id)
        if current is None or hit.rrf_score > current.rrf_score:
            votes[hit.pack_id][hit.request_item_id] = hit

    denominator = max(1, len(required_ids))
    candidates: list[PackCandidate] = []
    for pack_id, item_votes in votes.items():
        best_hits = list(item_votes.values())
        candidate = PackCandidate(
            pack_id=pack_id,
            pack_version=max(hit.pack_version for hit in best_hits),
            trade_code=trade_code,
            service_code=service_code,
            matched_line_ids=sorted({hit.line_id for hit in best_hits}),
            matched_request_item_ids=sorted(item_votes),
            line_parent_score=sum(hit.rrf_score for hit in best_hits) / denominator,
            direct_pack_score=0.0,
            lexical_score=max((hit.lexical_score for hit in best_hits), default=0.0),
            dense_score=max((hit.dense_score for hit in best_hits), default=0.0),
            rrf_score=sum(hit.rrf_score for hit in best_hits) / denominator,
            rerank_score=0.0,
            coverage_score=len(item_votes) / denominator,
            extra_scope_penalty=0.0,
            final_score=0.0,
        )
        candidates.append(
            candidate.model_copy(update={"final_score": compute_final_score(candidate)})
        )
    return candidates


def union_candidates(
    parent_candidates: Iterable[PackCandidate],
    direct_candidates: Iterable[PackCandidate],
) -> list[PackCandidate]:
    merged: dict[str, PackCandidate] = {}
    for candidate in (*tuple(parent_candidates), *tuple(direct_candidates)):
        previous = merged.get(candidate.pack_id)
        if previous is None:
            merged[candidate.pack_id] = candidate
            continue
        update = {
            "pack_version": max(previous.pack_version, candidate.pack_version),
            "trade_code": previous.trade_code or candidate.trade_code,
            "service_code": previous.service_code or candidate.service_code,
            "matched_line_ids": sorted(
                set(previous.matched_line_ids) | set(candidate.matched_line_ids)
            ),
            "matched_request_item_ids": sorted(
                set(previous.matched_request_item_ids)
                | set(candidate.matched_request_item_ids)
            ),
            "line_parent_score": max(
                previous.line_parent_score, candidate.line_parent_score
            ),
            "direct_pack_score": max(
                previous.direct_pack_score, candidate.direct_pack_score
            ),
            "lexical_score": max(previous.lexical_score, candidate.lexical_score),
            "dense_score": max(previous.dense_score, candidate.dense_score),
            "rrf_score": max(previous.rrf_score, candidate.rrf_score),
            "coverage_score": max(previous.coverage_score, candidate.coverage_score),
            "pack_code": previous.pack_code or candidate.pack_code,
            "fallback_rank": (
                previous.fallback_rank
                if previous.fallback_rank is not None
                else candidate.fallback_rank
            ),
        }
        combined = previous.model_copy(update=update)
        merged[candidate.pack_id] = combined.model_copy(
            update={"final_score": compute_final_score(combined)}
        )
    return sorted(
        merged.values(),
        key=lambda candidate: (-candidate.final_score, candidate.pack_id),
    )[: SEARCH.parent_pack_top_k]


__all__ = [
    "CatalogLine",
    "CatalogPack",
    "CatalogRepository",
    "EmbeddingService",
    "EmbeddingUnavailableError",
    "HierarchicalSearch",
    "aggregate_parent_candidates",
    "compute_final_score",
    "reciprocal_rank",
    "union_candidates",
]
