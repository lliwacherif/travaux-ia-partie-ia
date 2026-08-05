"""Capture the devis produced by POST /api/v1/devis/generate/stream.

Usage:
    python scripts/capture_devis.py --url http://127.0.0.1:8010 \
        --text "..." --out scripts/compare_out/baseline.json

Consumes the SSE stream, stores the `result` event payload as JSON and
prints a compact human-readable summary (blocs, lines, totals).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx


def wait_for_health(base_url: str, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/health", timeout=3.0)
            if r.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(1.0)
    raise RuntimeError(f"Server at {base_url} not healthy after {timeout_s}s: {last_err}")


def capture(base_url: str, text: str, timeout_s: float = 300.0) -> dict:
    """Consume the SSE stream and return {'result': ..., 'title': ..., 'error': ...}."""
    out: dict = {"result": None, "title": None, "error": None, "progress": []}
    with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=10.0)) as client:
        with client.stream(
            "POST",
            f"{base_url}/api/v1/devis/generate/stream",
            json={"text": text},
        ) as resp:
            resp.raise_for_status()
            event_type = None
            for raw_line in resp.iter_lines():
                line = raw_line.strip()
                if line.startswith("event:"):
                    event_type = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_str = line[len("data:"):].strip()
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    if event_type == "result":
                        # Router wraps the devis as {"data": {...}}.
                        out["result"] = data.get("data", data)
                    elif event_type == "title":
                        out["title"] = data.get("title")
                    elif event_type == "error":
                        out["error"] = data
                    elif event_type == "progress":
                        out["progress"].append(data)
    return out


def summarize(devis: dict) -> str:
    lines_out: list[str] = []
    grand_ht = 0.0
    for bloc in devis.get("blocs", []):
        lines_out.append(f"## {bloc.get('title')}")
        for lot in bloc.get("lots", []):
            for l in lot.get("lignes", []):
                grand_ht += l.get("ht", 0.0)
                lines_out.append(
                    f"  {l.get('num'):>2}. qte={l.get('qte'):>8} {l.get('unit'):<8} "
                    f"pu={l.get('pu'):>9.2f} tva={l.get('tva'):>4} "
                    f"ht={l.get('ht'):>10.2f}  {l.get('description', '')[:70]}"
                )
    lines_out.append(f"TOTAL HT (somme lignes): {round(grand_ht, 2)}")
    lines_out.append(f"MONTANT TTC (devis):     {devis.get('montant_ttc')}")
    lines_out.append(f"DUREE: {devis.get('duree')} jours")
    return "\n".join(lines_out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    parser.add_argument("--text", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    wait_for_health(args.url)
    started = time.monotonic()
    captured = capture(args.url, args.text)
    elapsed = time.monotonic() - started

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if captured["error"]:
        print(f"ERROR event: {captured['error']}")
        return 1
    if not captured["result"]:
        print("No result event received.")
        return 1

    print(f"Captured in {elapsed:.1f}s -> {out_path}")
    print(f"Title: {captured['title']}")
    print(summarize(captured["result"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
