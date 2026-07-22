# Analyse du système de TVA dans le Générateur de Devis

Cette analyse détaille la manière dont la TVA est gérée, calculée, validée et réparée au sein du pipeline de génération de devis. Le système repose sur une approche hybride : l'IA génère les structures de base, mais les règles de gestion (taux applicables) et les mathématiques (HT, TTC) sont rigoureusement contrôlées par le code backend.

## 1. Taux de TVA Supportés et Règles Métier

Le système gère trois taux de TVA principaux, documentés explicitement dans le schéma `TradeLineItem` (`app/schemas/trade_line.py`) :
* **5.5%** : Travaux de rénovation énergétique.
* **10.0%** : Travaux de rénovation standard.
* **20.0%** : Travaux pour des professionnels (B2B) ou du neuf.

### Logique d'affectation (`decide_tva_finale`)
Dans `app/services/prestations_engine.py`, la fonction `decide_tva_finale` agit comme la source de vérité pour déterminer le taux de TVA d'une ligne de prestation. Elle se base sur trois paramètres : la désignation de la ligne, le nom du lot et le type de client.

```python
def decide_tva_finale(designation: str, lot_label: str, client_type: str) -> float:
    # 1. Vérification des travaux de rénovation énergétique (TVA 5.5%)
    # Basée sur la liste ISOLATION_TVA_KEYWORDS (ex: "isolation", "laine", "ouate", "ite"...)
    is_isolation_lot = "isolat" in lot
    is_isolation_line = any(kw in text for kw in ISOLATION_TVA_KEYWORDS)
    if is_isolation_lot or is_isolation_line:
        return 5.5
        
    # 2. Vérification du type de client (TVA 20% pour les pros)
    if client_type == "professionnel" or client_type == "pro":
        return 20.0
        
    # 3. Taux par défaut pour les particuliers (TVA 10% - Rénovation)
    return 10.0
```

Cette fonction est appelée lors du "padding" ou de la création de nouvelles lignes, garantissant que le bon taux de TVA est toujours appliqué, indépendamment de ce que l'IA pourrait imaginer.

## 2. Déduction Intelligente pour l'Upsell

Le module d'Upsell (`app/services/upsell_engine.py`) ajoute dynamiquement de nouvelles lignes à un devis existant. Pour garantir la cohérence d'un lot, le système utilise une approche statistique pour affecter la TVA à ces nouvelles lignes, via la fonction `_dominant_tva`.

Plutôt que d'appliquer une règle globale, la fonction `_dominant_tva` observe les lignes *existantes* du lot et sélectionne le taux de TVA le plus fréquent (le mode statistique). 
* Si le lot est principalement composé de travaux énergétiques (5.5%), la nouvelle ligne héritera de ce taux.
* En cas d'échec ou d'absence de lignes, le système se rabat (fallback) sur la norme la plus courante (10.0%).

## 3. Mécanisme d'Auto-Réparation (Self-Healing)

Les LLM sont notoirement mauvais pour les calculs mathématiques exacts. C'est pourquoi le système intègre un module dédié `app/services/devis_repair.py` pour sanctuariser les calculs financiers (HT, TVA, TTC).

### Recalcul Systématique
Au lieu de faire confiance aux totaux `ht` et `ttc` générés par l'IA dans la structure JSON, la fonction `_try_repair_ligne` recalcule systématiquement ces valeurs si elles sont absentes ou mal formatées :

1. `ht = round(qte * pu, 2)`
2. `ttc = round(ht * (1.0 + tva / 100.0), 2)`

> [!NOTE]
> Le système s'assure que les champs requis (num, description, qte, unit, pu, tva) sont présents. Les champs dérivés (`ht`, `ttc`) sont recréés à la volée.

### Agrégation des Totaux
La fonction `recompute_montant_ttc` itère ensuite sur toutes les lignes réparées pour additionner les TTC de manière fiable, et réécrit le champ principal `devis["montant_ttc"]`. Le montant total annoncé au client correspond donc toujours au centime près à la somme des lignes du devis.

## 4. Ventilation de la TVA (Breakdown)

Enfin, pour l'affichage frontal, le système calcule la ventilation de la TVA (`tva_breakdown`) via `calculate_global_totals` dans `prestations_engine.py`. 

Pour chaque ligne de chaque lot :
* Le système additionne les bases HT par taux de TVA.
* Il calcule et additionne les montants de TVA.
* Il agrège le tout dans un objet `tva_breakdown` indexé par le taux (ex: `"10.0": {"base_ht": 1000.0, "tva_amount": 100.0}`).

## Conclusion de l'Analyse

Le système de TVA du générateur de devis est extrêmement robuste. Il isole parfaitement la "créativité" du LLM (qui génère les désignations, unités et prix) de la "rigueur" nécessaire à la facturation (qui est hardcodée en Python). L'IA est utilisée pour déterminer la nature des travaux, mais c'est le moteur Python qui décide formellement du taux applicable (via `ISOLATION_TVA_KEYWORDS`), recalcule les taxes avec une précision à deux décimales, et agrège les montants totaux.

## 5. Risques et Limites de la Règle des 5.5% (Isolation)

La méthode consistant à appliquer 5.5% de TVA sur un lot entier ou sur de simples mots-clés (`is_isolation_lot` ou `is_isolation_line`) présente plusieurs risques métiers, fiscaux et techniques :

1. **Contagion au sein d'un lot mixte** : Comme vous l'avez souligné, si le nom du lot contient "isolation", **toutes** les lignes de ce lot passeront à 5.5%, y compris les travaux non éligibles (comme la peinture décorative, l'évacuation de gravats non liés, ou la plomberie standard). Or, la TVA à 5.5% s'applique strictement aux matériaux d'amélioration énergétique et à la main-d'œuvre qui y est directement associée (travaux induits indispensables).
2. **Faux positifs sémantiques** : La liste `ISOLATION_TVA_KEYWORDS` contient des mots comme "laine" ou "isolant". Cela peut déclencher une TVA à 5.5% sur des prestations sans rapport avec la rénovation thermique éligible (ex: "Pose de moquette en laine", "Peinture d'isolation phonique").
3. **Absence de vérification de l'éligibilité du bâtiment** : La TVA réduite (5.5% ou 10%) n'est légalement applicable qu'aux logements (résidences principales ou secondaires) achevés depuis plus de 2 ans. Le système actuel ne vérifie que si le client est "pro" (pour forcer à 20%), sans prendre en compte l'âge ou la nature du bâtiment (tertiaire vs résidentiel) pour les particuliers.
4. **Conditions strictes du taux réduit (Certification RGE)** : Le taux de 5.5% nécessite que les matériaux (isolants, pompes à chaleur, etc.) atteignent des critères de performance énergétique (ex: résistance thermique R minimale) et que les travaux soient parfois réalisés par une entreprise RGE. Le code accorde les 5.5% sur simple détection lexicale, générant potentiellement un devis inapplicable fiscalement.
5. **Risque de redressement fiscal pour l'artisan** : Appliquer abusivement une TVA à 5.5% au lieu de 10% ou 20% expose directement le professionnel émetteur du devis (l'artisan) à un redressement fiscal sur le différentiel (4.5% ou 14.5%) réclamé par l'État, en plus d'éventuelles pénalités.
6. **Rejet des dossiers d'aides (MaPrimeRénov', CEE)** : Un devis confus (lot mixte où tout est à 5.5%) entraînera presque systématiquement le rejet du dossier de subvention du client par l'Anah ou les délégataires des primes énergie, car ils exigent une séparation très claire des travaux éligibles et non-éligibles.
7. **Amplification par l'Upsell (`_dominant_tva`)** : Si un lot est faussement "pollué" majoritairement par un taux à 5.5% dû au nom du lot, l'algorithme d'upsell (`_dominant_tva`) héritera de ce taux dominant pour toutes les nouvelles prestations qu'il proposera d'ajouter à ce lot, propageant et aggravant l'erreur.
