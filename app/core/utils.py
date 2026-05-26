"""Defensive JSON utilities for LLM output.

``gpt-oss-120b`` (served via Scaleway) returns raw text that frequently:

* wraps the JSON payload in a ```` ```json ... ``` ```` markdown fence,
* appends leading / trailing whitespace or narration,
* gets truncated mid-object when ``max_tokens`` is hit,
* sprinkles freak syntax errors (missing colons, missing commas, mismatched
  quotes) in the middle of an otherwise-complete response.

``clean_and_parse_json`` normalises all of these into a ``dict`` so the rest
of the pipeline can assume strict, parsed JSON. The repair is layered from
the cheapest / safest strategy to the most aggressive:

1. Strict ``json.loads``.
2. Hand-rolled brace healer + trailing-comma stripper, then ``json.loads``.
3. ``json_repair.loads`` (third-party) - very permissive, handles missing
   colons / commas / quotes, mis-typed numbers, etc.

If all three fail we surface a ``JSONHealingError`` carrying enough context
(line / column / a slice of the raw string) for ops to diagnose.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from json_repair import loads as _json_repair_loads

logger = logging.getLogger(__name__)

# Cap for logged raw payloads. We never want to spam logs with full LLM
# bodies, but we need enough context to debug parse failures.
_RAW_LOG_LIMIT: int = 1500


class JSONHealingError(ValueError):
    """Raised when we can't recover a valid JSON object from the AI output."""


def clean_and_parse_json(raw_response: str) -> dict[str, Any]:
    """Strip markdown fences, multi-strategy repair, and parse.

    Parameters
    ----------
    raw_response:
        The raw ``message.content`` string returned by the model.

    Returns
    -------
    dict
        The parsed (and possibly repaired) JSON object.

    Raises
    ------
    JSONHealingError
        If every repair strategy failed.
    """
    if not raw_response:
        raise JSONHealingError("Empty AI response.")

    # ---- 1. Strip markdown code fences ---------------------------------
    cleaned = re.sub(r"```json\s*", "", raw_response)
    cleaned = re.sub(r"```", "", cleaned)
    cleaned = cleaned.strip()

    if not cleaned:
        raise JSONHealingError("AI response was empty after stripping fences.")

    # ---- 2. Strict parse -----------------------------------------------
    parsed = _try_strict(cleaned)
    if parsed is not None:
        return _ensure_dict(parsed)

    # ---- 3. Hand-rolled healer (truncation + trailing commas) ---------
    healed = cleaned
    if not healed.endswith("}"):
        original_len = len(healed)
        healed = _heal_truncated_json(healed)
        logger.warning(
            "AI JSON was truncated; healed %d chars -> %d chars.",
            original_len,
            len(healed),
        )
    healed = _strip_trailing_commas(healed)

    parsed = _try_strict(healed)
    if parsed is not None:
        return _ensure_dict(parsed)

    # ---- 4. Last resort: json_repair (very permissive) ----------------
    try:
        parsed = _json_repair_loads(healed)
    except Exception as exc:
        # json_repair is supposed to NEVER raise on malformed input, but we
        # shield ourselves anyway. If it does raise, fall through to the
        # final error path with a hint of the raw content.
        logger.error(
            "json_repair failed unexpectedly: %s | raw[:%d]=%r",
            exc, _RAW_LOG_LIMIT, raw_response[:_RAW_LOG_LIMIT],
        )
        raise JSONHealingError(f"All repair strategies failed: {exc}") from exc

    if parsed is None or parsed == "":
        logger.error(
            "json_repair returned an empty result. raw[:%d]=%r",
            _RAW_LOG_LIMIT, raw_response[:_RAW_LOG_LIMIT],
        )
        raise JSONHealingError(
            "json_repair could not extract a usable object from the AI response."
        )

    logger.warning(
        "AI JSON was salvaged via json_repair (strict + heal both failed)."
    )
    return _ensure_dict(parsed)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _try_strict(s: str) -> Any | None:
    """Strict ``json.loads``; return ``None`` on failure (no exception)."""
    try:
        return json.loads(s)
    except json.JSONDecodeError as exc:
        logger.debug(
            "Strict JSON parse failed (%s line %d col %d). Falling back to repair.",
            exc.msg, exc.lineno, exc.colno,
        )
        return None


def _ensure_dict(parsed: Any) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise JSONHealingError(
            f"Expected a JSON object at the root, got {type(parsed).__name__}."
        )
    return parsed


def _heal_truncated_json(s: str) -> str:
    """Append the missing ``}`` / ``]`` closers to a truncated JSON string.

    Uses a small string-aware state machine so braces that appear *inside*
    string literals are not counted. Openers are pushed onto a stack, their
    matching closers popped when seen; whatever is left on the stack at the
    end of the input is appended (deepest scope first) to rebalance the
    structure.

    If truncation happened in the middle of a string literal we close the
    string first before flushing the stack, which is enough to make
    ``json.loads`` accept the healed payload in the common case.
    """
    stack: list[str] = []
    in_string = False
    escape = False

    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch == "}":
            if stack and stack[-1] == "}":
                stack.pop()
        elif ch == "]":
            if stack and stack[-1] == "]":
                stack.pop()

    suffix = ""
    if in_string:
        suffix += '"'
    suffix += "".join(reversed(stack))
    return s + suffix


def _strip_trailing_commas(s: str) -> str:
    """Remove every ``,`` that sits between a value and a closing ``}``/``]``.

    Strict JSON forbids trailing commas, but truncated LLM output regularly
    looks like ``{ "ttc": 220, }`` after our brace healer rebalanced the
    structure. We strip those commas while staying string-aware, so a
    literal comma inside a description (e.g. ``"text": "foo, bar"``) is
    preserved intact.
    """
    out: list[str] = []
    in_string = False
    escape = False
    n = len(s)

    for i, ch in enumerate(s):
        if escape:
            escape = False
            out.append(ch)
            continue
        if ch == "\\":
            escape = True
            out.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string:
            out.append(ch)
            continue
        if ch == ",":
            j = i + 1
            while j < n and s[j] in " \t\n\r":
                j += 1
            if j < n and s[j] in "}]":
                continue
        out.append(ch)

    return "".join(out)


__all__ = ["JSONHealingError", "clean_and_parse_json"]
