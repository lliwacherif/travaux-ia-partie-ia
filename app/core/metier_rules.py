import math
import re

# Safely evaluate math formulas from the rules strings
def safe_eval_formula(formula: str, variables: dict) -> float:
    """
    Evaluates a math formula string safely using only basic math operators
    and allowed variables (e.g., surface, longueur, hauteur).
    """
    allowed_names = {
        "sqrt": math.sqrt,
        "ceil": math.ceil,
        **variables
    }
    
    # We strip any weird chars just in case, though the formulas are hardcoded
    clean_formula = re.sub(r'[^a-zA-Z0-9_*\/()+\.\- ]', '', formula)
    
    try:
        # __builtins__: None ensures no arbitrary code can be run
        return float(eval(clean_formula, {"__builtins__": None}, allowed_names))
    except Exception as e:
        print(f"Error evaluating formula '{formula}' with {variables}: {e}")
        return 0.0

# ═══════════════════════════════════════════════════════════════
# 🧱 1. MAÇONNERIE – GROS ŒUVRE
# ═══════════════════════════════════════════════════════════════
REGLES_MACONNERIE_GROS_OEUVRE = {
    "metier": "Maçonnerie – Gros œuvre",
    "code": "MAC-GO",
    "rules": {
        "DALLE_BETON": {
            "surface_m2": {"formula": "surface", "unit": "m²", "description": "Surface de la dalle"},
            "beton_m3": {"formula": "surface * 0.12", "unit": "m³", "description": "Volume béton épaisseur 12cm"},
            "treillis_m2": {"formula": "surface", "unit": "m²", "description": "Treillis soudé"},
            "coffrage_ml": {"formula": "4 * sqrt(surface)", "unit": "ml", "description": "Périmètre coffrage"},
            "polyane_m2": {"formula": "surface", "unit": "m²", "description": "Film polyane"}
        },
        "MUR_PARPAINGS": {
            "surface_m2": {"formula": "longueur * hauteur", "unit": "m²", "description": "Surface du mur"},
            "blocs_u": {"formula": "(longueur * hauteur) * 10", "unit": "u", "description": "10 blocs/m²"},
            "mortier_m3": {"formula": "(longueur * hauteur) * 0.015", "unit": "m³", "description": "Mortier joints"},
            "chainages_ml": {"formula": "longueur * 1.1", "unit": "ml", "description": "Chaînages +10%"}
        }
    }
}

# ═══════════════════════════════════════════════════════════════
# 🏗️ 5. PLÂTRERIE – CLOISONS – DOUBLAGES – FAUX PLAFONDS
# ═══════════════════════════════════════════════════════════════
REGLES_PLATRERIE = {
    "metier": "Plâtrerie – Cloisons – Doublages – Faux plafonds",
    "code": "PLA",
    "rules": {
        "CLOISON_DISTRIBUTION": {
            "surface_m2": {"formula": "longueur * hauteur", "unit": "m²", "description": "Surface de cloison"},
            "placo_m2": {"formula": "(longueur * hauteur) * 2.1", "unit": "m²", "description": "BA13 double face + 5% chutes"},
            "rail_ml": {"formula": "(longueur * 2) + (longueur * hauteur / 0.60)", "unit": "ml", "description": "Rails hauts/bas + montants tous les 60cm"},
            "isolant_m2": {"formula": "longueur * hauteur", "unit": "m²", "description": "Laine minérale intérieur"},
            "bandes_ml": {"formula": "(longueur * hauteur) * 1.5", "unit": "ml", "description": "Bandes à joints"},
            "enduit_kg": {"formula": "(longueur * hauteur) * 0.5", "unit": "kg", "description": "Enduit joint ~0.5kg/m²"}
        },
        "FAUX_PLAFOND": {
            "surface_m2": {"formula": "surface", "unit": "m²", "description": "Surface plafond"},
            "placo_m2": {"formula": "surface * 1.05", "unit": "m²", "description": "BA13 +5% chutes"},
            "ossature_m2": {"formula": "surface", "unit": "m²", "description": "Ossature suspendue F530"},
            "suspentes_u": {"formula": "surface * 2.5", "unit": "u", "description": "~2.5 suspentes/m²"},
            "bandes_ml": {"formula": "4 * sqrt(surface)", "unit": "ml"}
        }
    }
}

# ═══════════════════════════════════════════════════════════════
# 🔷 17. CARRELAGE – SOLS & MURS
# ═══════════════════════════════════════════════════════════════
REGLES_CARRELAGE = {
    "metier": "Carrelage – Sols & Murs",
    "code": "CAR",
    "rules": {
        "CARRELAGE_SOL": {
            "carrelage_m2": {"formula": "surface * 1.10", "unit": "m²", "description": "+10% coupes/chutes"},
            "colle_kg": {"formula": "surface * 5", "unit": "kg", "description": "Colle ~5kg/m²"},
            "joint_kg": {"formula": "surface * 0.5", "unit": "kg", "description": "Joint ~0.5kg/m²"},
            "croisillons_u": {"formula": "surface * 10", "unit": "u", "description": "~10/m²"},
            "primaire_l": {"formula": "surface / 10", "unit": "l"}
        },
        "FAIENCE_MURALE": {
            "faience_m2": {"formula": "surface * 1.15", "unit": "m²", "description": "+15% coupes murales"},
            "colle_kg": {"formula": "surface * 4", "unit": "kg", "description": "Colle mur ~4kg/m²"},
            "joint_kg": {"formula": "surface * 0.5", "unit": "kg"},
            "croisillons_u": {"formula": "surface * 10", "unit": "u"},
            "profiles_ml": {"formula": "4 * sqrt(surface) * 0.5", "unit": "ml", "description": "Profils d'angle"}
        }
    }
}

# Combine all core MVP rules into one dict
ALL_METIER_RULES = {
    "MAC-GO": REGLES_MACONNERIE_GROS_OEUVRE,
    "PLA": REGLES_PLATRERIE,
    "CAR": REGLES_CARRELAGE
}
