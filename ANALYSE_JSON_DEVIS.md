# Analyse du Traitement du Grand Fichier JSON et Mapping pour la Generation de Devis

Ce document explique en details comment le systeme charge, traite et map le grand fichier JSON pour generer un devis de travaux.

## 1. Les Fichiers Sources JSON
Le systeme repose principalement sur deux grands fichiers JSON qui agissent comme bibliotheques de prix :
- `bibliotheque-travaux-ia-v1.json` : Contient environ 3000 lignes couvrant plus de 30 corps de metiers avec les prix reels.
- `bpu-master-v2.json` : Contient 325 lignes pour 3 metiers specifiques et des elements de secours (fallbacks).

## 2. L'Ingestion dans la Base de Donnees (Le Script Seed)
Le script `scripts/seed_bpu.py` est responsable de la lecture et du traitement de ces fichiers JSON :
1. **Lecture** : Il ouvre et parse les fichiers JSON en memoire.
2. **Normalisation (Slugify)** : Pour chaque prestation, la `designation` est transformee en `slug` (une chaine de caracteres sans accents, en minuscules, separee par des tirets) pour faciliter les recherches exactes ulterieures.
3. **Extraction des donnees** : Le script extrait l'ID, le corps de metier, la designation, le prix unitaire HT (`prix_unitaire_ht`), l'unite (`unite`), et le taux de TVA par defaut.
4. **Insertion (Upsert)** : Ces donnees sont ensuite inserees ou mises a jour dans la table PostgreSQL `bpu_items`. Cette table devient la source de verite unique pour les prix. L'avantage de cette methode est d'eviter de charger un fichier JSON de 2.35 Mo a chaque generation de devis.

## 3. L'Intervention de l'IA (Etape Semantique)
L'intelligence artificielle (GPT) ne manipule pas le JSON et ne fait aucun calcul de prix. Son seul role est d'analyser la demande de l'utilisateur et de retourner un objet structure en JSON avec :
- Le(s) metier(s) implique(s)
- Un identifiant de pack (`pack_id`) (soit connu du catalogue, soit invente par l'IA)
- Le type (prestation ou depannage)
- La quantite (par exemple 5 pour "5 splits" ou 100 pour "100 m2")

## 4. Le Mapping et la Generation du Devis (Le Moteur Deterministe)
Une fois que l'IA a identifie la prestation, le moteur de calcul `prestations_engine.py` prend le relais. Il doit resoudre le prix de la prestation en cherchant dans les donnees issues du grand fichier JSON (desormais dans la base de donnees). Il utilise une logique de recherche en cascade a 4 niveaux :

### Niveau 1 : Prix materiaux en dur
Pour des consommables tres standards (ex: colle_kg, joint_kg, croisillons_u), le prix est code en dur (ex: 3.50 euros/kg) pour etre immediat.

### Niveau 2 : Correspondance Exacte (Map par Slug)
Le moteur verifie si le `pack_id` ou la demande correspond exactement a un `slug` present dans la table `bpu_items` (generee a partir du JSON). Si oui, il recupere la ligne et prend le prix unitaire HT directement.

### Niveau 3 : Correspondance par Concept (Concept Map)
C'est ici que le contenu du grand fichier JSON est veritablement fouille. Au demarrage, tous les prix de la table `bpu_items` sont charges. Le systeme dispose d'un dictionnaire de mots-cles (ex: pour le concept "climatisation", il va chercher les mots "climatiseur", "monosplit", "split"). Il scanne la colonne `designation` des lignes issues du JSON pour trouver la prestation qui correspond le mieux au concept demande.
Par exemple, pour "climatisation", il matchera la ligne "Fourniture et pose de climatiseur mural monosplit inverter 3,5 kW" et appliquera son prix (ex: 450 euros).

### Niveau 4 : Prix de Secours (Fallback)
Si aucune correspondance n'est trouvee via la base de donnees du JSON, le systeme applique un prix statistique de secours base uniquement sur l'unite (ex: 45 euros pour un m2, 120 euros pour un forfait, 2.5 euros pour une unite).

## 5. Structuration Finale et Calculs
Une fois les prix unitaire HT recuperes et mappes depuis les donnees JSON, le moteur effectue la suite sans IA :
- **Eclatement des lignes** : Si la prestation correspond a un ensemble complexe (ex: carrelage = carrelage + colle + joint), la prestation est eclatee en plusieurs lignes mathematiques grace a des regles metiers en dur.
- **Application de la TVA** : Selon le contexte (isolation, renovation, neuf), la TVA appropriee (5.5%, 10% ou 20%) est appliquee.
- **Remplissage / Troncature** : Le systeme force une architecture K+2 blocs (un bloc de Mise en place de 3 lignes, des blocs d'intervention de 14 lignes strictes par metier, un bloc de Nettoyage de 3 lignes). Il ajoute des lignes "forfaitaires" liees au metier si besoin, en calculant leur prix proportionnellement au montant total.
- **Totaux Globaux** : Les totaux HT et TTC sont calcules en multipliant les prix unitaires par les quantites extraites par l'IA.

## Conclusion
En resume, le grand fichier JSON n'est pas parse a chaque requete. Il est ingere une fois dans une base PostgreSQL SQL. Le pipeline separe la reflexion (confiee a l'IA) du calcul (confie au code Python). Le systeme croise l'analyse semantique de l'IA avec une recherche mots-cles sur les lignes issues du JSON pour attribuer des prix de maniere 100% deterministe et rapide.
