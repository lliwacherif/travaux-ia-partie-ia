"""Unit tests for the V2 deterministic devis engine (prestations_engine).

Pure in-memory tests: no database, no OpenAI key required. They lock in the
quantity/price/structure behavior of the enhanced engine, plus a regression
matrix asserting the TVA rules are byte-identical to the previous version
(user decision: TVA logic must NOT change).
"""

from __future__ import annotations

import math

import pytest

from app.services.prestations_engine import (
    _find_pack,
    _pad_or_truncate_lines,
    _sanity_clamp,
    calculate_global_totals,
    calculate_quantity_from_unit,
    decide_tva_finale,
    estimate_duration_days,
    extract_surface_m2,
    normalize_unit,
    process_ai_lots,
)


# ---------------------------------------------------------------------------
# Synthetic catalog fixtures
# ---------------------------------------------------------------------------
def _mk_pack(code: str, nom: str, metier: str, lines: list[dict]) -> dict:
    return {
        "code_pack": code,
        "nom_pack": nom,
        "corps_metier": metier,
        "sous_metier_depannage": None,
        "pack_json": lines,
    }


CLIM_PACK = _mk_pack(
    "CLIM-VMC-PACK-001",
    "Climatisation mono-split mural",
    "Climatisation – Ventilation – VMC",
    [
        {"bloc": 1, "unite": "forfait", "quantite": 1, "prix_unitaire_ht": 50,
         "taux_tva_defaut": 10, "designation": "Protection du chantier"},
        {"bloc": 2, "unite": "u", "quantite": 1, "prix_unitaire_ht": 100,
         "taux_tva_defaut": 10, "designation": "Dépose de l'ancien climatiseur"},
        {"bloc": 3, "unite": "u", "quantite": 1, "prix_unitaire_ht": 900,
         "taux_tva_defaut": 10, "designation": "Fourniture et pose du split mural"},
        {"bloc": 3, "unite": "forfait", "quantite": 1, "prix_unitaire_ht": 120,
         "taux_tva_defaut": 10, "designation": "Mise en service et contrôles"},
        {"bloc": 4, "unite": "forfait", "quantite": 1, "prix_unitaire_ht": 40,
         "taux_tva_defaut": 10, "designation": "Nettoyage de fin d'intervention"},
    ],
)

PEINTURE_PACK = _mk_pack(
    "PEI-FIN-PACK-001",
    "Peinture murs et plafonds",
    "Peinture – Finitions – Enduits décoratifs",
    [
        {"bloc": 2, "unite": "m²", "quantite": 1, "prix_unitaire_ht": 6,
         "taux_tva_defaut": 10, "designation": "Préparation des supports"},
        {"bloc": 3, "unite": "m²", "quantite": 1, "prix_unitaire_ht": 18,
         "taux_tva_defaut": 10, "designation": "Peinture mate deux couches"},
    ],
)

CARRELAGE_PACK = _mk_pack(
    "CAR-PACK-001",
    "Carrelage sol grès cérame",
    "Carrelage – Sols & Murs",
    [
        {"bloc": 3, "unite": "m²", "quantite": 1, "prix_unitaire_ht": 55,
         "taux_tva_defaut": 10, "designation": "Fourniture et pose carrelage sol"},
    ],
)

CHAUFFE_EAU_PACK = _mk_pack(
    "PLB-PACK-010",
    "Remplacement chauffe-eau électrique",
    "Plomberie – Sanitaire",
    [
        {"bloc": 2, "unite": "u", "quantite": 1, "prix_unitaire_ht": 90,
         "taux_tva_defaut": 10, "designation": "Dépose de l'ancien chauffe-eau"},
        {"bloc": 3, "unite": "u", "quantite": 1, "prix_unitaire_ht": 650,
         "taux_tva_defaut": 10, "designation": "Fourniture et pose du chauffe-eau"},
        # Annex m² line in a unit-driven pack: must NOT get an invented surface.
        {"bloc": 3, "unite": "m²", "quantite": 1, "prix_unitaire_ht": 25,
         "taux_tva_defaut": 10, "designation": "Reprise ponctuelle du mur support"},
    ],
)

DEP_PACK = _mk_pack(
    "DEP-PLO-PACK-001",
    "Fuite sur robinet",
    "Dépannage & Interventions rapides",
    [
        {"bloc": 1, "unite": "u", "quantite": 1, "prix_unitaire_ht": 49,
         "taux_tva_defaut": 10, "designation": "Déplacement et diagnostic"},
        {"bloc": 2, "unite": "u", "quantite": 1, "prix_unitaire_ht": 80,
         "taux_tva_defaut": 10, "designation": "Remplacement du mécanisme"},
        {"bloc": 4, "unite": "u", "quantite": 1, "prix_unitaire_ht": 30,
         "taux_tva_defaut": 10, "designation": "Essais et nettoyage"},
    ],
)

ALL_PACKS = [CLIM_PACK, PEINTURE_PACK, CARRELAGE_PACK, CHAUFFE_EAU_PACK, DEP_PACK]
EXACT_MAP = {p["code_pack"]: p for p in ALL_PACKS}
PACKS_MAPS = (EXACT_MAP, ALL_PACKS)

METIER_MEDIANS = {
    "climatisation_ventilation_vmc": {"u": 400.0, "forfait": 150.0},
    "peinture_finitions_enduits_decoratifs": {"m²": 20.0},
    "plomberie_sanitaire": {"u": 250.0, "forfait": 300.0},
}
CONCEPT_MAP = {
    "climatisation": {"u": 450.0},
    "peinture": {"m²": 20.0},
    "toiture": {"m²": 96.0},
}


def _ai_pack(pack_id: str, qte: float, qtype: str, source: str = "x",
             ptype: str = "PRESTATION") -> dict:
    return {"id": pack_id, "type": ptype, "quantite": qte,
            "quantite_type": qtype, "source_qte": source}


def _ai_lot(metier: str, packs: list[dict], key: str = "LOT_01") -> dict:
    return {"lot_key": key, "metier": metier, "zone": "interieur", "packs": packs}


def _run(lots, *, user_text="", surface=None, client="particulier", nature="renovation"):
    return process_ai_lots(
        lots, client, nature,
        surface_m2=surface, user_text=user_text,
        price_map={}, concept_map=CONCEPT_MAP, metier_medians=METIER_MEDIANS,
        packs_maps=PACKS_MAPS,
    )


def _intervention_blocks(blocks):
    return blocks[1:-1]


def _flat(blocks):
    return [l for b in blocks for lot in b["lots"] for l in lot["lignes"]]


def _block_ht(block) -> float:
    return round(sum(l["ht"] for lot in block["lots"] for l in lot["lignes"]), 2)


# ---------------------------------------------------------------------------
# 1. Quantity threading (the core QTE fix)
# ---------------------------------------------------------------------------
class TestQuantityThreading:
    def test_unit_count_multiplies_matched_pack_work_lines(self):
        """'5 splits' on a matched pack must bill 5x the per-unit work lines."""
        lots = [_ai_lot("Climatisation – Ventilation – VMC",
                        [_ai_pack("CLIM-VMC-PACK-001", 5, "unitaire", "5 splits")])]
        b5 = _run(lots)
        lots1 = [_ai_lot("Climatisation – Ventilation – VMC",
                         [_ai_pack("CLIM-VMC-PACK-001", 1, "unitaire", "1 split")])]
        b1 = _run(lots1)

        inter5 = _intervention_blocks(b5)[0]
        lines5 = inter5["lots"][0]["lignes"]
        pose = next(l for l in lines5 if "pose du split" in l["description"])
        assert pose["qte"] == 5
        assert pose["ht"] == 4500.0  # 5 x 900

        # The devis for 5 splits must now cost strictly more than for 1.
        assert _block_ht(_intervention_blocks(b5)[0]) > _block_ht(_intervention_blocks(b1)[0])

    def test_prep_lines_not_multiplied_by_count(self):
        lots = [_ai_lot("Climatisation – Ventilation – VMC",
                        [_ai_pack("CLIM-VMC-PACK-001", 5, "unitaire", "5 splits")])]
        blocks = _run(lots)
        prep_lines = blocks[0]["lots"][0]["lignes"]
        protection = next(
            (l for l in prep_lines if "Protection du chantier" in l["description"]), None
        )
        assert protection is not None
        assert protection["qte"] == 1

    def test_per_pack_surfaces_multi_lot(self):
        """Carrelage 20 m² + peinture 45 m² must each use THEIR surface."""
        lots = [
            _ai_lot("Carrelage – Sols & Murs",
                    [_ai_pack("CAR-PACK-001", 20, "surface_m2", "carrelage 20m²")], "LOT_01"),
            _ai_lot("Peinture – Finitions – Enduits décoratifs",
                    [_ai_pack("PEI-FIN-PACK-001", 45, "surface_m2", "peinture 45m²")], "LOT_02"),
        ]
        blocks = _run(lots, user_text="carrelage 20m² et peinture 45m²", surface=20)
        car_block, pei_block = _intervention_blocks(blocks)

        car_main = next(l for l in car_block["lots"][0]["lignes"]
                        if "carrelage sol" in l["description"])
        assert car_main["qte"] == 20

        pei_main = next(l for l in pei_block["lots"][0]["lignes"]
                        if "deux couches" in l["description"])
        assert pei_main["qte"] == 45  # NOT 20 (the old global-surface bug)

    def test_surface_parsed_from_source_qte(self):
        """A surface quoted in source_qte drives m² lines even when quantite_type is not surface."""
        lots = [_ai_lot("Peinture – Finitions – Enduits décoratifs",
                        [_ai_pack("PEI-FIN-PACK-001", 1, "forfait", "les murs du salon 35 m²")])]
        blocks = _run(lots)
        main = next(l for l in _intervention_blocks(blocks)[0]["lots"][0]["lignes"]
                    if "deux couches" in l["description"])
        assert main["qte"] == 35

    def test_unit_driven_pack_without_surface_keeps_annex_m2_small(self):
        """'Remplacement chauffe-eau' (no surface) must not invent 20-50 m² of wall repair."""
        lots = [_ai_lot("Plomberie – Sanitaire",
                        [_ai_pack("PLB-PACK-010", 1, "non_specifie", "non spécifié")])]
        blocks = _run(lots, user_text="remplacement d'un chauffe-eau")
        annex = next(l for l in _intervention_blocks(blocks)[0]["lots"][0]["lignes"]
                     if "mur support" in l["description"])
        assert annex["qte"] == 1  # pack quantity, not an invented surface

    def test_surface_driven_pack_without_surface_uses_metier_default(self):
        """A peinture pack with no dimension gets the conservative default, not qty 1."""
        lots = [_ai_lot("Peinture – Finitions – Enduits décoratifs",
                        [_ai_pack("PEI-FIN-PACK-001", 1, "non_specifie", "non spécifié")])]
        blocks = _run(lots, user_text="peindre le salon")
        main = next(l for l in _intervention_blocks(blocks)[0]["lots"][0]["lignes"]
                    if "deux couches" in l["description"])
        assert main["qte"] == 30  # _DEFAULT_SURFACE_BY_METIER["peinture"]

    def test_longueur_ml_drives_linear_lines(self):
        qty, rule = calculate_quantity_from_unit(
            "ml", 20.0, 1.0, mode_calcul_ml=None, geometry=None,
            longueur_ml=25.0, surface_known=False,
        )
        assert qty == 25.0
        assert rule == "ML_FROM_AI_LENGTH"

    def test_longueur_ml_overrides_perimetre_mode(self):
        """'32 ml de tranchées' must beat the geometry perimeter estimate."""
        qty, rule = calculate_quantity_from_unit(
            "ml", 40.0, 1.0, mode_calcul_ml="PERIMETRE", coefficient_ml=1.0,
            geometry=None, longueur_ml=32.0, surface_known=False,
        )
        assert qty == 32.0
        assert rule == "ML_FROM_AI_LENGTH"

    def test_longueur_ml_scaled_by_mode_coefficient(self):
        qty, _ = calculate_quantity_from_unit(
            "ml", 40.0, 1.0, mode_calcul_ml="RATIO_SURFACE", coefficient_ml=0.5,
            geometry=None, longueur_ml=32.0, surface_known=False,
        )
        assert qty == 16.0

    def test_longueur_ml_does_not_override_fixed_lines(self):
        """Curated FIXE quantities (e.g. 'raccordement 2 ml') stay fixed."""
        qty, rule = calculate_quantity_from_unit(
            "ml", 40.0, 2.0, mode_calcul_ml="FIXE",
            geometry=None, longueur_ml=32.0, surface_known=False,
        )
        assert qty == 2.0
        assert rule == "ML_FIXED_PACK_QTY"

    def test_trench_volume_from_length(self):
        qty, rule = calculate_quantity_from_unit(
            "m³", 19.2, 1.0, geometry=None, longueur_ml=32.0,
            surface_known=True, designation="Evacuation des déblais en décharge",
        )
        assert qty == pytest.approx(32 * 0.35)
        assert rule == "M3_FROM_LENGTH_035"

    def test_strip_surface_inferred_from_length(self):
        """32 ml with no surface → m² lines billed on the 0.6 m working strip."""
        trench_pack = _mk_pack(
            "VRD-PACK-001", "Tranchée réseaux",
            "Terrassement – VRD – Assainissement",
            [
                {"bloc": 2, "unite": "m²", "quantite": 1, "prix_unitaire_ht": 4.5,
                 "taux_tva_defaut": 20, "designation": "Décapage de la terre végétale"},
                {"bloc": 3, "unite": "ml", "quantite": 1, "prix_unitaire_ht": 22,
                 "taux_tva_defaut": 20, "designation": "Ouverture mécanique de tranchée",
                 "mode_calcul_ml": "PERIMETRE", "coefficient_ml": 1.0},
            ],
        )
        maps = ({trench_pack["code_pack"]: trench_pack}, [trench_pack])
        lots = [_ai_lot("Terrassement – VRD – Assainissement",
                        [_ai_pack("VRD-PACK-001", 32, "longueur_ml", "32 ml de tranchées")])]
        blocks = process_ai_lots(
            lots, surface_m2=None, user_text="32 ml de tranchées",
            price_map={}, concept_map=CONCEPT_MAP, metier_medians=METIER_MEDIANS,
            packs_maps=maps,
        )
        lines = _intervention_blocks(blocks)[0]["lots"][0]["lignes"]
        trench = next(l for l in lines if "tranchée" in l["description"].lower())
        assert trench["qte"] == 32.0  # NOT the perimeter estimate
        decapage = next(l for l in lines if "Décapage" in l["description"])
        assert decapage["qte"] == pytest.approx(32 * 0.6)

    def test_quantity_cap(self):
        qty, rule = calculate_quantity_from_unit(
            "m²", 50_000.0, 1.0, surface_known=True,
        )
        assert qty == 10_000.0
        assert rule.endswith("_CAPPED")


# ---------------------------------------------------------------------------
# 2. Unit normalization
# ---------------------------------------------------------------------------
class TestUnits:
    @pytest.mark.parametrize("raw,expected", [
        ("mètre linéaire", "ml"),
        ("metre lineaire", "ml"),
        ("mètre", "ml"),
        ("m", "ml"),
        ("point", "u"),
        ("sac", "u"),
        ("paire", "u"),
        ("unité", "u"),
        ("pièce", "u"),
        ("semaine", "forfait"),
        ("litre", "l"),
        ("m2", "m²"),
        ("M2", "m²"),
    ])
    def test_normalize_unit(self, raw, expected):
        assert normalize_unit(raw) == expected

    def test_unknown_unit_never_falls_back_to_surface(self):
        qty, rule = calculate_quantity_from_unit(
            "gugusse", 80.0, 1.0, surface_known=True,
        )
        assert qty == 1.0
        assert rule == "GENERIC_DEFAULT"


# ---------------------------------------------------------------------------
# 3. Surface extraction
# ---------------------------------------------------------------------------
class TestSurfaceExtraction:
    def test_extracts_first_surface(self):
        assert extract_surface_m2("extension de 24 m² avec murs de 52 m²") == 24.0

    def test_returns_none_when_absent(self):
        assert extract_surface_m2("remplacement d'un chauffe-eau") is None

    def test_comma_decimal(self):
        assert extract_surface_m2("piece de 12,5 m2") == 12.5


# ---------------------------------------------------------------------------
# 4. TVA regression — rules must stay EXACTLY as before (user decision)
# ---------------------------------------------------------------------------
class TestTvaRegression:
    def test_isolation_lot_or_line_wins_even_for_pro(self):
        assert decide_tva_finale("", "Isolation intérieure", "pro") == 5.5
        assert decide_tva_finale("Pose laine de verre", "Plâtrerie", "pro") == 5.5

    def test_pro_gets_20(self):
        assert decide_tva_finale("Peinture", "Peinture", "pro") == 20.0

    def test_neuf_gets_20(self):
        assert decide_tva_finale("Peinture", "Peinture", "particulier", "neuf") == 20.0

    def test_renovation_particulier_gets_10(self):
        assert decide_tva_finale("Peinture", "Peinture", "particulier", "renovation") == 10.0

    def test_force_tva_55_whole_devis_when_isolation_in_text(self):
        lots = [_ai_lot("Peinture – Finitions – Enduits décoratifs",
                        [_ai_pack("PEI-FIN-PACK-001", 30, "surface_m2", "30 m²")])]
        blocks = _run(lots, user_text="peinture après isolation des murs", surface=30)
        for line in _flat(blocks):
            assert line["tva"] == 5.5


# ---------------------------------------------------------------------------
# 5. Padding and truncation
# ---------------------------------------------------------------------------
class TestPaddingTruncation:
    def test_unknown_pack_padding_is_total_neutral(self):
        """The 14-line detail must NOT add ~15% on top of the market price."""
        # This id fuzzy-matches nothing (ratio ~0.45) → true unknown path,
        # priced via the metier median (u → 400 €).
        assert _find_pack("PRESTATION_SPECIALE_XYZ", EXACT_MAP, ALL_PACKS,
                          corps_metier="Climatisation – Ventilation – VMC") is None
        lots = [_ai_lot("Climatisation – Ventilation – VMC",
                        [_ai_pack("PRESTATION_SPECIALE_XYZ", 5, "unitaire", "5 unités")])]
        blocks = _run(lots)
        inter = _intervention_blocks(blocks)[0]
        lines = inter["lots"][0]["lignes"]
        assert len(lines) == 14
        assert _block_ht(inter) == pytest.approx(5 * 400.0, abs=1.0)

    def test_matched_pack_scaled_padding_stays_in_envelope(self):
        """Padding on a count-scaled matched pack must stay ~15%, not x-count."""
        lots = [_ai_lot("Climatisation – Ventilation – VMC",
                        [_ai_pack("CLIM-VMC-PACK-001", 5, "unitaire", "5 splits")])]
        blocks = _run(lots)
        inter = _intervention_blocks(blocks)[0]
        # Real work lines: depose 5x100 + pose 5x900 + mise en service 120 = 5120.
        real_total = 5 * 100 + 5 * 900 + 120
        # Padding must add at most ~16% (15% envelope + integer rounding).
        assert _block_ht(inter) <= real_total * 1.18
        assert _block_ht(inter) >= real_total

    def test_matched_pack_keeps_exactly_14_lines(self):
        lots = [_ai_lot("Climatisation – Ventilation – VMC",
                        [_ai_pack("CLIM-VMC-PACK-001", 2, "unitaire", "2 splits")])]
        blocks = _run(lots)
        assert len(_intervention_blocks(blocks)[0]["lots"][0]["lignes"]) == 14

    def test_truncation_keeps_highest_value_lines(self):
        lines = [
            {"designation": f"petite ligne {i}", "unite": "forfait", "quantite": 1,
             "pu_ht": 10.0, "tva": 10.0, "total_ht": 10.0}
            for i in range(19)
        ]
        # The expensive line sits at the END — the old positional truncation
        # would have merged it away.
        lines.append({"designation": "GROSSE FOURNITURE PRINCIPALE", "unite": "u",
                      "quantite": 1, "pu_ht": 5000.0, "tva": 10.0, "total_ht": 5000.0})
        out = _pad_or_truncate_lines(lines, 14, "Travaux et fournitures Test", 10.0, "Test")
        assert len(out) == 14
        assert any("GROSSE FOURNITURE" in l["designation"] for l in out)
        merged = out[-1]
        assert "complémentaires" in merged["designation"]
        # 7 small lines merged: 19 smalls kept 12 → dropped 7 x 10 €.
        assert merged["total_ht"] == pytest.approx(70.0)
        # Total preserved.
        assert sum(l["total_ht"] for l in out) == pytest.approx(5000.0 + 190.0)

    def test_mixed_depannage_prestation_targets(self):
        lots = [
            _ai_lot("Dépannage & Interventions rapides",
                    [_ai_pack("DEP-PLO-PACK-001", 1, "forfait", "fuite", "DEPANNAGE")], "LOT_01"),
            _ai_lot("Climatisation – Ventilation – VMC",
                    [_ai_pack("CLIM-VMC-PACK-001", 1, "unitaire", "1 split")], "LOT_02"),
        ]
        blocks = _run(lots)
        dep_block, clim_block = _intervention_blocks(blocks)
        assert len(dep_block["lots"][0]["lignes"]) == 3
        assert len(clim_block["lots"][0]["lignes"]) == 14
        # Mixed request → standard global blocks (3 lines each).
        assert len(blocks[0]["lots"][0]["lignes"]) == 3
        assert len(blocks[-1]["lots"][0]["lignes"]) == 3

    def test_all_depannage_keeps_compact_structure(self):
        lots = [_ai_lot("Dépannage & Interventions rapides",
                        [_ai_pack("DEP-PLO-PACK-001", 1, "forfait", "fuite", "DEPANNAGE")])]
        blocks = _run(lots)
        assert len(blocks[0]["lots"][0]["lignes"]) == 1
        assert len(_intervention_blocks(blocks)[0]["lots"][0]["lignes"]) == 3
        assert len(blocks[-1]["lots"][0]["lignes"]) == 1


# ---------------------------------------------------------------------------
# 6. Pack matching guard
# ---------------------------------------------------------------------------
class TestPackMatching:
    def test_exact_code_match(self):
        assert _find_pack("CLIM-VMC-PACK-001", EXACT_MAP, ALL_PACKS)["code_pack"] == "CLIM-VMC-PACK-001"

    def test_wrong_metier_global_fuzzy_rejected(self):
        """A mid-confidence invented id must not bind to another metier's pack.

        "climatisation murale" vs "Climatisation mono-split mural" has a
        difflib ratio of ~0.76: above the global cutoff (0.75) but below the
        near-certainty override (0.85), so metier compatibility is required.
        """
        found = _find_pack(
            "climatisation murale",
            EXACT_MAP, ALL_PACKS,
            corps_metier="Maçonnerie – Gros œuvre",
        )
        assert found is None

    def test_same_metier_fuzzy_accepted(self):
        found = _find_pack(
            "climatisation murale",
            EXACT_MAP, ALL_PACKS,
            corps_metier="Climatisation – Ventilation – VMC",
        )
        assert found is not None
        assert found["code_pack"] == "CLIM-VMC-PACK-001"

    def test_near_certain_global_fuzzy_accepted_despite_metier(self):
        """A ~0.98 confidence name match overrides a sloppy lot metier label."""
        found = _find_pack(
            "climatisation mono split murale",
            EXACT_MAP, ALL_PACKS,
            corps_metier="Maçonnerie – Gros œuvre",
        )
        assert found is not None
        assert found["code_pack"] == "CLIM-VMC-PACK-001"


# ---------------------------------------------------------------------------
# 7. Prices
# ---------------------------------------------------------------------------
class TestPrices:
    def test_zero_price_pack_line_resolved(self):
        pack = _mk_pack(
            "TST-PACK-001", "Peinture test", "Peinture – Finitions – Enduits décoratifs",
            [{"bloc": 3, "unite": "m²", "quantite": 1, "prix_unitaire_ht": 0,
              "taux_tva_defaut": 10, "designation": "Peinture de finition"}],
        )
        maps = ({pack["code_pack"]: pack}, [pack])
        lots = [_ai_lot("Peinture – Finitions – Enduits décoratifs",
                        [_ai_pack("TST-PACK-001", 30, "surface_m2", "30 m²")])]
        blocks = process_ai_lots(
            lots, surface_m2=30, user_text="30 m²",
            price_map={}, concept_map=CONCEPT_MAP, metier_medians=METIER_MEDIANS,
            packs_maps=maps,
        )
        main = next(l for l in _intervention_blocks(blocks)[0]["lots"][0]["lignes"]
                    if "finition" in l["description"])
        assert main["pu"] > 0  # resolved via concept "peinture" → 20 €/m²

    def test_sanity_clamp(self):
        clamped = _sanity_clamp(
            50_000.0, "u",
            corps_metier="Climatisation – Ventilation – VMC",
            metier_medians=METIER_MEDIANS, context="test",
        )
        assert clamped == 400.0 * 8

    def test_totals_consistency(self):
        lots = [_ai_lot("Climatisation – Ventilation – VMC",
                        [_ai_pack("CLIM-VMC-PACK-001", 3, "unitaire", "3 splits")])]
        blocks = _run(lots)
        flat = _flat(blocks)
        totals = calculate_global_totals(flat)
        assert totals["total_ht"] == pytest.approx(sum(l["ht"] for l in flat), abs=0.5)
        assert totals["total_ttc"] >= totals["total_ht"]
        # Every line's ttc must equal ht x (1 + tva).
        for l in flat:
            assert l["ttc"] == pytest.approx(l["ht"] * (1 + l["tva"] / 100), abs=0.02)


# ---------------------------------------------------------------------------
# 8. Duration estimate
# ---------------------------------------------------------------------------
class TestDuration:
    def test_small_depannage_is_one_day(self):
        assert estimate_duration_days(250.0) == 1

    def test_midsize_project(self):
        blocks = [{"title": "prep"}, {"title": "inter"}, {"title": "fin"}]
        assert estimate_duration_days(7000.0, blocks) == math.ceil(7000 / 700)

    def test_clamped_to_90(self):
        assert estimate_duration_days(1_000_000.0) == 90

    def test_zero_total(self):
        assert estimate_duration_days(0.0) == 1


# ---------------------------------------------------------------------------
# 9. AI payload sanitation + routing guards (ai_service helpers)
# ---------------------------------------------------------------------------
class TestAiServiceHelpers:
    def test_sanitize_dedupes_and_coerces(self):
        from app.services.ai_service import _sanitize_ai_lots

        lots = _sanitize_ai_lots([
            {
                "lot_key": "LOT_01",
                "metier": "Peinture",
                "zone": "interieur",
                "packs": [
                    {"id": "PEI-1", "type": "PRESTATION", "quantite": "3",
                     "quantite_type": "unitaire", "source_qte": "3 portes"},
                    {"id": "PEI-1", "type": "PRESTATION", "quantite": 3,
                     "quantite_type": "unitaire", "source_qte": "3 portes"},  # duplicate
                    {"id": "PEI-2", "type": "PRESTATION", "quantite": -4,
                     "quantite_type": "surface_m2", "source_qte": "?"},  # invalid qty
                    {"id": "PEI-3", "type": "PRESTATION", "quantite": 99_999,
                     "quantite_type": "surface_m2", "source_qte": "?"},  # absurd qty
                    {"id": "", "type": "PRESTATION", "quantite": 1,
                     "quantite_type": "forfait", "source_qte": "x"},  # empty id
                ],
            },
            {"lot_key": "LOT_02", "metier": "Vide", "zone": "interieur", "packs": []},
        ])
        assert len(lots) == 1
        packs = lots[0]["packs"]
        assert [p["id"] for p in packs] == ["PEI-1", "PEI-2", "PEI-3"]
        assert packs[0]["quantite"] == 3.0
        assert packs[1]["quantite"] == 1.0 and packs[1]["quantite_type"] == "non_specifie"
        assert packs[2]["quantite"] == 1.0 and packs[2]["quantite_type"] == "non_specifie"

    def test_depannage_guard_blocks_renovation_texts(self):
        from app.services.ai_service import _is_depannage_request

        # Pure repair → depannage catalog.
        assert _is_depannage_request("fuite sous l'évier, urgent") is True
        # Renovation mentioning a broken element → standard catalog.
        assert _is_depannage_request(
            "rénovation complète de la salle de bain, remplacer le carrelage cassé"
        ) is False
        # Big-surface project with a repair word → standard catalog.
        assert _is_depannage_request("réparer la toiture sur 80 m²") is False

    def test_metier_hints_detection(self):
        from app.services.ai_service import _detect_metier_hints

        hints = _detect_metier_hints(
            "élévation des murs en parpaings, chaînages et linteaux"
        )
        assert "Maçonnerie – Gros œuvre" in hints

    def test_semantic_cache_key_is_normalised(self):
        from app.services.ai_service import _semantic_cache_key

        assert _semantic_cache_key("Peinture  Salon 20m²") == \
            _semantic_cache_key("peinture salon 20M²")
        assert _semantic_cache_key("Rénovation") == _semantic_cache_key("renovation")

    def test_semantic_cache_roundtrip_and_isolation(self):
        from app.services.ai_service import (
            _semantic_cache_get,
            _semantic_cache_key,
            _semantic_cache_put,
        )

        key = _semantic_cache_key("test cache roundtrip xyz")
        payload = {"is_btp": True, "lots": [{"metier": "Peinture", "packs": []}]}
        _semantic_cache_put(key, payload)
        got = _semantic_cache_get(key)
        assert got == payload
        # Deep-copied: mutating the returned object must not corrupt the cache.
        got["lots"][0]["metier"] = "MUTATED"
        assert _semantic_cache_get(key)["lots"][0]["metier"] == "Peinture"
        assert _semantic_cache_get(_semantic_cache_key("autre demande")) is None


# ---------------------------------------------------------------------------
# 10. Forbidden generic labels — detect + local rewrite fallback
# ---------------------------------------------------------------------------
class TestForbiddenLineRewrite:
    def test_detector_catches_autres_divers_complementaires(self):
        from app.services.ai_service import _is_forbidden_line_description

        assert _is_forbidden_line_description(
            "Autres travaux et fournitures climatisation – ventilation – vmc et finitions"
        )
        assert _is_forbidden_line_description(
            "Ensemble de prestations complémentaires — Terrassement – VRD (15 postes regroupés)"
        )
        assert _is_forbidden_line_description("Divers travaux annexes")
        assert _is_forbidden_line_description("Ajustement forfaitaire de finition")
        assert _is_forbidden_line_description("Montant d'équilibrage du lot")
        assert not _is_forbidden_line_description(
            "Fourniture et pose de climatiseur mural monosplit"
        )
        assert not _is_forbidden_line_description(
            "Remblaiement en couches successives avec compactage"
        )

    def test_local_rewrite_removes_forbidden_words(self):
        from app.services.ai_service import (
            _is_forbidden_line_description,
            _local_rewrite_forbidden_label,
        )

        samples = [
            "Autres travaux et fournitures climatisation – ventilation – vmc et finitions",
            "Ensemble de prestations complémentaires — Terrassement – VRD – Assainissement (15 postes regroupés)",
            "Autres mise en place, balisage et protection du chantier et finitions",
            "Divers",
        ]
        for sample in samples:
            rewritten = _local_rewrite_forbidden_label(sample)
            assert rewritten.strip()
            assert not _is_forbidden_line_description(rewritten), rewritten

    def test_rewrite_pass_mutates_only_descriptions(self):
        """Engine totals stay intact; only forbidden descriptions are replaced."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from app.services.ai_service import AIService, _line_rewrite_cache

        _line_rewrite_cache.clear()
        blocs = [
            {
                "title": "Climatisation – Ventilation – VMC",
                "lots": [
                    {
                        "title": "Travaux principaux",
                        "lignes": [
                            {
                                "num": 1,
                                "description": "Pose split mural",
                                "qte": 1,
                                "unit": "u",
                                "pu": 900,
                                "tva": 10,
                                "ht": 900,
                                "ttc": 990,
                            },
                            {
                                "num": 2,
                                "description": (
                                    "Autres travaux et fournitures climatisation "
                                    "– ventilation – vmc et finitions"
                                ),
                                "qte": 1,
                                "unit": "forfait",
                                "pu": 500,
                                "tva": 10,
                                "ht": 500,
                                "ttc": 550,
                            },
                        ],
                    }
                ],
            }
        ]

        svc = AIService.__new__(AIService)
        # Force the GPT path to fail so we exercise the local fallback without
        # needing an API key — production still calls GPT-4 first.
        svc._client = MagicMock()
        svc._client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("no network in unit test")
        )

        out = asyncio.run(svc._rewrite_forbidden_line_labels(blocs))
        lines = out[0]["lots"][0]["lignes"]
        assert lines[0]["description"] == "Pose split mural"
        assert lines[0]["ht"] == 900
        assert lines[1]["ht"] == 500
        assert lines[1]["pu"] == 500
        assert "Autres" not in lines[1]["description"]
        assert "Divers" not in lines[1]["description"]
        assert "complémentaires" not in lines[1]["description"].lower()
