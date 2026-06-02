"""Two-stage devis generation pipeline backed by OpenAI's Chat Completions API.

The service drives the OpenAI chat completions endpoint. The public entry
point is :meth:`AIService.generate_quote`, which runs:

* **Stage 1 - Routing.** The ``TRADE_DETECTION_PROMPT`` classifies the user's
  free-form text and returns a small JSON object describing whether the
  request is building-related and which trades it involves. The list of
  trades the model is allowed to pick from is pulled live from the
  ``trades`` table (via :func:`catalog_service.load_trade_names`).
* **Stage 2 - Generation.** The ``PRESTATION_ANALYSIS_PROMPT`` is rendered
  with a bulleted ``BIBLIOTHÈQUE DISPONIBLE`` catalog built from the
  ``trade_services`` rows that belong to the trades detected in Stage 1,
  and with the original user text, producing the full devis JSON that
  matches the ``DevisResponse`` Pydantic schema.

Both stages use the exact API parameters mandated by the product spec.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Final, TypedDict

from openai import APIError, AsyncOpenAI
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.prompts import (
    PRESTATION_ANALYSIS_PROMPT,
    PRESTATION_ANALYSIS_RETRY_SUFFIX,
    TRADE_DETECTION_PROMPT,
    TRADE_LINE_PROMPT,
    CHATBOT_PROMPT,
)
from app.core.utils import JSONHealingError, clean_and_parse_json
from app.schemas.devis import DevisResponse
from app.services.catalog_service import (
    build_rag_context,
    build_trade_line_context,
    load_trade_names,
)
from app.services.devis_repair import UnrepairableDevisError, repair_devis_payload
from app.services.upsell_engine import apply_upsell_rules

logger = logging.getLogger(__name__)


class InvalidBuildingRequestError(ValueError):
    """Raised when Stage 1 classifies the request as out-of-scope."""


class AIServiceError(RuntimeError):
    """Raised when the AI call itself fails (network, quota, 5xx, ...)."""


# ---------------------------------------------------------------------------
# Tiny helpers used by the retry loop.
# ---------------------------------------------------------------------------
_RETRY_ERROR_MAX_LEN: Final[int] = 400

# Default values used when the Stage 1 routing payload is missing the
# new structure fields (older model behaviour, defensive coding).
_DEFAULT_REQUEST_TYPE: Final[str] = "travaux"
_VALID_REQUEST_TYPES: Final[frozenset[str]] = frozenset({"travaux", "depannage"})


def _short(s: str, limit: int = 200) -> str:
    """Return ``s`` truncated to ``limit`` chars with an ellipsis."""
    return s if len(s) <= limit else s[:limit] + "..."


def _normalise_request_type(value: Any) -> str:
    """Coerce the LLM's ``requestType`` to ``"travaux"`` or ``"depannage"``.

    Accepts common spellings (``"dépannage"``, ``"DEPANNAGE"``, ``"repair"``,
    ``"breakdown"``…) and falls back to ``"travaux"`` for anything else.
    """
    if not isinstance(value, str):
        return _DEFAULT_REQUEST_TYPE
    normalised = (
        value.strip().lower().replace("é", "e").replace("è", "e").replace(" ", "")
    )
    if normalised in {"depannage", "depan", "repair", "breakdown", "urgence"}:
        return "depannage"
    if normalised in _VALID_REQUEST_TYPES:
        return normalised
    return _DEFAULT_REQUEST_TYPE


def _normalise_trade_line_payload(
    parsed: Any,
    *,
    job_corp: str,
    limit: int,
) -> dict[str, Any]:
    """Coerce the LLM's raw payload into ``{job_corp, count, items}``.

    The model sometimes returns a bare list instead of the wrapper, or
    forgets to echo ``job_corp`` consistently. We accept both shapes and
    overwrite ``job_corp`` on every item so the response is predictable.
    Items beyond ``limit`` are trimmed.
    """
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        items = parsed.get("items")
        if items is None:
            # Single-item legacy shape — wrap it so the response is uniform.
            if {"description", "unit", "pu", "tva"}.issubset(parsed.keys()):
                items = [parsed]
            else:
                items = []
    else:
        items = []

    if not isinstance(items, list):
        items = []

    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item["job_corp"] = job_corp
        cleaned.append(item)
        if len(cleaned) >= limit:
            break

    return {"job_corp": job_corp, "count": len(cleaned), "items": cleaned}


def _format_interventions_block(
    interventions: list[str], user_text: str
) -> str:
    """Render the ``{interventions_block}`` placeholder for the Stage 2 prompt.

    Falls back to a single intervention extracted from ``user_text`` when
    Stage 1 produced an empty list, so the prompt is never structurally
    incomplete.
    """
    cleaned = [s.strip() for s in interventions if isinstance(s, str) and s.strip()]
    if not cleaned:
        cleaned = [_short(user_text, 80) or "Intervention principale"]
    return "\n".join(f"  {idx}. {label}" for idx, label in enumerate(cleaned, 1))


# ---------------------------------------------------------------------------
# Streaming - progress event vocabulary
# ---------------------------------------------------------------------------
class StreamEvent(TypedDict, total=False):
    """A single event yielded by :meth:`AIService.generate_quote_stream`.

    ``type`` is always present; the other keys depend on the event:

    * ``type="progress"``  -> ``step``, ``total``, ``label``
    * ``type="result"``    -> ``data`` (the parsed devis dict)
    * ``type="error"``     -> ``status`` (HTTP-style hint), ``detail``
    """

    type: str
    step: int
    total: int
    label: str
    data: dict[str, Any]
    status: int
    detail: str


# Public, ordered list of UI-visible progress labels for the generate flow.
# The frontend can rely on the ``step`` index being stable across versions
# even if the wording of a label evolves.
PROGRESS_STEPS: Final[tuple[str, ...]] = (
    "Analyse",
    "Generate",
    "Calculate",
    "Finalise",
)


ProgressCallback = Callable[[int, str], Awaitable[None]]


def _format_retry_error(exc: Exception) -> str:
    """Make the previous-attempt error compact + safe to embed in the prompt.

    Pydantic's ``ValidationError`` repr is huge; trim it. We also escape
    any backticks that could collide with the model's own markdown.
    """
    if isinstance(exc, ValidationError):
        first_errors = exc.errors()[:3]
        rendered = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in first_errors
        )
        msg = f"DevisResponse validation failed: {rendered}"
    else:
        msg = f"{type(exc).__name__}: {exc}"
    return _short(msg.replace("`", "'"), _RETRY_ERROR_MAX_LEN)


class AIService:
    """High-level client for the OpenAI-backed devis generation pipeline."""

    # Lifted out of the call site so tests can monkey-patch a single dict.
    #
    # NOTE on ``max_tokens``: 8192 gives enough headroom for the detailed
    # devis JSON output (15-20 lines with descriptions, prices, TVA) plus
    # the structured prompt context.
    _COMPLETION_PARAMS: Final[dict[str, Any]] = {
        "max_tokens": 8192,
        "temperature": 1,
        "top_p": 1,
        "presence_penalty": 0,
        "stream": False,
    }

    # How many Stage-2 attempts before giving up. 1 initial + (N-1) retries.
    # Each retry passes the previous error back to the model so it can
    # self-correct.
    _STAGE2_MAX_ATTEMPTS: Final[int] = 2

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._model: str = model or settings.OPENAI_MODEL
        self._client: AsyncOpenAI = AsyncOpenAI(
            api_key=api_key or settings.OPENAI_API_KEY,
            base_url=base_url or "https://api.openai.com/v1",
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def aclose(self) -> None:
        """Release the underlying httpx client. Call on app shutdown."""
        await self._client.close()


    # ------------------------------------------------------------------
    # Low-level call
    # ------------------------------------------------------------------
    async def _chat(self, system_prompt: str, user_text: str) -> str:
        """Single chat completion call with the mandated parameters."""
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                **self._COMPLETION_PARAMS,
            )
        except APIError as exc:
            logger.exception("OpenAI API call failed.")
            raise AIServiceError(f"OpenAI API error: {exc}") from exc

        if not response.choices:
            raise AIServiceError("OpenAI returned no choices.")

        content = response.choices[0].message.content
        if not content:
            raise AIServiceError("OpenAI returned an empty completion.")
        return content

    # ------------------------------------------------------------------
    # Stage 1 - Routing / trade detection
    # ------------------------------------------------------------------
    async def _detect_trades(
        self,
        user_text: str,
        available_trades: list[str],
    ) -> dict[str, Any]:
        """Classify the user's request and extract the trades involved."""
        trades_list = ", ".join(available_trades) if available_trades else ""
        prompt = TRADE_DETECTION_PROMPT.replace("{trades_list}", trades_list)
        raw = await self._chat(prompt, user_text)
        return clean_and_parse_json(raw)

    # ------------------------------------------------------------------
    # Stage 2 - Devis generation
    # ------------------------------------------------------------------
    async def _generate_devis(
        self,
        user_text: str,
        rag_context: str,
        db: AsyncSession,
        *,
        request_type: str,
        interventions: list[str],
    ) -> dict[str, Any]:
        """Produce the full devis JSON from the user's request + RAG context.

        Reliability strategy (in order, on every attempt):

        1. Call the LLM.
        2. Run the multi-stage parser (``clean_and_parse_json``): strict ->
           hand-rolled healer -> ``json_repair`` fallback.
        3. Run the domain repair (``repair_devis_payload``): rebuild missing
           ``ht`` / ``ttc``, drop unsalvageable lines, recompute
           ``montant_ttc``.
        4. Run the upsell engine (``apply_upsell_rules``): inject required
           complements (toiture/évacuation, carrelage/ragréage), auto-fill
           any line whose ``pu`` is 0 from the catalog, then recompute
           ``montant_ttc`` once more if anything changed.
        5. Validate against ``DevisResponse``. If validation fails we
           retry with the error fed back to the model so it can fix it.

        Up to :attr:`_STAGE2_MAX_ATTEMPTS` calls are made. The last error
        is re-raised when every attempt has been exhausted.
        """
        base_prompt = (
            PRESTATION_ANALYSIS_PROMPT
            .replace("{database_rag_context}", rag_context)
            .replace("{request_type}", request_type)
            .replace(
                "{interventions_block}",
                _format_interventions_block(interventions, user_text),
            )
        )

        last_exc: Exception | None = None
        for attempt in range(1, self._STAGE2_MAX_ATTEMPTS + 1):
            prompt = base_prompt
            if attempt > 1 and last_exc is not None:
                prompt = base_prompt + PRESTATION_ANALYSIS_RETRY_SUFFIX.replace(
                    "{error}", _format_retry_error(last_exc)
                )

            try:
                raw = await self._chat(prompt, user_text)
                parsed = clean_and_parse_json(raw)
                repaired = repair_devis_payload(parsed)
                repaired = await apply_upsell_rules(repaired, db)
                # Pydantic validation lives in the loop so a bad shape
                # also triggers a retry, not just unparseable JSON.
                DevisResponse.model_validate(repaired)
                if attempt > 1:
                    logger.info("Stage 2 succeeded on attempt %d.", attempt)
                return repaired
            except (
                AIServiceError,
                JSONHealingError,
                UnrepairableDevisError,
                ValidationError,
            ) as exc:
                last_exc = exc
                logger.warning(
                    "Stage 2 attempt %d/%d failed (%s): %s",
                    attempt,
                    self._STAGE2_MAX_ATTEMPTS,
                    type(exc).__name__,
                    _short(str(exc)),
                )

        # All attempts exhausted - re-raise the last error.
        assert last_exc is not None  # for the type checker
        raise last_exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def generate_quote(
        self,
        user_text: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Run the full two-stage pipeline and return the parsed devis ``dict``.

        Non-streaming entry point. Use :meth:`generate_quote_stream` to
        observe progress as the pipeline runs.

        Raises
        ------
        InvalidBuildingRequestError
            When Stage 1 decides the user's request is not a building request.
        AIServiceError
            On any transport / provider-side failure.
        JSONHealingError
            When the AI JSON cannot be parsed even after auto-healing.
        """
        return await self._run_pipeline(user_text, db, on_progress=None)

    async def generate_trade_line(
        self,
        job_corp: str,
        db: AsyncSession,
        *,
        limit: int,
    ) -> dict[str, Any]:
        """Return a LIST of representative billable prestations for a corps de métier.

        Powers ``POST /api/v1/trade-line/generate``. Single-shot pipeline:

        1. Fuzzy-load the catalog rows for ``job_corp`` from ``trades`` /
           ``trade_services`` via :func:`build_trade_line_context` (cap
           bumped to ``limit * 2`` so the AI can pick + complement).
        2. Render :data:`TRADE_LINE_PROMPT` with ``job_corp`` + RAG +
           target ``limit`` and hand it to the model.
        3. Parse / heal the JSON. Raise
           :class:`InvalidBuildingRequestError` if Stage 1 rejection
           triggered. Normalise the dict to ``{job_corp, items}`` so
           the router can validate against :class:`TradeLineResponse`.
        """
        job_corp = job_corp.strip()
        if not job_corp:
            raise ValueError("`job_corp` must not be empty.")
        if limit <= 0:
            raise ValueError("`limit` must be a positive integer.")

        # Pull a slightly wider catalog so the AI has enough breadth to
        # produce ``limit`` distinct items without running out of options.
        rag_context = await build_trade_line_context(
            db, job_corp=job_corp, limit=max(limit * 2, 20)
        )

        prompt = (
            TRADE_LINE_PROMPT
            .replace("{job_corp}", job_corp)
            .replace("{database_rag_context}", rag_context)
            .replace("{limit}", str(limit))
        )

        raw = await self._chat(prompt, job_corp)
        parsed = clean_and_parse_json(raw)

        if parsed.get("isValidBuildingRequest") is False:
            raise InvalidBuildingRequestError(
                parsed.get("analysis")
                or f"{job_corp!r} is not a recognised building trade."
            )

        return _normalise_trade_line_payload(parsed, job_corp=job_corp, limit=limit)

    async def generate_chat_response(
        self,
        user_text: str,
    ) -> str:
        """Run the chatbot pipeline and return the generated text response.

        Uses the configured OpenAI model and the `CHATBOT_PROMPT` system context.
        """
        user_text = user_text.strip()
        if not user_text:
            raise ValueError("`user_text` must not be empty.")

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": CHATBOT_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                max_tokens=8192,
                temperature=0.7,
                top_p=1,
                presence_penalty=0,
                stream=False,
            )
        except APIError as exc:
            logger.exception("OpenAI chat call failed.")
            raise AIServiceError(f"OpenAI API error: {exc}") from exc

        if not response.choices:
            raise AIServiceError("OpenAI returned no choices.")

        content = response.choices[0].message.content
        if not content:
            raise AIServiceError("OpenAI returned an empty completion.")
        return content

    async def generate_quote_stream(
        self,
        user_text: str,
        db: AsyncSession,
    ) -> AsyncIterator[StreamEvent]:
        """Run the pipeline and yield UI-friendly events as they happen.

        The generator yields exactly :data:`PROGRESS_STEPS` ``progress`` events
        in order, then a single terminal event:

        * ``{"type": "result", "data": <devis>}`` on success;
        * ``{"type": "error",  "status": <int>, "detail": <str>}`` otherwise.

        Cancelling the consumer (e.g. when the HTTP client disconnects)
        cancels the background pipeline task cleanly.
        """
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()

        async def _on_progress(step: int, label: str) -> None:
            await queue.put(
                StreamEvent(
                    type="progress",
                    step=step,
                    total=len(PROGRESS_STEPS),
                    label=label,
                )
            )

        async def _runner() -> None:
            try:
                devis = await self._run_pipeline(
                    user_text, db, on_progress=_on_progress
                )
            except InvalidBuildingRequestError as exc:
                await queue.put(StreamEvent(type="error", status=400, detail=str(exc)))
            except AIServiceError as exc:
                await queue.put(StreamEvent(type="error", status=503, detail=str(exc)))
            except (JSONHealingError, UnrepairableDevisError) as exc:
                await queue.put(StreamEvent(type="error", status=502, detail=str(exc)))
            except ValidationError as exc:
                await queue.put(
                    StreamEvent(
                        type="error",
                        status=502,
                        detail=f"DevisResponse validation failed: {exc.errors()[:3]}",
                    )
                )
            except Exception as exc:  # pragma: no cover - last-resort safety net
                logger.exception("Unexpected error in streaming pipeline.")
                await queue.put(StreamEvent(type="error", status=500, detail=str(exc)))
            else:
                await queue.put(StreamEvent(type="result", data=devis))
            finally:
                await queue.put(None)  # sentinel

        runner_task = asyncio.create_task(_runner())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if not runner_task.done():
                runner_task.cancel()
                try:
                    await runner_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    # ------------------------------------------------------------------
    # Internal pipeline shared by both entry points.
    # ------------------------------------------------------------------
    async def _run_pipeline(
        self,
        user_text: str,
        db: AsyncSession,
        *,
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        user_text = user_text.strip()
        if not user_text:
            raise ValueError("`user_text` must not be empty.")

        # Step 1: routing / trade classification.
        if on_progress is not None:
            await on_progress(1, PROGRESS_STEPS[0])
        available_trades = await load_trade_names(db)
        logger.debug("Loaded %d trade names from DB.", len(available_trades))

        routing = await self._detect_trades(user_text, available_trades)
        logger.info("Trade detection payload: %s", routing)
        if not routing.get("isValidBuildingRequest", False):
            raise InvalidBuildingRequestError(
                routing.get("analysis")
                or "The request was not recognised as a building-related query."
            )
        detected: list[str] = routing.get("detectedTrades") or []
        request_type: str = _normalise_request_type(routing.get("requestType"))
        raw_interventions = routing.get("interventions") or []
        interventions: list[str] = (
            list(raw_interventions) if isinstance(raw_interventions, list) else []
        )
        logger.info(
            "Routing: requestType=%s, %d interventions, %d trades.",
            request_type,
            len(interventions),
            len(detected),
        )

        # Step 2: build the trade-scoped RAG context.
        if on_progress is not None:
            await on_progress(2, PROGRESS_STEPS[1])
        rag_context = await build_rag_context(db, trade_names=detected)
        logger.debug(
            "RAG context is %d chars (%d trades scoping the retrieval).",
            len(rag_context),
            len(detected),
        )

        # Step 3: generation (the slow LLM call + parse + repair + upsell).
        if on_progress is not None:
            await on_progress(3, PROGRESS_STEPS[2])
        devis = await self._generate_devis(
            user_text,
            rag_context,
            db,
            request_type=request_type,
            interventions=interventions,
        )

        # Step 4: heartbeat right before emitting the result so the UI
        # has time to swap the spinner to the "almost done" label.
        if on_progress is not None:
            await on_progress(4, PROGRESS_STEPS[3])

        return devis


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------
ai_service: AIService = AIService()


__all__ = [
    "AIService",
    "AIServiceError",
    "InvalidBuildingRequestError",
    "PROGRESS_STEPS",
    "StreamEvent",
    "UnrepairableDevisError",
    "ai_service",
]
