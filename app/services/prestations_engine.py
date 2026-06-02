import logging
from typing import Any, Dict, List
from app.core.metier_rules import ALL_METIER_RULES, safe_eval_formula

logger = logging.getLogger(__name__)

def _get_mock_price(unit: str) -> float:
    """Provides a dummy price for MVP demonstration."""
    if unit == "m²": return 45.0
    if unit == "m³": return 120.0
    if unit == "ml": return 15.0
    if unit == "kg": return 5.0
    if unit == "u": return 30.0
    if unit == "l": return 10.0
    return 50.0

def _get_tva(metier: str, designation: str, client_type: str, project_nature: str) -> float:
    # 1. 5.5% if isolation
    metier_lower = metier.lower()
    designation_lower = designation.lower()
    if "isolation" in metier_lower or "isolation" in designation_lower or "laine" in designation_lower or "énergétique" in designation_lower:
        return 5.5
        
    # 2. 20% if neuf or pro
    if project_nature.lower() == "neuf" or client_type.lower() == "pro":
        return 20.0
        
    # 3. 10% by default
    return 10.0

def _pad_or_truncate_lines(lines: List[Dict[str, Any]], target_count: int, default_designation: str, tva: float) -> List[Dict[str, Any]]:
    """Enforces exactly target_count lines. Sums prices if truncated, injects 0€ lines if padded."""
    if target_count <= 0:
        return []
        
    if len(lines) > target_count:
        if target_count == 1:
            total_ht = round(sum(l.get("total_ht", 0) for l in lines), 2)
            return [{
                "designation": lines[0].get("designation", default_designation),
                "unite": "forfait",
                "quantite": 1,
                "pu_ht": total_ht,
                "tva": tva,
                "total_ht": total_ht
            }]
            
        kept = lines[:target_count-1]
        dropped = lines[target_count-1:]
        dropped_ht = round(sum(l.get("total_ht", 0) for l in dropped), 2)
        kept.append({
            "designation": f"Autres {default_designation.lower()} et finitions",
            "unite": "forfait",
            "quantite": 1,
            "pu_ht": dropped_ht,
            "tva": tva,
            "total_ht": dropped_ht
        })
        return kept
    elif len(lines) < target_count:
        needed = target_count - len(lines)
        padded = list(lines)
        
        # Pick specific generic labels based on the default designation to avoid nonsensical phrasing
        if "Mise en place" in default_designation:
            generic_labels = [
                "Balisage et sécurisation de la zone de travail",
                "Mise en place des protections au sol et murales",
                "Acheminement de l'outillage et préparation du poste",
                "Vérification des supports et repérages initiaux"
            ]
        elif "Nettoyage" in default_designation:
            generic_labels = [
                "Évacuation des gravats et déchets résiduels",
                "Nettoyage approfondi de la zone d'intervention",
                "Retrait des protections et remise en ordre",
                "Réception technique et contrôle final"
            ]
        else:
            generic_labels = [
                "Préparation et traitement ponctuel du support",
                "Découpes, ajustements et façonnage sur mesure",
                "Fourniture des petits consommables et fixations",
                "Vérification des niveaux, aplombs et équerrages",
                "Manutention et approvisionnement à pied d'œuvre",
                "Traitement des joints et raccords",
                "Protection spécifique des ouvrages attenants",
                "Contrôle qualité et essais de fonctionnement",
                "Acheminement et gestion des gravats intermédiaires",
                "Finition et retouches de peinture"
            ]
        
        for i in range(needed):
            label_suffix = generic_labels[i] if i < len(generic_labels) else f"Prestation annexe {i+1}"
            padded.append({
                "designation": f"{default_designation} - {label_suffix}",
                "unite": "forfait",
                "quantite": 1,
                "pu_ht": 0.0,
                "tva": tva,
                "total_ht": 0.0
            })
        return padded
    return lines

def process_ai_lots(lots: List[Dict[str, Any]], client_type: str = "particulier", project_nature: str = "renovation") -> List[Dict[str, Any]]:
    """
    Takes the pure semantic AI JSON, evaluates rules, and structures it into
    the strict K+2 blocs architecture with exact line counts.
    """
    if not lots:
        return []

    # Determine global type based on the first pack of the first lot
    global_type = "PRESTATION"
    for lot in lots:
        for pack in lot.get("packs", []):
            if pack.get("type", "").upper() == "DEPANNAGE":
                global_type = "DEPANNAGE"
                break

    is_depannage = (global_type == "DEPANNAGE")
    target_mise_en_place = 1 if is_depannage else 3
    target_intervention = 3 if is_depannage else 14
    target_finition = 1 if is_depannage else 3
    
    global_mise_en_place_lines = []
    global_finition_lines = []
    intervention_blocks = []
    
    for lot in lots:
        metier = lot.get("metier", "Métier inconnu")
        lot_key = lot.get("lot_key", "LOT_01")
        packs = lot.get("packs", [])
        tva = _get_tva(metier, "", client_type, project_nature)
        
        matched_rules = next((rules for code, rules in ALL_METIER_RULES.items() if rules["metier"].lower() in metier.lower()), None)
        
        lot_intervention_lines = []
        
        for pack in packs:
            pack_id = pack.get("id", "INCONNU")
            quantite_brute = pack.get("quantite", 1)
            
            pack_lines_def = None
            if matched_rules and pack_id in matched_rules["rules"]:
                pack_lines_def = matched_rules["rules"][pack_id]
            
            if pack_lines_def:
                for line_key, rule in pack_lines_def.items():
                    # Format designation nicely: e.g. "carrelage_m2 (+10% coupes/chutes)"
                    desc = rule.get("description", "")
                    clean_key = line_key.replace("_m2", "").replace("_ml", "").replace("_u", "").replace("_kg", "").replace("_l", "").replace("_m3", "").capitalize()
                    designation = f"{clean_key} ({desc})" if desc else clean_key
                    tva = _get_tva(metier, designation, client_type, project_nature)
                    
                    qte_calc = safe_eval_formula(rule["formula"], {"surface": quantite_brute, "longueur": quantite_brute, "hauteur": 2.5})
                    pu_ht = _get_mock_price(rule["unit"])
                    total_ht = round(qte_calc * pu_ht, 2)
                    
                    line_data = {
                        "designation": designation,
                        "unite": rule["unit"],
                        "quantite": round(qte_calc, 2),
                        "pu_ht": pu_ht,
                        "tva": tva,
                        "total_ht": total_ht
                    }
                    
                    # Heuristics to dispatch into global vs intervention
                    key_lower = line_key.lower()
                    if "nettoyage" in key_lower or "repli" in key_lower:
                        global_finition_lines.append(line_data)
                    elif "protection" in key_lower or "installation" in key_lower and "chantier" in key_lower:
                        global_mise_en_place_lines.append(line_data)
                    else:
                        lot_intervention_lines.append(line_data)
            else:
                fallback_designation = f"Prestation: {pack_id}"
                tva = _get_tva(metier, fallback_designation, client_type, project_nature)
                lot_intervention_lines.append({
                    "designation": fallback_designation,
                    "unite": "forfait",
                    "quantite": 1,
                    "pu_ht": 150.0,
                    "tva": tva,
                    "total_ht": 150.0
                })
        
        # Enforce exact line count for THIS intervention block
        base_tva = _get_tva(metier, "", client_type, project_nature)
        lot_intervention_lines = _pad_or_truncate_lines(
            lot_intervention_lines, 
            target_intervention, 
            f"Travaux et fournitures {metier}",
            base_tva
        )
        
        intervention_blocks.append({
            "title": f"Intervention: {metier}",
            "lines": lot_intervention_lines
        })
        
    # Enforce exact line counts for global blocks
    global_tva = _get_tva("", "", client_type, project_nature)
    global_mise_en_place_lines = _pad_or_truncate_lines(
        global_mise_en_place_lines,
        target_mise_en_place,
        "Mise en place, balisage et protection du chantier",
        global_tva
    )
    
    global_finition_lines = _pad_or_truncate_lines(
        global_finition_lines,
        target_finition,
        "Nettoyage fin de chantier et repli",
        global_tva
    )

    # Assemble the final strict structure
    final_blocks = []
    
    # 1. Mise en place
    final_blocks.append({
        "title": "Mise en place et préparation",
        "sub_categories": [{"sub_label": "Préparation", "lines": global_mise_en_place_lines}],
        "total_lot_ht": round(sum(l.get("total_ht", 0) for l in global_mise_en_place_lines), 2)
    })
    
    # 2..K. Interventions
    for block in intervention_blocks:
        final_blocks.append({
            "title": block["title"],
            "sub_categories": [{"sub_label": "Travaux principaux", "lines": block["lines"]}],
            "total_lot_ht": round(sum(l.get("total_ht", 0) for l in block["lines"]), 2)
        })
        
    # K+1. Finition
    final_blocks.append({
        "title": "Finition et nettoyage",
        "sub_categories": [{"sub_label": "Nettoyage", "lines": global_finition_lines}],
        "total_lot_ht": round(sum(l.get("total_ht", 0) for l in global_finition_lines), 2)
    })
    
    return final_blocks

def calculate_global_totals(lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculates global TVA, HT and TTC."""
    tva_groups = {}
    
    for line in lines:
        rate = line.get("tva", 20.0)
        tva_groups[rate] = tva_groups.get(rate, 0) + line.get("total_ht", 0)
        
    tva_breakdown = {}
    total_tva = 0
    
    for rate, base_ht in tva_groups.items():
        amount = round((base_ht * rate) / 100, 2)
        tva_breakdown[str(rate)] = {
            "base_ht": round(base_ht, 2),
            "tva_amount": amount
        }
        total_tva += amount
        
    total_ht = round(sum(base for base in tva_groups.values()), 2)
    total_ttc = round(total_ht + total_tva, 2)
    
    return {
        "total_ht": total_ht,
        "total_tva": round(total_tva, 2),
        "total_ttc": total_ttc,
        "tva_breakdown": tva_breakdown
    }
