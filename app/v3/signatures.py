"""Correctifs ciblés à intégrer dans la V3.2 §2 — pack_match_signature.

Computed per pack_id+version from published semantic match fields.
Required before PUBLISHED status.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

from app.v3.context import normalize_text


def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "|".join(sorted(normalize_text(str(item)).matching for item in value))
    return normalize_text(str(value)).matching


def pack_match_payload(pack: Mapping[str, Any] | Any) -> dict[str, Any]:
    raw = dict(pack) if isinstance(pack, Mapping) else dict(getattr(pack, "__dict__", {}))
    dump = getattr(pack, "model_dump", None)
    if callable(dump):
        raw = dump(mode="python")
    lines = raw.get("lines") or ()
    line_payloads = []
    for line in lines:
        line_raw = (
            line
            if isinstance(line, Mapping)
            else (
                line.model_dump(mode="python")
                if hasattr(line, "model_dump")
                else getattr(line, "__dict__", {})
            )
        )
        line_payloads.append(
            {
                "phase": _norm(line_raw.get("phase")),
                "slot_index": int(line_raw.get("slot_index") or 0),
                "action": _norm(
                    line_raw.get("normalized_action") or line_raw.get("action")
                ),
                "object": _norm(line_raw.get("object_family") or line_raw.get("object")),
                "material": _norm(line_raw.get("material_family") or line_raw.get("material")),
                "unit": _norm(line_raw.get("unit")),
                "capabilities": _norm(line_raw.get("capability_tags") or ()),
                "exclusions": _norm(line_raw.get("exclusion_tags") or ()),
            }
        )
    line_payloads.sort(key=lambda item: (item["phase"], item["slot_index"]))
    return {
        "pack_code": _norm(raw.get("pack_code") or raw.get("pack_id")),
        "pack_version": int(raw.get("version") or raw.get("pack_version") or 0),
        "flow": _norm(raw.get("flow")),
        "trade_code": _norm(raw.get("trade_code")),
        "service_code": _norm(raw.get("service_code")),
        "required_coverage": _norm(raw.get("required_coverage") or ()),
        "exclusion_tags": _norm(raw.get("exclusion_tags") or ()),
        "lines": line_payloads,
    }


def compute_pack_match_signature(pack: Mapping[str, Any] | Any) -> str:
    """Correctifs ciblés à intégrer dans la V3.2 — signature stable pack_id+version."""

    payload = pack_match_payload(pack)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    version = payload["pack_version"]
    code = payload["pack_code"] or "pack"
    return f"PMS|{code}|v{version}|{digest[:32]}"


def line_content_hash(line: Mapping[str, Any] | Any) -> str:
    """Correctifs ciblés à intégrer dans la V3.2 §8 — empreinte ligne catalogue."""

    raw = (
        line
        if isinstance(line, Mapping)
        else (
            line.model_dump(mode="python")
            if hasattr(line, "model_dump")
            else getattr(line, "__dict__", {})
        )
    )
    payload = {
        "designation": _norm(raw.get("designation")),
        "unit": _norm(raw.get("unit")),
        "quantity_rule": _norm(raw.get("quantity_rule")),
        "default_quantity": str(raw.get("default_quantity") or ""),
        "price_id": str(raw.get("price_id") or ""),
        "price_version": int(raw.get("price_version") or 0),
        "vat_rule_id": str(raw.get("vat_rule_id") or ""),
        "vat_rule_version": int(raw.get("vat_rule_version") or 0),
        "vat_rate": str(raw.get("vat_rate") or ""),
        "unit_price_cents": int(raw.get("unit_price_cents") or 0),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def assert_signatures_unique(signatures: Iterable[str]) -> None:
    seen: set[str] = set()
    for signature in signatures:
        if not signature:
            raise ValueError("PACK_MATCH_SIGNATURE_REQUIRED")
        if signature in seen:
            raise ValueError(f"PACK_MATCH_SIGNATURE_COLLISION:{signature}")
        seen.add(signature)


__all__ = [
    "assert_signatures_unique",
    "compute_pack_match_signature",
    "line_content_hash",
    "pack_match_payload",
]
