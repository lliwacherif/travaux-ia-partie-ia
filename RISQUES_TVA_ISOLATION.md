# Risques et Limites de la Règle de TVA à 5.5% (Isolation)

## 1. Risques Architecturaux et Systémiques

D'un point de vue purement technique et de conception logicielle, cette approche "basée sur des mots-clés" introduit de réelles vulnérabilités :

* **L'Anti-Pattern du "Couplage Texte Libre / Logique Stricte"** : Le système utilise une chaîne de caractères en langage naturel et probabiliste (générée par le LLM) pour déclencher une règle métier fiscale stricte. Si le LLM formule "Mise en place de calfeutrement thermique" au lieu de "Pose d'isolation", la détection échoue et la TVA passe à 10%. Le comportement financier devient instable et dépendant du vocabulaire du modèle.
* **Rupture de la Source Unique de Vérité (Single Source of Truth)** : Le système possède une base de données de prestations. Normalement, c'est cette base qui devrait dicter la TVA (ex: cet article a une TVA à 5.5%). La fonction `decide_tva_finale` agit comme un "override" global qui court-circuite la base. Il y a donc deux sources de vérité concurrentes, ce qui nuit à la maintenabilité.
* **Effet "Boîte de Pétri" dans le moteur d'Upsell** : Le module d'upsell utilise une inférence statistique (`_dominant_tva`). Le système injecte son propre "bruit" (les 5.5% affectés par erreur sur un lot) dans l'algorithme d'upsell, qui va amplifier cette anomalie en générant de nouvelles lignes annexes avec la mauvaise donnée. C'est une boucle d'erreur auto-entretenue.
* **Dette Technique sur les Tests End-to-End (Flakiness)** : Tester la logique de facturation de bout en bout devient instable. Un test pourra passer un jour et échouer le lendemain simplement parce que le LLM a utilisé un synonyme (valide sémantiquement) absent de la liste `ISOLATION_TVA_KEYWORDS`.

## 2. Risques Métiers, Fiscaux et Opérationnels

Au-delà de l'architecture logicielle, cette méthode présente d'importants risques d'application pour la plateforme et les artisans :


1. **Contagion au sein d'un lot mixte**
Si le nom du lot généré par l'IA contient le terme "isolation", **toutes** les lignes de ce lot se verront appliquer la TVA à 5.5%. Cela inclut les travaux non éligibles facturés dans le même lot (peinture décorative de finition, évacuation de gravats non liés, plomberie standard). Légalement, la TVA à 5.5% s'applique strictement aux matériaux d'amélioration énergétique et à la main-d'œuvre qui y est directement associée (travaux induits indispensables).

2. **Faux positifs sémantiques (Les homonymes)**
La liste `ISOLATION_TVA_KEYWORDS` contient des mots ambigus comme "laine" ou "isolant". Cela déclenche une TVA à 5.5% sur des prestations n'ayant aucun rapport avec la rénovation thermique éligible, par exemple : "Pose de moquette en laine" ou "Peinture d'isolation phonique".

3. **Absence de vérification de l'éligibilité du bâtiment**
La TVA réduite (5.5% ou 10%) n'est légalement applicable qu'aux logements (résidences principales ou secondaires) achevés depuis plus de 2 ans. Le système actuel ne vérifie que si le client est "pro" (pour forcer à 20%), sans jamais prendre en compte l'âge ou la nature du bâtiment (tertiaire vs résidentiel) pour les particuliers. Un particulier construisant dans le neuf se verra attribuer 5.5% à tort.

4. **Ignorance des conditions strictes du taux réduit (Certification RGE)**
L'application du taux de 5.5% exige souvent que l'artisan détienne la certification RGE (Reconnu Garant de l'Environnement) et que les matériaux (isolants) atteignent des critères de performance énergétique précis (résistance thermique R minimale). Le code accordant les 5.5% sur simple détection lexicale, il génère des devis juridiquement inapplicables pour des artisans non certifiés ou pour des matériaux standards.

5. **Risque de redressement fiscal pour l'artisan**
L'artisan émetteur du devis est le seul responsable juridiquement. Fournir un outil qui applique de manière "magique" et abusive une TVA à 5.5% au lieu de 10% ou 20% l'expose directement à un redressement fiscal sur le différentiel (4.5% ou 14.5%) réclamé par l'administration fiscale, en plus des pénalités.

6. **Rejet systématique des dossiers d'aides (MaPrimeRénov', CEE)**
Un devis confus (ex: lot mixte où toutes les prestations héritent du 5.5%) entraînera presque systématiquement le rejet du dossier de subvention du client par l'Anah ou les délégataires des primes énergie. Ces organismes exigent une séparation très stricte et explicite des travaux éligibles et non-éligibles.

7. **Amplification de l'erreur par l'Upsell (`_dominant_tva`)**
Si un lot est faussement "pollué" majoritairement par un taux à 5.5% dû à la simple présence du mot "isolation" dans le titre, l'algorithme d'upsell du système (`_dominant_tva`) héritera de ce taux dominant. Ainsi, toutes les nouvelles prestations suggérées automatiquement (ex: nettoyage de fin de chantier, location d'échafaudage) seront créées avec une TVA à 5.5%, propageant de manière exponentielle l'erreur de base.
