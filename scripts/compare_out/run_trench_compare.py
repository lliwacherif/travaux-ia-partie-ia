"""Local V2 vs V3 compare for the trench / EU prompt."""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib import error, request

OUT = Path(__file__).resolve().parent
PROMPT = (
    "je dois réaliser 32 ml de tranchées entre la limite de propriété et le "
    "bâtiment pour l’eau, l’électricité et les télécommunications. Prévoir "
    "terrassement, lit de sable, fourreaux réglementaires, grillages "
    "avertisseurs, remblai compacté et deux regards.canalisation d’eaux "
    "usées PVC sur 18 ml"
)
BASE = "http://127.0.0.1:8000"


def post(path: str) -> tuple[int, dict | None, object, int]:
    body = json.dumps({"text": PROMPT}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        f"{BASE}{path}",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with request.urlopen(req, timeout=240) as response:
            elapsed = int((time.perf_counter() - started) * 1000)
            return (
                response.status,
                json.loads(response.read().decode("utf-8")),
                None,
                elapsed,
            )
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


def flatten_v2(devis: dict, engine: str) -> list[dict]:
    rows: list[dict] = []
    for bloc in devis.get("blocs") or []:
        for lot in bloc.get("lots") or []:
            for line in lot.get("lignes") or []:
                rows.append(
                    {
                        "engine": engine,
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


def main() -> None:
    v2_status, v2, v2_err, v2_ms = post("/api/v1/devis/generate")
    v3_status, v3, v3_err, v3_ms = post("/api/v3/devis/generate")

    if v2 is not None:
        (OUT / "trench_v2.json").write_text(
            json.dumps(v2, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        (OUT / "trench_v2_err.txt").write_text(
            json.dumps(v2_err, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if v3 is not None:
        (OUT / "trench_v3.json").write_text(
            json.dumps(v3, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        (OUT / "trench_v3_err.txt").write_text(
            json.dumps(v3_err, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    summary = {
        "prompt": PROMPT,
        "v2": {
            "status": v2_status,
            "ms": v2_ms,
            "ttc": (v2 or {}).get("montant_ttc") if v2 else None,
            "lines": len(flatten_v2(v2, "V2")) if v2 else 0,
            "error": v2_err,
        },
        "v3": {
            "status": v3_status,
            "ms": v3_ms,
            "ttc": ((v3 or {}).get("devis") or {}).get("montant_ttc") if v3 else None,
            "generation_mode": ((v3 or {}).get("quote") or {}).get("generation_mode")
            if v3
            else None,
            "trade": None,
            "lines": len(flatten_v2((v3 or {}).get("devis") or {}, "V3")) if v3 else 0,
            "error": v3_err,
        },
    }
    if v3 and v3.get("quote"):
        blocks = v3["quote"].get("trade_blocks") or []
        if blocks:
            summary["v3"]["trade"] = blocks[0].get("trade_code")
        arb = ((v3["quote"].get("trace") or {}).get("arbitration")) or {}
        summary["v3"]["arbitration"] = arb
        summary["v3"]["pack_id"] = (v3["quote"].get("selected_pack") or {}).get(
            "pack_code"
        ) or (v3["quote"].get("trace") or {}).get("selected_pack_code")

    (OUT / "trench_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
