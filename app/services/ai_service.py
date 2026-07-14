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
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Final, TypedDict

from openai import APIError, AsyncOpenAI
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.chat_responses import (
    build_chatbot_provider_fallback_response,
    build_chatbot_static_response,
    build_landing_chatbot_provider_fallback_response,
    build_mobile_chatbot_provider_fallback_response,
)
from app.core.prompts import (
    LANDING_CHATBOT_SYSTEM_PROMPT,
    MOBILE_CHATBOT_SYSTEM_PROMPT,
    SYSTEM_PROMPT_GENERATOR,
    build_chatbot_system_prompt,
)
from app.core.chat_intent import classify_chat_intent
from app.schemas.chat import ChatMessage
from app.core.utils import JSONHealingError, clean_and_parse_json
from app.core.btp_validator import validate_btp_context

# ---------------------------------------------------------------------------
# OpenAI Structured Outputs — JSON Schema for devis generation
# ---------------------------------------------------------------------------
# This schema mirrors EXACTLY the JSON format the AI already returns.
# By passing it via response_format, OpenAI enforces valid JSON at the
# decoding layer — the model no longer wastes reasoning tokens on
# formatting, and JSONHealingError becomes virtually impossible.
# ---------------------------------------------------------------------------
_DEVIS_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "devis_generation",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "client_type": {
                    "type": "string",
                    "enum": ["pro", "particulier"],
                },
                "project_nature": {
                    "type": "string",
                    "enum": ["neuf", "renovation"],
                },
                "lots": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "lot_key": {"type": "string"},
                            "metier": {"type": "string"},
                            "zone": {
                                "type": "string",
                                "enum": ["interieur", "exterieur"],
                            },
                            "packs": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "type": {
                                            "type": "string",
                                            "enum": [
                                                "PRESTATION",
                                                "DEPANNAGE",
                                            ],
                                        },
                                        "quantite": {"type": "number"},
                                    },
                                    "required": ["id", "type", "quantite"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["lot_key", "metier", "zone", "packs"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["client_type", "project_nature", "lots"],
            "additionalProperties": False,
        },
    },
}

from app.services.prestations_engine import (
    process_ai_lots,
    calculate_global_totals,
    get_cached_price_map,
    get_cached_packs_map,
    extract_surface_m2,
)
from app.schemas.devis import DevisResponse
from app.services.catalog_service import (
    build_reference_trade_line_items,
    build_trade_line_items,
)
from app.services.devis_repair import UnrepairableDevisError
from app.core.metier_rules import ALL_METIER_RULES

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
class TokenUsage(TypedDict):
    """Token counts returned alongside every chat response."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


ZERO_USAGE: TokenUsage = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
}


def _extract_usage(response: Any) -> TokenUsage:
    """Pull token counts from an OpenAI chat completion response."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return dict(ZERO_USAGE)  # type: ignore[return-value]
    return TokenUsage(
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
    )


class StreamEvent(TypedDict, total=False):
    """A single event yielded by :meth:`AIService.generate_quote_stream`.

    ``type`` is always present; the other keys depend on the event:

    * ``type="progress"``  -> ``step``, ``total``, ``label``
    * ``type="result"``    -> ``data`` (the parsed devis dict)
    * ``type="title"``     -> ``title`` (short generated devis title)
    * ``type="error"``     -> ``status`` (HTTP-style hint), ``detail``
    """

    type: str
    step: int
    total: int
    label: str
    data: dict[str, Any]
    title: str
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
        "max_completion_tokens": 8192,
        "temperature": 1,
        "top_p": 1,
        "presence_penalty": 0,
        "stream": False,
    }
    _CHAT_COMPLETION_PARAMS: Final[dict[str, Any]] = {
        "max_completion_tokens": 4096,
        "temperature": 1,
        "top_p": 1,
        "presence_penalty": 0,
        "stream": False,
    }
    _DEVIS_TITLE_MODEL: Final[str] = "gpt-4"
    _DEVIS_TITLE_PARAMS: Final[dict[str, Any]] = {
        "max_tokens": 60,
        "temperature": 0.2,
        "top_p": 1,
        "presence_penalty": 0,
        "stream": False,
    }
    _CHAT_HISTORY_MAX_MESSAGES: Final[int] = 6
    _CHAT_HISTORY_MESSAGE_MAX_CHARS: Final[int] = 700

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
        self._chatbot_model: str = settings.OPENAI_CHATBOT_MODEL
        self._mobile_model: str = settings.OPENAI_MOBILE_MODEL
        self._landing_model: str = settings.OPENAI_LANDING_MODEL
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
    async def _chat(
        self,
        system_prompt: str,
        user_text: str,
        *,
        completion_params: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """Single chat completion call with the mandated parameters.

        Parameters
        ----------
        response_format
            Optional OpenAI ``response_format`` dict.  When set to a
            ``{"type": "json_schema", ...}`` payload, the API enforces
            valid JSON output at the decoding layer — no post-hoc
            healing needed.
        """
        params = dict(completion_params or self._COMPLETION_PARAMS)
        if response_format is not None:
            params["response_format"] = response_format
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                **params,
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

    def _compact_chat_history(
        self,
        history: list[ChatMessage] | None,
    ) -> list[dict[str, str]]:
        """Keep recent chat context small enough for fast, grounded replies."""
        if not history:
            return []

        compacted: list[dict[str, str]] = []
        for msg in history[-self._CHAT_HISTORY_MAX_MESSAGES:]:
            content = msg.content.strip()
            if not content:
                continue
            compacted.append(
                {
                    "role": msg.role,
                    "content": _short(content, self._CHAT_HISTORY_MESSAGE_MAX_CHARS),
                }
            )
        return compacted

    # ------------------------------------------------------------------
    # (Obsolete _detect_trades and _generate_devis methods removed for V2)

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

        Powers ``POST /api/v1/trade-line/generate``. Fast pipeline:

        1. Fuzzy-load response-ready catalog rows for ``job_corp`` from
           ``trades`` / ``trade_services`` and return them directly.
        2. If the catalog has no match, return deterministic BTP reference
           items for known trades.
        3. If neither source can classify the input as BTP, raise
           :class:`InvalidBuildingRequestError` so the router returns 400.
        """
        job_corp = job_corp.strip()
        if not job_corp:
            raise ValueError("`job_corp` must not be empty.")
        if limit <= 0:
            raise ValueError("`limit` must be a positive integer.")

        catalog_items = await build_trade_line_items(
            db, job_corp=job_corp, limit=limit
        )
        if catalog_items:
            logger.info(
                "Trade-line fast path used for job_corp=%r (%d items).",
                job_corp,
                len(catalog_items),
            )
            return {
                "job_corp": job_corp,
                "count": len(catalog_items),
                "items": catalog_items,
            }

        reference_items = build_reference_trade_line_items(job_corp, limit=limit)
        if reference_items:
            logger.info(
                "Trade-line reference fallback used for job_corp=%r (%d items).",
                job_corp,
                len(reference_items),
            )
            return {
                "job_corp": job_corp,
                "count": len(reference_items),
                "items": reference_items,
            }

        raise InvalidBuildingRequestError(
            f"{job_corp!r} is not a recognised building trade."
        )

    async def generate_chat_response(
        self,
        user_text: str,
        history: list[ChatMessage] | None = None,
    ) -> tuple[str, TokenUsage]:
        """Run the chatbot pipeline and return ``(text, token_usage)``.

        The system prompt is assembled dynamically:

        * The core persona (``CHATBOT_SYSTEM_BASE``) is **always** included.
        * UX module guides are injected **only** when the user's question
          is about app navigation (detected by keyword classification).
        * Previous conversation turns are prepended so the model has
          multi-turn context.
        """
        user_text = user_text.strip()
        if not user_text:
            raise ValueError("`user_text` must not be empty.")

        compact_history = self._compact_chat_history(history)

        # --- 1. Classify intent (zero-cost keyword scan) ---
        relevant_modules = classify_chat_intent(user_text)
        if relevant_modules == {"assistant"} and compact_history:
            recent_user_text = " ".join(
                msg["content"] for msg in compact_history if msg["role"] == "user"
            )
            history_modules = classify_chat_intent(recent_user_text)
            specific_history_modules = history_modules - {"assistant"}
            if specific_history_modules:
                relevant_modules = relevant_modules | specific_history_modules

        static_response = build_chatbot_static_response(relevant_modules)
        if static_response:
            logger.debug(
                "Chat static response used for UX modules=%s",
                relevant_modules,
            )
            return static_response, dict(ZERO_USAGE)  # type: ignore[return-value]

        system_prompt = build_chatbot_system_prompt(relevant_modules or None)

        logger.debug(
            "Chat intent: UX modules=%s, prompt size=%d chars, history=%d messages",
            relevant_modules or "(none — BTP domain)",
            len(system_prompt),
            len(compact_history),
        )

        # --- 2. Assemble messages with history ---
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]
        messages.extend(compact_history)
        messages.append({"role": "user", "content": user_text})

        # --- 3. Call the model ---
        try:
            response = await self._client.chat.completions.create(
                model=self._chatbot_model,
                messages=messages,
                **self._CHAT_COMPLETION_PARAMS,
            )
        except APIError as exc:
            logger.exception("OpenAI chat call failed; returning local fallback.")
            return build_chatbot_provider_fallback_response(user_text), dict(ZERO_USAGE)  # type: ignore[return-value]

        if not response.choices:
            logger.warning("OpenAI returned no choices for chat; returning local fallback.")
            return build_chatbot_provider_fallback_response(user_text), _extract_usage(response)

        content = response.choices[0].message.content
        if not content:
            logger.warning("OpenAI returned empty chat completion; returning local fallback.")
            return build_chatbot_provider_fallback_response(user_text), _extract_usage(response)
        return content, _extract_usage(response)

    async def generate_landing_chat_response(
        self,
        user_text: str,
        history: list[ChatMessage] | None = None,
    ) -> tuple[str, TokenUsage]:
        """Return ``(text, token_usage)`` for the landing-page chatbot.

        This bot is intentionally narrower than the in-app assistant: it can
        explain the product and help visitors choose between the public offers.
        """
        user_text = user_text.strip()
        if not user_text:
            raise ValueError("`user_text` must not be empty.")

        compact_history = self._compact_chat_history(history)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": LANDING_CHATBOT_SYSTEM_PROMPT},
        ]
        messages.extend(compact_history)
        messages.append({"role": "user", "content": user_text})

        try:
            response = await self._client.chat.completions.create(
                model=self._landing_model,
                messages=messages,
                **self._CHAT_COMPLETION_PARAMS,
            )
        except APIError:
            logger.exception("OpenAI landing chat call failed; returning local fallback.")
            return build_landing_chatbot_provider_fallback_response(user_text), dict(ZERO_USAGE)  # type: ignore[return-value]

        if not response.choices:
            logger.warning(
                "OpenAI returned no choices for landing chat; returning local fallback."
            )
            return build_landing_chatbot_provider_fallback_response(user_text), _extract_usage(response)

        content = response.choices[0].message.content
        if not content:
            logger.warning(
                "OpenAI returned empty landing chat completion; returning local fallback."
            )
            return build_landing_chatbot_provider_fallback_response(user_text), _extract_usage(response)
        return content, _extract_usage(response)

    async def generate_mobile_chat_response(
        self,
        user_text: str,
        history: list[ChatMessage] | None = None,
    ) -> tuple[str, TokenUsage]:
        """Return ``(text, token_usage)`` for the mobile-app chatbot."""
        user_text = user_text.strip()
        if not user_text:
            raise ValueError("`user_text` must not be empty.")

        compact_history = self._compact_chat_history(history)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": MOBILE_CHATBOT_SYSTEM_PROMPT},
        ]
        messages.extend(compact_history)
        messages.append({"role": "user", "content": user_text})

        try:
            response = await self._client.chat.completions.create(
                model=self._mobile_model,
                messages=messages,
                **self._CHAT_COMPLETION_PARAMS,
            )
        except APIError:
            logger.exception("OpenAI mobile chat call failed; returning local fallback.")
            return build_mobile_chatbot_provider_fallback_response(user_text), dict(ZERO_USAGE)  # type: ignore[return-value]

        if not response.choices:
            logger.warning(
                "OpenAI returned no choices for mobile chat; returning local fallback."
            )
            return build_mobile_chatbot_provider_fallback_response(user_text), _extract_usage(response)

        content = response.choices[0].message.content
        if not content:
            logger.warning(
                "OpenAI returned empty mobile chat completion; returning local fallback."
            )
            return build_mobile_chatbot_provider_fallback_response(user_text), _extract_usage(response)
        return content, _extract_usage(response)

    def _normalise_devis_title(self, title: str | None) -> str:
        """Force a short title beginning exactly with ``Travaux de``."""
        cleaned = (title or "").strip().strip("\"'`“”‘’")
        cleaned = " ".join(cleaned.split())
        if not cleaned:
            cleaned = "rénovation"

        prefix = "Travaux de "
        lowered = cleaned.lower()
        if lowered.startswith(prefix.lower()):
            body = cleaned[len(prefix) :].strip()
        elif lowered.startswith("travaux d'"):
            body = cleaned[len("travaux d'") :].strip()
        elif lowered.startswith("travaux de"):
            body = cleaned[len("travaux de") :].strip()
        elif lowered.startswith("travaux "):
            body = cleaned[len("travaux ") :].strip()
        else:
            body = cleaned

        body = body.strip(" .,:;-/")
        body = re.sub(
            r"\b\d+(?:[.,]\d+)?\s*(?:m2|m²|m3|m³|ml|cm|mm|m|kg|l|u|unités?|pieces?|pièces?)\b",
            "",
            body,
            flags=re.IGNORECASE,
        )
        body = re.sub(r"\b\d+(?:[.,]\d+)?\b", "", body)
        body = re.sub(r"\s+", " ", body).strip(" .,:;-/")
        if not body:
            body = "rénovation"
        return f"{prefix}{body[:80]}".rstrip(" .,:;-/")

    async def generate_devis_title(self, user_text: str) -> str:
        """Generate a short French devis title in parallel with the devis."""
        fallback = self._normalise_devis_title("rénovation")
        try:
            response = await self._client.chat.completions.create(
                model=self._DEVIS_TITLE_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Tu génères uniquement un titre court en français "
                            "pour un devis BTP. Le titre doit commencer "
                            "exactement par \"Travaux de \". Retourne uniquement "
                            "le titre, sans guillemets, sans explication, sans "
                            "ponctuation finale. Fais un titre simple et générique: "
                            "garde seulement le type de travaux et la pièce ou le "
                            "métier principal. Supprime les surfaces, dimensions, "
                            "quantités, nombres, matériaux trop précis, gammes et "
                            "détails techniques. Exemple: \"Rénovation cuisine 20m2\" "
                            "devient \"Travaux de rénovation cuisine\"."
                        ),
                    },
                    {"role": "user", "content": user_text},
                ],
                **self._DEVIS_TITLE_PARAMS,
            )
        except Exception:
            logger.exception("OpenAI title generation failed; using fallback title.")
            return fallback

        if not response.choices:
            logger.warning("OpenAI returned no choices for devis title.")
            return fallback

        content = response.choices[0].message.content
        return self._normalise_devis_title(content)

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
        title_task = asyncio.create_task(self.generate_devis_title(user_text))

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
                title = await title_task
                await queue.put(StreamEvent(type="title", title=title))
            finally:
                if not title_task.done():
                    title_task.cancel()
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

        # Step 1: BTP Guardrail
        if on_progress is not None:
            await on_progress(1, PROGRESS_STEPS[0])
        
        validate_btp_context(user_text)
        
        # Load price maps and packs (cached in RAM after first call)
        price_map, concept_map = await get_cached_price_map(db)
        exact_map, pack_list = await get_cached_packs_map(db)
        
        # Step 2: Generation (Semantic Mapping)
        if on_progress is not None:
            await on_progress(2, PROGRESS_STEPS[1])
            
        catalog_by_metier = {}
        for p in pack_list:
            cm = p["corps_metier"]
            if cm not in catalog_by_metier:
                catalog_by_metier[cm] = []
            catalog_by_metier[cm].append(f"[{p['code_pack']}] {p['nom_pack']}")
            
        catalog_lines = []
        for cm, packs in catalog_by_metier.items():
            catalog_lines.append(f"- Métier: {cm}")
            for p in packs:
                catalog_lines.append(f"  {p}")
        catalog_str = "\n".join(catalog_lines)
        
        prompt = SYSTEM_PROMPT_GENERATOR.replace("{catalog}", catalog_str)
            
        raw = await self._chat(
            prompt,
            user_text,
            response_format=_DEVIS_RESPONSE_FORMAT,
        )
        # Structured Outputs guarantees valid JSON, but we keep the
        # healer as a safety net — it's a no-op when input is clean.
        parsed = clean_and_parse_json(raw)
        
        # Step 3: Calculation (Deterministic engine)
        if on_progress is not None:
            await on_progress(3, PROGRESS_STEPS[2])
            
        lots = parsed.get("lots", [])
        logger.info("AI returned %d lots: %s", len(lots), [l.get("metier", "?") for l in lots])
        client_type = parsed.get("client_type", "particulier")
        project_nature = parsed.get("project_nature", "renovation")
        
        surface_m2 = extract_surface_m2(user_text)
        
        four_blocks = process_ai_lots(
            lots, 
            client_type, 
            project_nature, 
            surface_m2=surface_m2,
            user_text=user_text,
            price_map=price_map, 
            concept_map=concept_map, 
            packs_maps=(exact_map, pack_list)
        )
        
        from datetime import datetime, timedelta, timezone

        # Flat lines for global totals
        flat_lines = []
        for b in four_blocks:
            for lot in b.get("lots", []):
                flat_lines.extend(lot.get("lignes", []))
                
        totals = calculate_global_totals(flat_lines)
        
        now = datetime.now(timezone.utc)

        devis = {
            "date": now.isoformat(),
            "validite": (now + timedelta(days=30)).isoformat(),
            "duree": 30,
            "montant_ttc": totals["total_ttc"],
            "blocs": four_blocks,
        }

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
    "TokenUsage",
    "UnrepairableDevisError",
    "ZERO_USAGE",
    "ai_service",
]
