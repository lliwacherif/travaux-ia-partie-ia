"""Compare V2 vs V3 for one prompt and print a markdown table summary."""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "tests/fixtures/v3/compare_isolation_toiture"
PROMPT = "Isolation mur 8m sur 2m et changement toiture 8m2"
BASE = "http://127.0.0.1:8001"


def _post(url: str, payload: dict) -> tuple[int, dict | None, object, int]:
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


def flatten_v2(devis: dict) -> list[dict]:
    rows: list[dict] = []
    for bloc in devis.get("blocs") or []:
        for lot in bloc.get("lots") or []:
            for line in lot.get("lignes") or []:
                rows.append(
                    {
                        "engine": "V2",
                        "bloc": bloc.get("title"),
                        "lot": lot.get("title"),
                        "description": line.get("description"),
                        "qte": line.get("qte"),
                        "unit": line.get("unit"),
                        "pu": line.get("pu"),
                        "tva": line.get("tva"),
                        "ht": line.get("ht"),
                        "ttc": line.get("ttc"),
                    }
                )
    return rows


def flatten_v3(envelope: dict) -> list[dict]:
    devis = envelope.get("devis") or {}
    if devis.get("blocs"):
        rows = flatten_v2(devis)
        for row in rows:
            row["engine"] = "V3"
        return rows
    rows: list[dict] = []
    quote = envelope.get("quote") or {}
    for line in quote.get("setup_lines") or []:
        rows.append(_v3_line("SETUP", "Mise en place", line))
    for block in quote.get("trade_blocks") or []:
        trade = block.get("trade_code") or "CORE"
        for line in block.get("lines") or []:
            rows.append(_v3_line("CORE", trade, line))
    for line in quote.get("finish_lines") or []:
        rows.append(_v3_line("FINISH", "Finitions", line))
    return rows


def _v3_line(phase: str, bloc: str, line: dict) -> dict:
    pu = (line.get("unit_price_cents") or 0) / 100
    ht = (line.get("total_ht_cents") or 0) / 100
    tva = line.get("vat_rate")
    ttc = round(ht * (1 + float(tva or 0) / 100), 2)
    return {
        "engine": "V3",
        "bloc": bloc,
        "lot": phase,
        "description": line.get("designation"),
        "qte": line.get("quantity"),
        "unit": line.get("unit"),
        "pu": pu,
        "tva": tva,
        "ht": ht,
        "ttc": ttc,
    }


def try_bases() -> str:
    for base in ("http://127.0.0.1:8001", "http://127.0.0.1:8000"):
        try:
            with request.urlopen(f"{base}/api/v3/devis/system/readiness", timeout=5) as r:
                if r.status == 200:
                    return base
        except Exception:
            continue
    return "http://127.0.0.1:8001"


def main() -> None:
    global BASE
    BASE = try_bases()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    v2_status, v2_body, v2_err, v2_ms = _post(
        f"{BASE}/api/v1/devis/generate", {"text": PROMPT}
    )
    v3_status, v3_body, v3_err, v3_ms = _post(
        f"{BASE}/api/v3/devis/generate", {"text": PROMPT}
    )

    v2_rows = flatten_v2(v2_body or {}) if v2_body else []
    v3_rows = flatten_v3(v3_body or {}) if v3_body else []

    v2_ttc = (v2_body or {}).get("montant_ttc")
    v3_ttc = None
    if v3_body:
        v3_ttc = (v3_body.get("devis") or {}).get("montant_ttc")
        if v3_ttc is None:
            cents = ((v3_body.get("quote") or {}).get("totals") or {}).get("ttc_cents")
            if cents is not None:
                v3_ttc = cents / 100

    payload = {
        "prompt": PROMPT,
        "base": BASE,
        "v2": {
            "status_code": v2_status,
            "duration_ms": v2_ms,
            "montant_ttc": v2_ttc,
            "n_lines": len(v2_rows),
            "error": v2_err,
            "response": v2_body,
            "lines": v2_rows,
        },
        "v3": {
            "status_code": v3_status,
            "duration_ms": v3_ms,
            "montant_ttc": v3_ttc,
            "n_lines": len(v3_rows),
            "error": v3_err,
            "response": v3_body,
            "lines": v3_rows,
            "trade_blocks": (
                ((v3_body or {}).get("quote") or {}).get("trade_blocks")
                if v3_body
                else None
            ),
        },
    }
    (OUT_DIR / "full.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "base": BASE,
        "v2_status": v2_status,
        "v2_ttc": v2_ttc,
        "v2_n": len(v2_rows),
        "v2_error": v2_err,
        "v3_status": v3_status,
        "v3_ttc": v3_ttc,
        "v3_n": len(v3_rows),
        "v3_error": v3_err,
        "v2_lines": v2_rows,
        "v3_lines": v3_rows,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
