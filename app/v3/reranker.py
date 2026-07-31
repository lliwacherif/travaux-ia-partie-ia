"""Complete-pack reranking with an authorized deterministic fallback."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import cohere

from app.core.config import settings
from app.v3.contracts import PackCandidate
from app.v3.search import compute_final_score
from app.v3.ssot import SEARCH


def stable_candidate_order(
    candidates: Sequence[PackCandidate],
) -> list[PackCandidate]:
    """Apply the stable tie-break order mandated by V3.1."""

    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.final_score,
            -candidate.coverage_score,
            -candidate.line_parent_score,
            candidate.extra_scope_penalty,
            -candidate.pack_version,
            candidate.fallback_rank if candidate.fallback_rank is not None else 9999,
            candidate.pack_code or candidate.pack_id,
        ),
    )


class PackReranker:
    """Cohere reranker that can never create or mutate catalog content."""

    def __init__(self, client: Any | None = None) -> None:
        api_key = settings.V3_COHERE_API_KEY
        self._client = client or (cohere.AsyncClientV2(api_key=api_key) if api_key else None)

    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[PackCandidate],
        documents_by_pack_id: Mapping[str, str],
    ) -> tuple[list[PackCandidate], bool, str | None]:
        limited = list(candidates[: SEARCH.rerank_top_k])
        if not limited:
            return [], False, None
        if self._client is None:
            return stable_candidate_order(limited), True, "COHERE_API_KEY_MISSING"

        documents = [
            documents_by_pack_id.get(candidate.pack_id, candidate.pack_code or candidate.pack_id)
            for candidate in limited
        ]
        try:
            response = await self._client.rerank(
                model=settings.V3_COHERE_RERANK_MODEL,
                query=query,
                documents=documents,
                top_n=len(documents),
            )
            scores: dict[int, float] = {
                int(result.index): max(0.0, min(1.0, float(result.relevance_score)))
                for result in response.results
            }
            reranked: list[PackCandidate] = []
            for index, candidate in enumerate(limited):
                scored = candidate.model_copy(
                    update={"rerank_score": scores.get(index, 0.0)}
                )
                reranked.append(
                    scored.model_copy(
                        update={"final_score": compute_final_score(scored)}
                    )
                )
            reranked.sort(
                key=lambda candidate: (
                    -candidate.rerank_score,
                    -candidate.final_score,
                    candidate.pack_code or candidate.pack_id,
                )
            )
            return reranked, False, None
        except Exception as exc:
            reason = f"{type(exc).__name__}:{exc}"
            return stable_candidate_order(limited), True, reason


__all__ = ["PackReranker", "stable_candidate_order"]
