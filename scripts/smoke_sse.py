"""Hit POST /api/v1/devis/generate/stream and pretty-print the SSE feed.

Usage::

    python -m scripts.smoke_sse  "Pose carrelage 25m2 dans une salle de bain"
"""

from __future__ import annotations

import json
import sys
import time
from typing import Iterator

import httpx


def _parse_events(raw_chunks: Iterator[str]) -> Iterator[tuple[str, dict]]:
    """Split a stream of partial SSE bytes into (event, payload) tuples."""
    buffer = ""
    for chunk in raw_chunks:
        buffer += chunk
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            event = "message"
            data_lines: list[str] = []
            for line in frame.splitlines():
                if line.startswith(":"):  # comment / heartbeat
                    continue
                if line.startswith("event:"):
                    event = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:") :].lstrip())
            if not data_lines:
                continue
            try:
                payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                payload = {"_raw": "\n".join(data_lines)}
            yield event, payload


def main(text: str) -> int:
    url = "http://127.0.0.1:8000/api/v1/devis/generate/stream"
    body = {"text": text}
    start = time.monotonic()
    print(f"POST {url}\n  text = {text!r}\n")

    with httpx.stream(
        "POST",
        url,
        json=body,
        timeout=httpx.Timeout(connect=5.0, read=200.0, write=5.0, pool=5.0),
    ) as r:
        if r.status_code != 200:
            print(f"HTTP {r.status_code}: {r.read().decode()}")
            return 1
        for event, payload in _parse_events(r.iter_text()):
            elapsed = time.monotonic() - start
            if event == "progress":
                print(
                    f"  [{elapsed:5.1f}s] progress  step={payload.get('step')}/"
                    f"{payload.get('total')}  {payload.get('label')!r}"
                )
            elif event == "result":
                devis = payload.get("data") or {}
                n_lignes = sum(
                    len(lot.get("lignes", []))
                    for bloc in devis.get("blocs", [])
                    for lot in bloc.get("lots", [])
                )
                print(
                    f"  [{elapsed:5.1f}s] RESULT    "
                    f"duree={devis.get('duree')}  "
                    f"montant_ttc={devis.get('montant_ttc')}  "
                    f"lignes={n_lignes}"
                )
            elif event == "error":
                print(
                    f"  [{elapsed:5.1f}s] ERROR     "
                    f"status={payload.get('status')}  detail={payload.get('detail')!r}"
                )
            elif event == "done":
                print(f"  [{elapsed:5.1f}s] done.")
            else:
                print(f"  [{elapsed:5.1f}s] {event}: {payload}")
    return 0


if __name__ == "__main__":
    text = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "Pose carrelage gres cerame 25m2 dans une salle de bain, particulier"
    )
    raise SystemExit(main(text))
