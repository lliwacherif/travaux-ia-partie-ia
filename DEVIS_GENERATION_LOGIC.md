# 🏗️ TRAVAUX IA — Logique complète de génération de devis

> Documentation technique exhaustive du pipeline de génération de devis.
> Dernière mise à jour : 10 juin 2026

---

## Table des matières

1. [Vue d'ensemble du pipeline](#1-vue-densemble-du-pipeline)
2. [Étape 1 — Garde-fou BTP (Blacklist)](#2-étape-1--garde-fou-btp-blacklist)
3. [Étape 2 — Interprétation sémantique (IA)](#3-étape-2--interprétation-sémantique-ia)
4. [Étape 3 — Moteur déterministe (Calcul)](#4-étape-3--moteur-déterministe-calcul)
5. [Résolution des prix](#5-résolution-des-prix)
6. [Architecture K+2 blocs](#6-architecture-k2-blocs)
7. [Logique de padding / troncature (14 lignes)](#7-logique-de-padding--troncature-14-lignes)
8. [Règle intelligente des quantités](#8-règle-intelligente-des-quantités)
9. [Labels spécifiques par métier](#9-labels-spécifiques-par-métier)
10. [Calcul de la TVA](#10-calcul-de-la-tva)
11. [Calcul des totaux globaux](#11-calcul-des-totaux-globaux)
12. [Structure de sortie JSON](#12-structure-de-sortie-json)
13. [Endpoints API](#13-endpoints-api)
14. [Sources de données](#14-sources-de-données)
15. [Exemples concrets](#15-exemples-concrets)

---

## 1. Vue d'ensemble du pipeline

Le système fonctionne en **3 étapes séquentielles** :

```
┌─────────────────┐     ┌─────────────────────┐     ┌──────────────────────┐
│   ÉTAPE 1       │     │   ÉTAPE 2           │     │   ÉTAPE 3            │
│   Garde-fou BTP │ ──▸ │   IA (OpenAI GPT)   │ ──▸ │   Moteur déterministe│
│   (Blacklist)   │     │   (Semantic Mapping) │     │   (Calcul & Structure)│
└─────────────────┘     └─────────────────────┘     └──────────────────────┘
     Bloquant                Sémantique                 Mathématique
     ~0ms                    ~2-5s                      ~50ms
```

**Principe fondamental :** L'IA ne fait **aucun calcul**. Elle se contente d'interpréter la demande et de mapper vers des identifiants de packs. Tout le chiffrage, la structuration et les prix sont calculés de manière **100% déterministe** par le moteur Python.

### Fichiers clés

| Fichier | Rôle |
|---|---|
| `app/api/routers/devis.py` | Routes HTTP (JSON + SSE streaming) |
| `app/services/ai_service.py` | Orchestrateur du pipeline (appels IA + moteur) |
| `app/core/btp_validator.py` | Garde-fou blacklist/whitelist |
| `app/core/prompts.py` | Prompts système pour l'IA |
| `app/core/metier_rules.py` | Règles métier (formules mathématiques par pack) |
| `app/services/prestations_engine.py` | **Cœur du moteur** — prix, padding, structure, totaux |
| `app/models/bpu_item.py` | Modèle ORM de la table `bpu_items` (catalogue de prix) |
| `app/schemas/devis.py` | Schéma Pydantic de la réponse finale |

---

## 2. Étape 1 — Garde-fou BTP (Blacklist)

**Fichier :** `app/core/btp_validator.py`

Avant tout appel IA, le texte utilisateur est scanné contre une **blacklist de mots hors-contexte** pour bloquer instantanément les requêtes non-BTP.

### Mots bloqués (exemples)

| Catégorie | Mots |
|---|---|
| Alimentaire | poisson, poulet, pizza, hamburger, kebab |
| Vestimentaire | pantalon, chemise, t-shirt, chaussure |
| Loisirs | voyage, hotel, console, jeux vidéo |
| Automobile | voiture, pneu, vidange, moto |

### Comportement

- Si un mot blacklisté est détecté → **HTTP 400** immédiat, aucun appel IA.
- La recherche utilise `\b` (word boundaries) pour éviter les faux positifs.
- Coût : **0 token**, **0 ms** d'appel API.

---

## 3. Étape 2 — Interprétation sémantique (IA)

**Fichiers :** `app/services/ai_service.py` + `app/core/prompts.py`

### Ce que fait l'IA

L'IA reçoit un **prompt système** (`SYSTEM_PROMPT_GENERATOR`) et le texte libre de l'utilisateur. Son **unique** rôle est de retourner un JSON structuré qui identifie :

1. **Les métiers** impliqués (Climatisation, Toiture, Plâtrerie, etc.)
2. **Le type** : `PRESTATION` (travaux) ou `DEPANNAGE` (intervention rapide)
3. **Les quantités** extraites du texte (5 splits, 100 m², etc.)
4. **Le type de client** : `pro` ou `particulier`
5. **La nature du projet** : `neuf` ou `renovation`

### Catalogue injecté dans le prompt

Le prompt inclut dynamiquement la liste des packs connus depuis `ALL_METIER_RULES` :

```
CATALOGUE DISPONIBLE :
- Métier: Maçonnerie – Gros œuvre
  Packs: DALLE_BETON, MUR_PARPAINGS
- Métier: Plâtrerie – Cloisons – Doublages – Faux plafonds
  Packs: CLOISON_DISTRIBUTION, FAUX_PLAFOND
- Métier: Carrelage – Sols & Murs
  Packs: CARRELAGE_SOL, FAIENCE_MURALE
```

### Format de sortie de l'IA (JSON strict)

```json
{
  "client_type": "particulier",
  "project_nature": "renovation",
  "lots": [
    {
      "lot_key": "LOT_01",
      "metier": "Chauffage – Ventilation – Climatisation",
      "zone": "interieur",
      "packs": [
        {
          "id": "CLIMATISATION_SPLIT_INSTALLATION",
          "type": "PRESTATION",
          "quantite": 5
        }
      ]
    }
  ]
}
```

### Règles fondamentales de l'IA

- **1 LOT = 1 MÉTIER RÉEL** (jamais de lot "échafaudage", "nettoyage", "protection")
- Si le pack est connu dans le catalogue → utiliser son ID exact
- Si le pack est inconnu → inventer un ID en `MAJUSCULES_SNAKE_CASE`
- Si le pack commence par `DEP-` → type = `DEPANNAGE`
- Si une surface m² est mentionnée → `quantite` = cette surface
- Sinon → `quantite` = le nombre d'unités (ex: 5 pour "5 splits")

### Paramètres de l'appel OpenAI

| Paramètre | Valeur |
|---|---|
| Modèle | `gpt-5` (configurable via `.env`) |
| `max_completion_tokens` | 8192 |
| `temperature` | 1 |
| `top_p` | 1 |
| `presence_penalty` | 0 |

---

## 4. Étape 3 — Moteur déterministe (Calcul)

**Fichier :** `app/services/prestations_engine.py` → `process_ai_lots()`

Le moteur reçoit le JSON de l'IA et le transforme en un devis structuré avec des **prix réels** et une **architecture stricte**.

### Logique principale

```
Pour chaque LOT retourné par l'IA :
│
├── 1. Identifier le métier
├── 2. Chercher si des RÈGLES MÉTIER existent pour ce pack
│       ├── OUI → Éclater le pack en N lignes via les formules mathématiques
│       └── NON → Créer 1 ligne "Fourniture et pose : [pack_id]"
│
├── 3. Résoudre le PRIX de chaque ligne (DB → concept → fallback)
├── 4. Appliquer la TVA selon les règles légales
├── 5. Forcer EXACTEMENT 14 lignes par bloc d'intervention (padding/troncature)
└── 6. Assembler dans l'architecture K+2 blocs
```

### Branche A : Pack CONNU (règles métier)

Quand le `pack_id` retourné par l'IA correspond à une entrée dans `ALL_METIER_RULES`, le moteur **éclate** le pack en plusieurs lignes de matériaux/travaux avec des formules mathématiques.

**Exemple : `CARRELAGE_SOL` avec `quantite = 20` (m²)**

```python
# Règles définies dans metier_rules.py :
"CARRELAGE_SOL": {
    "carrelage_m2": {"formula": "surface * 1.10", "unit": "m²"},  # +10% chutes
    "colle_kg":     {"formula": "surface * 5",    "unit": "kg"},  # 5 kg/m²
    "joint_kg":     {"formula": "surface * 0.5",  "unit": "kg"},  # 0.5 kg/m²
    "croisillons_u":{"formula": "surface * 10",   "unit": "u"},   # 10/m²
    "primaire_l":   {"formula": "surface / 10",   "unit": "l"}
}
```

Le moteur évalue chaque formule avec `surface = 20` :

| Ligne | Formule | QTE calculée | Prix résolu |
|---|---|---|---|
| Carrelage | `20 * 1.10` | 22 m² | Prix DB (concept "carrelage") |
| Colle | `20 * 5` | 100 kg | 3.50 €/kg (prix matériau fixe) |
| Joint | `20 * 0.5` | 10 kg | 4.00 €/kg (prix matériau fixe) |
| Croisillons | `20 * 10` | 200 u | 0.15 €/u (prix matériau fixe) |
| Primaire | `20 / 10` | 2 l | 12.00 €/l (prix matériau fixe) |

### Branche B : Pack INCONNU (fallback)

Quand le `pack_id` n'est pas dans `ALL_METIER_RULES` (ce qui est le cas pour la majorité des métiers comme la climatisation, la toiture, la peinture, etc.), le moteur crée **une seule ligne principale** :

```
"Fourniture et pose : Climatisation split installation"
```

L'unité est déduite automatiquement de la quantité :

| Quantité | Unité attribuée | Logique |
|---|---|---|
| `== 1` | `forfait` | Quantité unitaire → forfait |
| `> 10` | `m²` | Grande quantité → surface |
| `2 à 10` | `u` | Petite quantité → unités discrètes |

Le prix unitaire est résolu via le système de résolution des prix (voir section 5).

---

## 5. Résolution des prix

**Fichier :** `prestations_engine.py` → `_resolve_price()`

Le système de prix fonctionne en **cascade à 4 niveaux** :

```
┌─────────────────────────────────────────────────┐
│ Niveau 1 : Prix matériau connu (_MATERIAL_PRICES)│
│   Ex: "colle_kg" → 3.50 €                       │
│   Priorité : MAXIMALE (consommables hardcodés)   │
├─────────────────────────────────────────────────┤
│ Niveau 2 : Correspondance exacte DB (price_map)  │
│   Ex: slug "carrelage-gres-cerame-60x60" → 55 €  │
│   Source : table SQL bpu_items (slug ou désignation)│
├─────────────────────────────────────────────────┤
│ Niveau 3 : Correspondance concept (concept_map)  │
│   Ex: "climatisation" → 450 € (via mots-clés)    │
│   Recherche : "climatiseur", "monosplit", "split" │
├─────────────────────────────────────────────────┤
│ Niveau 4 : Prix fallback statique par unité      │
│   m² → 45 €  |  forfait → 120 €  |  u → 2.5 €  │
│   ml → 15 €  |  m³ → 120 €       |  kg → 5 €    │
└─────────────────────────────────────────────────┘
```

### Niveau 1 : Prix matériaux (hardcodés)

Pour les consommables dont le prix unitaire est connu et stable :

| Clé | Prix | Description |
|---|---|---|
| `colle_kg` | 3.50 € | Colle à carrelage |
| `joint_kg` | 4.00 € | Mortier de joint |
| `croisillons_u` | 0.15 € | Croisillons |
| `primaire_l` | 12.00 € | Primaire d'accrochage |
| `suspentes_u` | 1.50 € | Suspente réglable |
| `bandes_ml` | 1.20 € | Bande à joint |
| `enduit_kg` | 2.50 € | Enduit à joint |
| `isolant_m2` | 8.00 € | Laine minérale 45mm |
| `rail_ml` | 3.50 € | Rail R48 / montant M48 |
| `polyane_m2` | 1.20 € | Film polyane |
| `mortier_m3` | 95.00 € | Mortier prêt à l'emploi |
| `blocs_u` | 1.80 € | Parpaing creux 20×20×50 |

### Niveau 3 : Concept Map (comment ça marche)

Au démarrage du pipeline, **tous les prix** de la table SQL `bpu_items` sont chargés en mémoire. Pour chaque entrée, le système scanne la désignation contre un dictionnaire de mots-clés :

```python
_KEYWORD_TO_BPU_SEARCH = {
    "climatisation": ["climatisation", "climatiseur", "monosplit", "mono-split"],
    "split":         ["split", "monosplit", "multisplit"],
    "toiture":       ["toiture", "couverture", "réfection toiture"],
    "peinture":      ["peinture", "enduit décoratif"],
    # ... 25+ concepts
}
```

**Exemple :** La requête "Installation 5 splits" → le pack_id est `CLIMATISATION_SPLIT_INSTALLATION` → concept extrait : `"climatisation"` → le concept_map trouve dans la DB : *"Fourniture et pose de climatiseur mural monosplit inverter 3,5 kW"* → **450 €/u**.

### La table `bpu_items` (source de vérité des prix)

| Colonne | Description |
|---|---|
| `id` | ID unique (ex: `BIBLIO-00194`) |
| `corps_metier` | Corps d'état (ex: "Chauffage – Chaudières") |
| `designation` | Désignation complète de la prestation |
| `prix_unitaire_ht` | **Prix unitaire HT** (source réelle) |
| `unite` | Unité de mesure (m², u, forfait, ml...) |
| `slug` | Clé normalisée pour recherche rapide |
| `taux_tva_defaut` | TVA par défaut (5.5, 10, 20) |

Cette table est alimentée par 2 fichiers JSON :
- `bibliotheque-travaux-ia-v1.json` — ~3 000 lignes, 30+ métiers
- `bpu-master-v2.json` — 325 lignes, 3 métiers + 5 fallbacks

---

## 6. Architecture K+2 blocs

Chaque devis généré suit une **structure stricte** composée de K+2 blocs :

```
┌──────────────────────────────────────────────┐
│  BLOC 1 : Mise en place et préparation       │  ← Toujours en premier
│           (3 lignes PRESTATION / 1 DEPANNAGE) │
├──────────────────────────────────────────────┤
│  BLOC 2 : Intervention: [Métier 1]           │  ← 14 lignes (PRESTATION)
│           (ex: Climatisation – CVC)           │     ou 3 lignes (DEPANNAGE)
├──────────────────────────────────────────────┤
│  BLOC 3 : Intervention: [Métier 2]           │  ← Si multi-métier
│           (ex: Plomberie – Sanitaire)         │
├──────────────────────────────────────────────┤
│  ...                                          │  ← Jusqu'à N métiers
├──────────────────────────────────────────────┤
│  BLOC K+2 : Finition et nettoyage            │  ← Toujours en dernier
│              (3 lignes PRESTATION / 1 DEPAN.) │
└──────────────────────────────────────────────┘
```

### Nombre de lignes par bloc

| Bloc | Type PRESTATION | Type DEPANNAGE |
|---|---|---|
| Mise en place | **3 lignes** | **1 ligne** |
| Intervention (×K) | **14 lignes** | **3 lignes** |
| Finition | **3 lignes** | **1 ligne** |

### Détection du type global

Le système détecte si c'est un dépannage en scannant le premier pack :
- Si `pack.type == "DEPANNAGE"` → mode dépannage (lignes réduites)
- Sinon → mode prestation standard

---

## 7. Logique de padding / troncature (14 lignes)

**Fichier :** `prestations_engine.py` → `_pad_or_truncate_lines()`

Le moteur force un nombre exact de lignes dans chaque bloc. Si la quantité de lignes réelles ne correspond pas au target, deux mécanismes s'activent :

### Cas 1 : Trop de lignes → Troncature

Si `len(lignes) > target` (ex: 18 lignes pour un target de 14) :
- Garder les `target - 1` premières lignes
- Fusionner les lignes restantes en une seule : `"Autres travaux et finitions"` avec le total HT cumulé

Si `target == 1` :
- Tout fusionner en une seule ligne forfaitaire

### Cas 2 : Pas assez de lignes → Padding

Si `len(lignes) < target` (ex: 1 ligne pour un target de 14) :

1. **Calculer le prix unitaire du padding :**
   ```
   pad_pu = 15% du total HT réel / nombre de lignes manquantes
   Borné entre 25 € et 85 €
   ```
   
2. **Sélectionner les labels** adaptés au métier (voir section 9)

3. **Déterminer la quantité** via la règle intelligente (voir section 8)

4. **Générer** les lignes manquantes avec `unite = "forfait"`

---

## 8. Règle intelligente des quantités

### Le problème résolu

Sans cette règle, "Remplacement toiture 100m²" générait des lignes accessoires à **QTE = 100**, donnant des totaux absurdes (132 671 € au lieu de 12 336 €).

### La règle

L'unité de la ligne principale détermine si les lignes de padding héritent la quantité :

| Unité principale | QTE des lignes padding | Justification métier |
|---|---|---|
| `u` (unités discrètes) | **Héritée** (ex: 5) | Chaque unité a besoin de ses propres raccordements |
| `m²` | **1** | Le repérage/traçage se fait une seule fois pour toute la surface |
| `ml` | **1** | Idem, une seule opération pour toute la longueur |
| `m³` | **1** | Idem |
| `forfait` | **1** | Déjà un prix global |

### Exemples concrets

**"Installation 5 splits"** → unité principale = `u`
```
Fourniture et pose : Climatisation split     | QTE=5 | u       | 450 €
Repérage et tracé des parcours frigorifiques | QTE=5 | forfait | 25.96 €  ← hérite
Percements et carottages                     | QTE=5 | forfait | 25.96 €  ← hérite
```

**"Remplacement toiture 100m²"** → unité principale = `m²`
```
Fourniture et pose : Toiture remplacement    | QTE=100 | m²      | 96 €
Mise en sécurité des éléments de l'ouvrage   | QTE=1   | forfait | 85 €  ← pas d'héritage
Repérage, traçage et implantation            | QTE=1   | forfait | 85 €  ← pas d'héritage
```

---

## 9. Labels spécifiques par métier

### Règle absolue de sélection des prestations

> Ne sélectionner que des prestations ayant un lien **direct, technique et indispensable** avec les travaux décrits. Interdiction totale d'ajouter des prestations annexes, complémentaires, optionnelles ou hors périmètre.

Le moteur sélectionne des labels de prestations **strictement cloisonnés par corps d'état** :

### Climatisation / CVC / Chauffage

| # | Label |
|---|---|
| 1 | Repérage et tracé des parcours frigorifiques ou hydrauliques |
| 2 | Percements et carottages pour passages de liaisons |
| 3 | Mise en place des supports et fixations anti-vibratiles |
| 4 | Pose et raccordement des liaisons et réseaux |
| 5 | Câblage électrique d'interconnexion interne |
| 6 | Pose et raccordement des évacuations de condensats |
| 7 | Mise sous pression pour test d'étanchéité |
| 8 | Tirage au vide de l'installation (si applicable) |
| 9 | Appoint de fluide ou traitement des réseaux |
| 10 | Tests d'étanchéité et relevés de pressions |
| 11 | Vérification des écoulements et pentes |
| 12 | Contrôle de l'isolation thermique des liaisons |
| 13 | Mise en service, réglages et relevés de températures |
| 14 | Vérification de la conformité aux normes |
| 15 | Mise en propreté de la zone d'intervention |

### Plomberie / Sanitaire

| # | Label |
|---|---|
| 1 | Repérage et tracé des parcours de canalisations |
| 2 | Percements et saignées pour passages de tuyauteries |
| 3 | Fourniture et pose des colliers et supports de fixation |
| 4 | Découpes, ébavurages et préparation des tubes |
| 5 | Réalisation des assemblages (soudures, sertissages, collages) |
| 6 | Raccordement des réseaux d'alimentation (EF/EC) |
| 7 | Raccordement des réseaux d'évacuation (EU/EV) |
| 8 | Mise en eau et purge des canalisations |
| 9 | Tests de mise en pression et recherche de fuites |
| 10 | Contrôle des débits et des pentes d'écoulement |
| 11 | Isolation thermique ou acoustique ponctuelle des tubes |
| 12 | Rebouchage technique des traversées de cloisons |
| 13 | Vérification de la conformité des raccordements |
| 14 | Paramétrage des organes de régulation |
| 15 | Mise en propreté de la zone d'intervention |

### Électricité

| # | Label |
|---|---|
| 1 | Repérage et tracé des cheminements électriques |
| 2 | Réalisation des saignées et percements |
| 3 | Fourniture et pose des cheminements (gaines, goulottes) |
| 4 | Tirage des câbles et conducteurs |
| 5 | Dénudage et repérage des extrémités de câbles |
| 6 | Raccordements des appareillages et boîtes de dérivation |
| 7 | Repérage et raccordement au tableau de répartition |
| 8 | Vérification de la continuité des conducteurs de protection |
| 9 | Mesure de la résistance de prise de terre |
| 10 | Contrôle d'isolement des circuits |
| 11 | Tests de déclenchement des dispositifs différentiels |
| 12 | Rebouchage technique des saignées et scellements |
| 13 | Mise sous tension et essais fonctionnels |
| 14 | Identification formelle des circuits (étiquetage) |
| 15 | Mise en propreté de la zone d'intervention |

### Peinture / Revêtements

| # | Label |
|---|---|
| 1 | Reconnaissance et sondage des supports |
| 2 | Lessivage et dégraissage des surfaces |
| 3 | Grattage et égrenage des parties non adhérentes |
| 4 | Ouverture et traitement des fissures |
| 5 | Application d'un enduit de rebouchage ponctuel |
| 6 | Application d'un enduit de lissage ou repassage |
| 7 | Ponçage soigné et dépoussiérage des fonds |
| 8 | Mise en place de bandes de masquage |
| 9 | Protection spécifique des menuiseries et appareillages |
| 10 | Application d'une couche d'impression ou primaire |
| 11 | Traitement spécifique des joints ou réchampissage |
| 12 | Vérification de l'opacité et retouches intermédiaires |
| 13 | Dépose minutieuse des adhésifs de masquage |
| 14 | Contrôle visuel de l'homogénéité du rendu |
| 15 | Mise en propreté de la zone d'intervention |

### Mise en place (bloc global)

| # | Label |
|---|---|
| 1 | Balisage et sécurisation de la zone de travail |
| 2 | Mise en place des protections au sol et murales |
| 3 | Acheminement de l'outillage et préparation du poste |
| 4 | Vérification des supports et repérages initiaux |

### Nettoyage (bloc global)

| # | Label |
|---|---|
| 1 | Évacuation des gravats et déchets résiduels |
| 2 | Nettoyage approfondi de la zone d'intervention |
| 3 | Retrait des protections et remise en ordre |
| 4 | Réception technique et contrôle final |

### Métier générique (fallback)

Pour tout métier non explicitement mappé, un jeu de labels neutres et techniquement irréprochables est utilisé (15 labels disponibles : mise en sécurité, repérage, préparation, manutention, ajustement, consommables, vérification, protection, contrôle, tests, déchets, conformité DTU, réglages, réception, propreté).

---

## 10. Calcul de la TVA

**Fichier :** `prestations_engine.py` → `_get_tva()`

La TVA est calculée selon les règles fiscales françaises :

```
┌───────────────────────────────────────────────────┐
│  1. Si "isolation" dans le métier ou la désignation│
│     → TVA = 5.5%                                   │
├───────────────────────────────────────────────────┤
│  2. Si project_nature == "neuf" OU client == "pro" │
│     → TVA = 20%                                    │
├───────────────────────────────────────────────────┤
│  3. Sinon (rénovation chez un particulier)          │
│     → TVA = 10% (défaut)                           │
└───────────────────────────────────────────────────┘
```

### Mots-clés déclencheurs pour 5.5%

- `"isolation"` dans le métier
- `"isolation"` dans la désignation
- `"laine"` dans la désignation
- `"énergétique"` dans la désignation

---

## 11. Calcul des totaux globaux

**Fichier :** `prestations_engine.py` → `calculate_global_totals()`

Les totaux sont calculés de manière **purement arithmétique** après l'assemblage complet :

```
Pour chaque ligne du devis :
    total_ht_ligne = pu_ht × quantité
    total_ttc_ligne = total_ht_ligne × (1 + tva / 100)

Totaux globaux :
    Total HT  = Σ total_ht_ligne (toutes lignes)
    Total TVA = Σ (base_ht × taux_tva / 100) par taux
    Total TTC = Total HT + Total TVA
```

La ventilation TVA est détaillée par taux :

```json
"tva_breakdown": {
    "10.0": { "base_ht": 4447.40, "tva_amount": 444.74 },
    "5.5":  { "base_ht": 126.00,  "tva_amount": 6.93 }
}
```

---

## 12. Structure de sortie JSON

### Schéma complet (`DevisResponse`)

```json
{
    "date": "2026-06-10T10:05:48.383133Z",
    "validite": "2026-07-10T10:05:48.383133Z",
    "duree": 30,
    "montant_ttc": 4892.14,
    "blocs": [
        {
            "title": "Mise en place et préparation",
            "lots": [
                {
                    "title": "Préparation",
                    "lignes": [
                        {
                            "num": 1,
                            "description": "Balisage et sécurisation de la zone de travail",
                            "qte": 1.0,
                            "unit": "forfait",
                            "pu": 95.0,
                            "tva": 10.0,
                            "ht": 95.0,
                            "ttc": 104.5
                        }
                    ]
                }
            ]
        },
        {
            "title": "Intervention: Chauffage – Ventilation – Climatisation",
            "lots": [
                {
                    "title": "Travaux principaux",
                    "lignes": [ /* ... 14 lignes ... */ ]
                }
            ]
        },
        {
            "title": "Finition et nettoyage",
            "lots": [
                {
                    "title": "Nettoyage",
                    "lignes": [ /* ... 3 lignes ... */ ]
                }
            ]
        }
    ]
}
```

### Hiérarchie des objets

```
DevisResponse
├── date: datetime (ISO 8601)
├── validite: datetime (date + 30 jours)
├── duree: int (jours)
├── montant_ttc: float
└── blocs: Bloc[]
    ├── title: str
    └── lots: Lot[]
        ├── title: str
        └── lignes: Ligne[]
            ├── num: int (index 1-based)
            ├── description: str
            ├── qte: float
            ├── unit: str
            ├── pu: float (prix unitaire HT)
            ├── tva: float (taux en %)
            ├── ht: float (total HT ligne)
            └── ttc: float (total TTC ligne)
```

---

## 13. Endpoints API

### `POST /api/v1/devis/generate`

Génération synchrone (réponse complète en une seule requête).

```bash
curl -X POST http://127.0.0.1:8000/api/v1/devis/generate \
  -H "Content-Type: application/json" \
  -d '{"text": "installation 5 splits"}'
```

**Réponses :**
- `200` → `DevisResponse` (JSON)
- `400` → Requête hors-contexte BTP
- `502` → L'IA a retourné un JSON invalide
- `503` → L'IA est indisponible

### `POST /api/v1/devis/generate/stream`

Génération avec streaming SSE (Server-Sent Events) pour afficher la progression en temps réel.

```bash
curl -N -X POST http://127.0.0.1:8000/api/v1/devis/generate/stream \
  -H "Content-Type: application/json" \
  -d '{"text": "installation 5 splits"}'
```

**Événements SSE :**

```
event: progress
data: {"step": 1, "total": 4, "label": "Analyse"}

event: progress
data: {"step": 2, "total": 4, "label": "Generate"}

event: progress
data: {"step": 3, "total": 4, "label": "Calculate"}

event: progress
data: {"step": 4, "total": 4, "label": "Finalise"}

event: result
data: { ...DevisResponse... }

event: done
data: {}
```

---

## 14. Sources de données

### Base de données PostgreSQL (source de vérité)

| Table | Contenu | Volume |
|---|---|---|
| `bpu_items` | Catalogue de prix unitaires | ~3 325 lignes |

### Fichiers JSON d'alimentation

| Fichier | Lignes | Métiers | Usage |
|---|---|---|---|
| `bibliotheque-travaux-ia-v1.json` | ~3 000 | 30+ | Bibliothèque principale |
| `bpu-master-v2.json` | 325 | 3 | Fallbacks + règles détaillées |

### Règles métier Python

| Métier | Code | Packs disponibles |
|---|---|---|
| Maçonnerie – Gros œuvre | `MAC-GO` | `DALLE_BETON`, `MUR_PARPAINGS` |
| Plâtrerie – Cloisons | `PLA` | `CLOISON_DISTRIBUTION`, `FAUX_PLAFOND` |
| Carrelage – Sols & Murs | `CAR` | `CARRELAGE_SOL`, `FAIENCE_MURALE` |

> **Note :** Tous les autres métiers (climatisation, toiture, peinture, électricité, etc.) passent par la branche B (fallback) avec résolution de prix via la table `bpu_items`.

---

## 15. Exemples concrets

### Exemple 1 : "Installation 5 splits"

```
Entrée utilisateur : "installation 5 splits"

ÉTAPE 1 (Blacklist) : ✅ Aucun mot interdit
ÉTAPE 2 (IA) : → metier="Chauffage – Ventilation – Climatisation"
                → pack_id="CLIMATISATION_SPLIT_INSTALLATION"
                → quantite=5, type="PRESTATION"
ÉTAPE 3 (Moteur) :
  - Pack inconnu dans les règles → Branche B (fallback)
  - Unité : quantité=5, 2<5≤10 → "u"
  - Prix : concept "climatisation" → DB trouve 450 €/u
  - Padding : 13 lignes manquantes, labels CVC spécifiques
  - QTE padding : unité "u" → héritage → QTE=5
  - PU padding : (2250 × 15%) / 13 = 25.96 €

RÉSULTAT :
  Bloc 1 : Mise en place (3 lignes × 95 € × QTE=1)    =   285 €
  Bloc 2 : Intervention (1 × 2250 + 13 × 129.80)       = 3 937.40 €
  Bloc 3 : Finition (3 lignes × 75 € × QTE=1)          =   225 €
  ─────────────────────────────────────────────────
  Total HT  = 4 447.40 €
  TVA 10%   =   444.74 €
  Total TTC = 4 892.14 €
```

### Exemple 2 : "Remplacement toiture 100m²"

```
Entrée utilisateur : "remplacement toiture 100m2"

ÉTAPE 2 (IA) : → metier="Couverture – Toiture"
                → pack_id="TOITURE_REMPLACEMENT"
                → quantite=100, type="PRESTATION"
ÉTAPE 3 (Moteur) :
  - Pack inconnu → Branche B
  - Unité : quantité=100, >10 → "m²"
  - Prix : concept "toiture" → DB trouve 96 €/m²
  - Padding : 13 lignes manquantes, labels génériques
  - QTE padding : unité "m²" → PAS d'héritage → QTE=1
  - PU padding : (9600 × 15%) / 13 = 85 € (plafonné à 85)

RÉSULTAT :
  Bloc 1 : Mise en place (3 × 95)                      =   285 €
  Bloc 2 : Intervention (1 × 9600 + 13 × 85)           = 10 705 €
  Bloc 3 : Finition (3 × 75)                            =   225 €
  ─────────────────────────────────────────────────
  Total HT  = 11 215 €
  TVA 10%   =  1 121.50 €
  Total TTC = 12 336.50 €
```
