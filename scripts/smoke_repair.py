"""Smoke-test the JSON healer + devis repair pipeline.

Reproduces the two production failure modes we have seen so far:

1. ``"ht"`` / ``"ttc"`` missing on the last ``Ligne`` because the LLM hit
   ``max_tokens`` mid-line.
2. Truncation cut the response right after a comma, producing a string
   that when rebalanced ends with ``, }`` or ``, ]`` (illegal trailing
   comma in strict JSON).

Run with::

    python -m scripts.smoke_repair
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.utils import clean_and_parse_json  # noqa: E402
from app.schemas.devis import DevisResponse  # noqa: E402
from app.services.devis_repair import repair_devis_payload  # noqa: E402
from app.services.upsell_engine import (  # noqa: E402
    f_p_quantity,
    inject_missing_complements,
)


# ---------------------------------------------------------------------------
# Case A - missing ht/ttc on the last line.
# ---------------------------------------------------------------------------
TRUNCATED_MISSING_FIELDS: str = """```json
{
  "date": "2026-04-27T10:00:00+02:00",
  "montant_ttc": 1980.0,
  "validite": "2026-05-27T23:59:59+02:00",
  "duree": "21jours",
  "blocs": [
    {
      "title": "Bloc principal",
      "lots": [
        {
          "title": "Plomberie",
          "ligne_ids": ["L1", "L2"],
          "lignes": [
            { "num": 1, "description": "Pose lavabo", "qte": 1, "unit": "u", "pu": 350.0, "tva": 10.0, "ht": 350.0, "ttc": 385.0 },
            { "num": 2, "description": "Pose chauffe-eau", "qte": 1, "unit": "u", "pu": 850.0, "tva": 10.0, "ht": 850.0, "ttc": 935.0 },
            { "num": 3, "description": "Robinetterie", "qte": 2, "unit": "u", "pu": 180.0, "tva": 10.0, "ht": 360.0, "ttc": 396.0 },
            { "num": 4, "description": "Mitigeurs thermo", "qte": 1, "unit": "u", "pu": 250.0, "tva": 10.0, "ht": 250.0, "ttc": 275.0 },
            { "num": 5, "description": "Clapet anti-retour", "qte": 1, "unit": "u", "pu": 60.0, "tva": 10.0, "ht": 60.0, "ttc": 66.0 },
            { "num": 6, "description": "Nourrices distribution", "qte": 1, "unit": "u", "pu": 145.0, "tva": 10.0, "ht": 145.0, "ttc": 159.5 },
            { "num": 7, "description": "Fourniture + pose de réseau d'évacuation en PVC Ø100mm", "qte": 15, "unit": "ml", "pu": 10.0, "tva": 30.0
"""


# ---------------------------------------------------------------------------
# Case A2 - missing colon between key and value mid-payload
# (this caused: "Expecting ':' delimiter (line N, col M)").
# json-repair must save us here; our hand-rolled healer can't.
# ---------------------------------------------------------------------------
MALFORMED_MISSING_COLON: str = """```json
{
  "date": "2026-04-27T10:00:00+02:00",
  "montant_ttc": 1200.0,
  "validite": "2026-05-27T23:59:59+02:00",
  "duree": 21,
  "blocs": [
    {
      "title": "Bloc principal",
      "lots": [
        {
          "title": "Plomberie",
          "ligne_ids": [],
          "lignes": [
            { "num": 1, "description" "Pose lavabo", "qte": 1, "unit": "u", "pu": 1000.0, "tva": 10.0, "ht": 1000.0, "ttc": 1100.0 }
          ]
        }
      ]
    }
  ]
}
```"""


# ---------------------------------------------------------------------------
# Case B - truncation right after a trailing comma
# (this caused: "Illegal trailing comma before end of object").
# ---------------------------------------------------------------------------
TRUNCATED_TRAILING_COMMA: str = """```json
{
  "date": "2026-04-27T10:00:00+02:00",
  "montant_ttc": 0,
  "validite": "2026-05-27T23:59:59+02:00",
  "duree": "14jours",
  "blocs": [
    {
      "title": "Bloc RDC",
      "lots": [
        {
          "title": "Électricité",
          "ligne_ids": [],
          "lignes": [
            { "num": 1, "description": "Pose tableau électrique, fusibles", "qte": 1, "unit": "u", "pu": 1200.0, "tva": 20.0, "ht": 1200.0, "ttc": 1440.0 },
            { "num": 2, "description": "Tirage cable, section 2.5", "qte": 50, "unit": "ml", "pu": 15.0, "tva": 20.0, "ht": 750.0, "ttc": 900.0 },
"""


def _run_case(name: str, raw: str) -> DevisResponse:
    print(f"=== {name} ".ljust(72, "="))
    parsed = clean_and_parse_json(raw)
    repaired = repair_devis_payload(parsed)
    devis = DevisResponse.model_validate(repaired)
    n_lignes = sum(len(lot.lignes) for bloc in devis.blocs for lot in bloc.lots)
    print(
        f"  passed - duree={devis.duree} (type={type(devis.duree).__name__}), "
        f"{n_lignes} lignes total, montant_ttc={devis.montant_ttc} EUR\n"
    )
    return devis


def _run_duree_coercion_cases() -> None:
    print("=== Case C: duree coercion ".ljust(72, "="))
    raw_inputs = [30, "30", "30jours", "30 jours", "30 days", 30.0]
    for raw_duree in raw_inputs:
        payload = {
            "date": "2026-04-27T10:00:00+02:00",
            "montant_ttc": 0,
            "validite": "2026-05-27T23:59:59+02:00",
            "duree": raw_duree,
            "blocs": [
                {
                    "title": "B",
                    "lots": [
                        {
                            "title": "L",
                            "lignes": [
                                {
                                    "num": 1,
                                    "description": "x",
                                    "qte": 1,
                                    "unit": "u",
                                    "pu": 100.0,
                                    "tva": 20.0,
                                    "ht": 100.0,
                                    "ttc": 120.0,
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        devis = DevisResponse.model_validate(payload)
        assert devis.duree == 30, f"got {devis.duree!r}"
        print(f"  {raw_duree!r:>14} -> {devis.duree}  ({type(devis.duree).__name__})")
    print()


def _run_upsell_complement_cases() -> None:
    print("=== Case D: required-complement injection ".ljust(72, "="))

    def _make_devis(designation: str) -> dict:
        return {
            "date": "2026-04-27T10:00:00+02:00",
            "montant_ttc": 0,
            "validite": "2026-05-27T23:59:59+02:00",
            "duree": 30,
            "blocs": [
                {
                    "title": "B",
                    "lots": [
                        {
                            "title": "T",
                            "lignes": [
                                {
                                    "num": 1,
                                    "description": designation,
                                    "qte": 1,
                                    "unit": "u",
                                    "pu": 100.0,
                                    "tva": 10.0,
                                    "ht": 100.0,
                                    "ttc": 110.0,
                                }
                            ],
                        }
                    ],
                }
            ],
        }

    # Toiture without évacuation -> should get one injection.
    devis = _make_devis("Pose tuiles mécaniques sur toiture neuve")
    n = inject_missing_complements(devis)
    last = devis["blocs"][0]["lots"][0]["lignes"][-1]
    assert n == 1 and "Évacuation" in last["description"], "toiture rule failed"
    print(f"  toiture     -> injected: {last['description']!r} ({last['pu']} EUR {last['unit']})")

    # Carrelage without ragréage -> one injection.
    devis = _make_devis("Pose carrelage grès cérame 25 m²")
    n = inject_missing_complements(devis)
    last = devis["blocs"][0]["lots"][0]["lignes"][-1]
    assert n == 1 and "Ragréage" in last["description"], "carrelage rule failed"
    print(f"  carrelage   -> injected: {last['description']!r} ({last['pu']} EUR/{last['unit']})")

    # Toiture WITH évacuation already present -> no injection.
    devis = _make_devis("Pose tuiles toiture")
    devis["blocs"][0]["lots"][0]["lignes"].append({
        "num": 2, "description": "Évacuation des gravats", "qte": 1, "unit": "forfait",
        "pu": 200, "tva": 10, "ht": 200, "ttc": 220,
    })
    n = inject_missing_complements(devis)
    assert n == 0, "should not double-inject when complement already exists"
    print("  toiture + evac already present -> no injection [OK]")
    print()


def _run_fp_heuristics_cases() -> None:
    print("=== Case E: F+P math heuristics ".ljust(72, "="))
    cases = [
        ("murs std 25 m2 (x2.4)",            {"surface_sol": 25,  "component": "murs"},                       60.0),
        ("murs wet room 6 m2 (x3.0)",        {"surface_sol": 6,   "component": "murs",     "is_wet_room": True}, 18.0),
        ("plafonds 40 m2",                   {"surface_sol": 40,  "component": "plafonds"},                    40.0),
        ("plinthes 25 m2 (4*sqrt(25)=20)",   {"surface_sol": 25,  "component": "plinthes"},                    20.0),
        ("faience SDB 6 m2 (3*6=18)",        {"surface_sol": 6,   "component": "faience"},                     18.0),
    ]
    for name, kwargs, expected in cases:
        result = f_p_quantity(**kwargs)
        ok = abs(result - expected) < 0.01
        print(f"  {name:<32} -> {result}  ({'OK' if ok else 'FAIL'})")
        assert ok, name
    print()


def main() -> int:
    _run_case("Case A: missing ht/ttc on last line", TRUNCATED_MISSING_FIELDS)
    _run_case("Case A2: missing colon mid-payload (json-repair)", MALFORMED_MISSING_COLON)
    _run_case("Case B: trailing comma after last full line", TRUNCATED_TRAILING_COMMA)
    _run_duree_coercion_cases()
    _run_upsell_complement_cases()
    _run_fp_heuristics_cases()

    print("All cases passed strict DevisResponse validation.")
    print()
    print("Final shape (case B, compact):")
    repaired = repair_devis_payload(clean_and_parse_json(TRUNCATED_TRAILING_COMMA))
    print(json.dumps(repaired, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
