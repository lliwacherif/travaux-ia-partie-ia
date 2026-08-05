"""Golden-scenario verification of the V2 engine against the real database.

Bypasses the LLM (fixed semantic payloads) and runs the deterministic engine
on real packs/prices, asserting realistic total ranges and structural
invariants. Run with the API database available:

    python scripts/verify_v2_devis.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.services.prestations_engine import (
    calculate_global_totals,
    load_packs_map,
    load_price_map,
    process_ai_lots,
)


def _pack(pack_id, qte, qtype, source, ptype="PRESTATION"):
    return {"id": pack_id, "type": ptype, "quantite": qte,
            "quantite_type": qtype, "source_qte": source}


def _lot(metier, packs, key="LOT_01"):
    return {"lot_key": key, "metier": metier, "zone": "interieur", "packs": packs}


SCENARIOS: list[dict] = [
    {
        "name": "5 splits (matched pack, count threading)",
        "user_text": "Installation de 5 splits muraux",
        "lots": [_lot("Climatisation – Ventilation – VMC",
                      [_pack("CLIM-VMC-PACK-001", 5, "unitaire", "5 splits muraux")])],
        "surface": None,
        "ht_range": (4000, 18000),
    },
    {
        "name": "Carrelage 20m² + peinture 40m² (per-pack surfaces)",
        "user_text": "Pose de carrelage 20m² dans la cuisine et peinture 40m² au salon",
        "lots": [
            _lot("Carrelage – Sols & Murs",
                 [_pack("CAR-PACK-001", 20, "surface_m2", "carrelage 20m²")], "LOT_01"),
            _lot("Peinture – Finitions – Enduits décoratifs",
                 [_pack("PEI-FIN-PACK-001", 40, "surface_m2", "peinture 40m²")], "LOT_02"),
        ],
        "surface": 20.0,
        "ht_range": (1500, 9000),
    },
    {
        "name": "Chauffe-eau sans surface (no invented m²)",
        "user_text": "Remplacement d'un chauffe-eau électrique",
        "lots": [_lot("Plomberie – Sanitaire",
                      [_pack("PLB-SAN-PACK-004", 1, "non_specifie", "non spécifié")])],
        "surface": None,
        "ht_range": (300, 4000),
    },
    {
        "name": "Fuite robinet (dépannage compact)",
        "user_text": "Fuite sur le robinet de la cuisine, urgent",
        "lots": [_lot("Dépannage & Interventions rapides",
                      [_pack("DEP-PLO-PACK-001", 1, "forfait", "non spécifié", "DEPANNAGE")])],
        "surface": None,
        "ht_range": (80, 1200),
    },
    {
        "name": "Isolation combles 80m² (TVA 5.5 partout — règle conservée)",
        "user_text": "Isolation des combles perdus sur 80 m²",
        "lots": [_lot("Isolation intérieure",
                      [_pack("ISO-INT-PACK-001", 80, "surface_m2", "combles perdus sur 80 m²")])],
        "surface": 80.0,
        "ht_range": (1200, 15000),
        "expect_tva": 5.5,
    },
]


async def main() -> int:
    engine = create_async_engine(str(settings.DATABASE_URL))
    async with AsyncSession(engine) as db:
        price_map, concept_map, metier_medians = await load_price_map(db)
        packs_maps = await load_packs_map(db)
    await engine.dispose()

    failures: list[str] = []
    for sc in SCENARIOS:
        blocks = process_ai_lots(
            sc["lots"], "particulier", "renovation",
            surface_m2=sc["surface"], user_text=sc["user_text"],
            price_map=price_map, concept_map=concept_map,
            metier_medians=metier_medians, packs_maps=packs_maps,
        )
        flat = [l for b in blocks for lot in b["lots"] for l in lot["lignes"]]
        totals = calculate_global_totals(flat)
        ht = totals["total_ht"]
        lo, hi = sc["ht_range"]
        ok = lo <= ht <= hi
        status = "OK " if ok else "FAIL"
        print(f"[{status}] {sc['name']}: HT={ht:.2f}€ (attendu {lo}-{hi}), "
              f"{len(blocks)} blocs, {len(flat)} lignes")
        if not ok:
            failures.append(sc["name"])

        # Structural invariants for every scenario.
        line_sum = round(sum(l["ht"] for l in flat), 2)
        if abs(line_sum - ht) > 1.0:
            print(f"       INCOHERENCE totaux: somme lignes {line_sum} != total {ht}")
            failures.append(sc["name"] + " (totaux)")
        if sc.get("expect_tva") is not None:
            bad = [l for l in flat if l["tva"] != sc["expect_tva"]]
            if bad:
                print(f"       TVA inattendue sur {len(bad)} lignes")
                failures.append(sc["name"] + " (tva)")

    # Engine determinism: same payload twice → identical totals.
    sc = SCENARIOS[0]
    t1 = calculate_global_totals([
        l for b in process_ai_lots(
            sc["lots"], surface_m2=sc["surface"], user_text=sc["user_text"],
            price_map=price_map, concept_map=concept_map,
            metier_medians=metier_medians, packs_maps=packs_maps,
        ) for lot in b["lots"] for l in lot["lignes"]
    ])["total_ttc"]
    t2 = calculate_global_totals([
        l for b in process_ai_lots(
            sc["lots"], surface_m2=sc["surface"], user_text=sc["user_text"],
            price_map=price_map, concept_map=concept_map,
            metier_medians=metier_medians, packs_maps=packs_maps,
        ) for lot in b["lots"] for l in lot["lignes"]
    ])["total_ttc"]
    det = "OK " if t1 == t2 else "FAIL"
    print(f"[{det}] Déterminisme moteur: {t1} == {t2}")
    if t1 != t2:
        failures.append("determinisme")

    if failures:
        print(f"\n{len(failures)} échec(s): {failures}")
        return 1
    print("\nTous les scénarios passent.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
