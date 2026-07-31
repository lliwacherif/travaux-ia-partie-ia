"""Call V2 and V3 for the cuisine prompt and write a comparison fixture."""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "tests/fixtures/v3/compare_cuisine"
PROMPT = "Renovation cuisine 8m2"


def _post(url: str, payload: dict) -> tuple[int, dict | None, str | None, int]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with request.urlopen(req, timeout=240) as response:
            elapsed = int((time.perf_counter() - started) * 1000)
            return response.status, json.loads(response.read().decode("utf-8")), None, elapsed
    except error.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
        except json.JSONDecodeError:
            parsed = {"raw": detail}
        return exc.code, None, parsed, elapsed
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.perf_counter() - started) * 1000)
        return 0, None, {"detail": f"{type(exc).__name__}: {exc}"}, elapsed


def _flatten_v2(devis: dict) -> list[dict]:
    lines: list[dict] = []
    for bloc in devis.get("blocs", []):
        for lot in bloc.get("lots", []):
            for line in lot.get("lignes", []):
                lines.append(
                    {
                        "bloc": bloc.get("title"),
                        "description": line.get("description"),
                        "qte": line.get("qte"),
                        "unit": line.get("unit"),
                        "pu": line.get("pu"),
                        "ht": line.get("ht"),
                        "ttc": line.get("ttc"),
                    }
                )
    return lines


def _flatten_v3(envelope: dict) -> list[dict]:
    devis = envelope.get("devis") or {}
    quote = envelope.get("quote") or {}
    if devis.get("blocs"):
        return _flatten_v2(devis)
    lines: list[dict] = []
    for line in quote.get("setup_lines") or []:
        lines.append(
            {
                "phase": "SETUP",
                "description": line.get("designation"),
                "qte": line.get("quantity"),
                "unit": line.get("unit"),
                "pu": (line.get("unit_price_cents") or 0) / 100,
                "ht": (line.get("total_ht_cents") or 0) / 100,
            }
        )
    for block in quote.get("trade_blocks") or []:
        for line in block.get("lines") or []:
            lines.append(
                {
                    "phase": "CORE",
                    "description": line.get("designation"),
                    "qte": line.get("quantity"),
                    "unit": line.get("unit"),
                    "pu": (line.get("unit_price_cents") or 0) / 100,
                    "ht": (line.get("total_ht_cents") or 0) / 100,
                }
            )
    for line in quote.get("finish_lines") or []:
        lines.append(
            {
                "phase": "FINISH",
                "description": line.get("designation"),
                "qte": line.get("quantity"),
                "unit": line.get("unit"),
                "pu": (line.get("unit_price_cents") or 0) / 100,
                "ht": (line.get("total_ht_cents") or 0) / 100,
            }
        )
    return lines


def _v3_ttc(envelope: dict) -> float | None:
    devis = envelope.get("devis") or {}
    if devis.get("montant_ttc") is not None:
        return devis.get("montant_ttc")
    totals = (envelope.get("quote") or {}).get("totals") or {}
    if totals.get("ttc_cents") is not None:
        return totals["ttc_cents"] / 100
    return None


def _v3_pack_code(envelope: dict) -> str | None:
    quote = envelope.get("quote") or {}
    blocks = quote.get("trade_blocks") or []
    if not blocks:
        return None
    pack_id = blocks[0].get("pack_id")
    return pack_id


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    v2_status, v2_body, v2_err, v2_ms = _post(
        "http://127.0.0.1:8001/api/v1/devis/generate",
        {"text": PROMPT},
    )

    v3_status, v3_body, v3_err, v3_ms = _post(
        "http://127.0.0.1:8001/api/v3/devis/generate",
        {"text": PROMPT},
    )

    v2_lines = _flatten_v2(v2_body or {}) if v2_body else []
    v3_lines = _flatten_v3(v3_body or {}) if v3_body else []

    v2_ttc = (v2_body or {}).get("montant_ttc")
    v3_ttc = _v3_ttc(v3_body or {}) if v3_body else None
    v3_quote = (v3_body or {}).get("quote") or {}

    comparison = {
        "prompt": PROMPT,
        "v2": {
            "status_code": v2_status,
            "duration_ms": v2_ms,
            "montant_ttc": v2_ttc,
            "n_lines": len(v2_lines),
            "geometry": [
                {
                    "bloc": bloc.get("title"),
                    "n": sum(len(lot.get("lignes", [])) for lot in bloc.get("lots", [])),
                }
                for bloc in (v2_body or {}).get("blocs", [])
            ],
            "sample_lines": v2_lines[:5],
            "error": v2_err,
        },
        "v3": {
            "status_code": v3_status,
            "duration_ms": v3_ms,
            "montant_ttc": v3_ttc,
            "n_lines": len(v3_lines),
            "pack_id": _v3_pack_code(v3_body or {}) if v3_body else None,
            "trade_code": (
                ((v3_quote.get("trade_blocks") or [{}])[0].get("trade_code"))
                if v3_quote
                else None
            ),
            "totals_cents": v3_quote.get("totals"),
            "sample_lines": v3_lines[:8],
            "error": v3_err,
        },
    }

    (OUT_DIR / "v2.json").write_text(
        json.dumps(
            {
                "engine": "v2",
                "prompt": PROMPT,
                "status_code": v2_status,
                "duration_ms": v2_ms,
                "response": v2_body,
                "error": v2_err,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (OUT_DIR / "v3.json").write_text(
        json.dumps(
            {
                "engine": "v3",
                "prompt": PROMPT,
                "status_code": v3_status,
                "duration_ms": v3_ms,
                "response": v3_body,
                "error": v3_err,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (OUT_DIR / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(comparison, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
