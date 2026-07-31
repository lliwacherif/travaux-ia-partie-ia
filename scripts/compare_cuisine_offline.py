"""Offline V2 vs official-pack estimate for Renovation cuisine 8m2."""

from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
V2_PATH = ROOT / "tests/fixtures/v3/compare_cuisine/v2.json"
OUT_PATH = ROOT / "tests/fixtures/v3/compare_cuisine/official_cuis_pack_002_estimate.json"


def summarize_v2() -> dict:
    payload = json.loads(V2_PATH.read_text(encoding="utf-8"))
    response = payload["response"]
    lines: list[dict] = []
    for bloc in response["blocs"]:
        for lot in bloc["lots"]:
            for line in lot["lignes"]:
                lines.append(
                    {
                        **line,
                        "bloc": bloc["title"],
                        "lot": lot["title"],
                    }
                )

    # Heuristic: V2 padding uses generic forfait wording + fixed PU bands.
    padded = [
        line
        for line in lines
        if str(line.get("unit", "")).lower() == "forfait"
        or line["description"].startswith(
            (
                "Balisage",
                "Mise en place",
                "Acheminement",
                "Mise en sécurité",
                "Repérage",
                "Préparation",
                "Contrôle",
                "Nettoyage",
                "Enlèvement",
                "Finition",
                "Réglage",
                "Protection",
                "Evacuation",
                "Déchets",
                "Finitions",
                "Vérification",
                "Remise",
            )
        )
    ]
    realish = [line for line in lines if line not in padded]

    geometry = [
        {
            "bloc": bloc["title"],
            "n_lines": sum(len(lot["lignes"]) for lot in bloc["lots"]),
        }
        for bloc in response["blocs"]
    ]

    return {
        "engine": "v2",
        "status_code": payload["status_code"],
        "duration_ms": payload["duration_ms"],
        "montant_ht": response.get("montant_ht"),
        "montant_tva": response.get("montant_tva"),
        "montant_ttc": response.get("montant_ttc"),
        "n_lines": len(lines),
        "geometry": geometry,
        "realish_lines": [
            {
                "description": line["description"],
                "qte": line["qte"],
                "unit": line["unit"],
                "pu": line["pu"],
                "ht": line["ht"],
            }
            for line in realish
        ],
        "padded_count": len(padded),
        "all_lines": [
            {
                "bloc": line["bloc"],
                "description": line["description"],
                "qte": line["qte"],
                "unit": line["unit"],
                "pu": line["pu"],
                "ht": line["ht"],
            }
            for line in lines
        ],
    }


def estimate_official_pack() -> dict:
    conn = psycopg2.connect(
        host="localhost",
        user="liwa-travauxia",
        password="12300liwa",
        dbname="travauxia_devis",
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT code_pack, description, pack_json "
        "FROM packs_travaux WHERE code_pack = %s",
        ("CUIS-PACK-002",),
    )
    code, desc, pack = cur.fetchone()
    conn.close()
    if isinstance(pack, str):
        pack = json.loads(pack)

    area = Decimal("8")
    # Only surface is known from the prompt. Kitchen linear run assumption:
    # 2 * sqrt(area) ≈ two walls of a square room (common L/U proxy).
    linear = (Decimal("2") * (area.sqrt())).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    rows = []
    ht_total = Decimal("0")
    tva_total = Decimal("0")
    for index, line in enumerate(pack):
        role = "SETUP" if index < 3 else ("FINISH" if index >= 17 else "CORE")
        pu = Decimal(str(line.get("prix_unitaire_ht") or 0))
        unit = str(line.get("unite") or "u")
        unit_l = unit.lower().replace("²", "2")
        tva_rate = Decimal(str(line.get("taux_tva_defaut") or 10)) / Decimal("100")
        if unit_l in {"m2", "m²"}:
            qty = area
            qty_source = "PROJECT_AREA_M2"
        elif unit_l in {"ml", "m"}:
            qty = linear
            qty_source = "ASSUMED_LINEAR_2_SQRT_AREA"
        elif unit_l in {"forfait", "ens", "ensemble", "u", "unite", "unité"}:
            qty = Decimal("1")
            qty_source = "PACK_DEFAULT_OR_UNIT"
        else:
            qty = Decimal(str(line.get("quantite") or 1))
            qty_source = "PACK_DEFAULT"
        ht = (pu * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        tva = (ht * tva_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ttc = ht + tva
        ht_total += ht
        tva_total += tva
        rows.append(
            {
                "role": role,
                "code": line.get("code"),
                "designation": line.get("designation") or line.get("description"),
                "unit": unit,
                "pu": float(pu),
                "qte": float(qty),
                "qty_source": qty_source,
                "tva_rate": float(tva_rate * 100),
                "ht": float(ht),
                "ttc": float(ttc),
            }
        )

    return {
        "note": (
            "Offline estimate from official V2 pack CUIS-PACK-002 (3-14-3). "
            "Not a live /api/v3 response — postgres_v3/Docker unavailable and "
            "no curated cuisine pack is published in V3 yet."
        ),
        "pack_code": code,
        "description": desc,
        "geometry": {
            "setup": 3,
            "core": 14,
            "finish": 3,
            "total": len(pack),
        },
        "assumptions": {
            "area_m2": float(area),
            "linear_ml": float(linear),
            "linear_rule": "2 * sqrt(area) because prompt gives only 8m2",
        },
        "montant_ht": float(ht_total),
        "montant_tva": float(tva_total),
        "montant_ttc": float(ht_total + tva_total),
        "lines": rows,
    }


def main() -> None:
    v2 = summarize_v2()
    official = estimate_official_pack()
    report = {
        "prompt": "Renovation cuisine 8m2",
        "v2": v2,
        "v3_live": {
            "status_code": 503,
            "error": "postgres_v3 unreachable on :5433 (Docker Desktop unable to start / WSL)",
            "curated_cuisine_pack": False,
            "curated_packs_today": [
                "V3-MET-STRUCTURE-COMPLETE",
                "V3-CHARPENTE-BOIS-EXTENSION",
            ],
        },
        "v3_expected_if_cuis_pack_002_curated": official,
        "comparison": {
            "v2_ttc": v2["montant_ttc"],
            "official_pack_ttc_estimate": official["montant_ttc"],
            "official_pack_ht_estimate": official["montant_ht"],
            "v2_n_lines": v2["n_lines"],
            "official_n_lines": official["geometry"]["total"],
            "v2_padded_heuristic_count": v2["padded_count"],
            "v2_realish_count": len(v2["realish_lines"]),
            "delta_ttc_v2_minus_official": round(
                float(v2["montant_ttc"]) - float(official["montant_ttc"]), 2
            ),
        },
    }
    OUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["comparison"], ensure_ascii=False, indent=2))
    print("assumptions", official["assumptions"])
    print("wrote", OUT_PATH)


if __name__ == "__main__":
    main()
