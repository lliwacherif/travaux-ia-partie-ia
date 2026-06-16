"""Deterministic chatbot replies for Travaux IA UI/navigation intents."""

from __future__ import annotations


def build_chatbot_static_response(ux_modules: set[str]) -> str | None:
    """Return a fast local answer for known UI workflows.

    The chatbot still uses the model for open-ended BTP domain questions, but
    UI/navigation guidance is fixed product knowledge. Answering it locally is
    faster and avoids provider-side 503s for simple operational commands.
    """
    if not ux_modules:
        return None

    if "devis" in ux_modules:
        return (
            "Pour générer un devis détaillé :\n"
            "1. Accédez à l'onglet **« DEVIS IA »** et vérifiez la "
            "**« Sélection du client »**.\n"
            "2. Choisissez et validez le chantier concerné.\n"
            "3. Dans l'espace de prompt (texte), saisissez la description du "
            "travail ou des travaux à réaliser pour le devis. Vous pouvez "
            "également utiliser la dictée vocale.\n"
            "4. Lancez la génération du devis.\n"
            "5. Une fois le devis généré, vérifiez les lignes proposées et "
            "apportez les modifications nécessaires.\n"
            "6. Enfin, validez le devis pour confirmer sa création."
        )

    if "planification" in ux_modules and "assistant" in ux_modules:
        return (
            "Pour générer une facture à partir d'un devis :\n"
            "1. Validez d'abord le devis concerné.\n"
            "2. Accédez à la page **« Documents »** via le bouton situé en "
            "haut de l'interface.\n"
            "3. Sélectionnez le devis à facturer dans la liste des documents.\n"
            "4. Assurez-vous que le devis est bien signé.\n"
            "5. Cliquez sur le menu des trois points **(⋯)** associé au devis.\n"
            "6. Sélectionnez l'option **« Créer une facture »** pour générer "
            "la facture."
        )

    if "catalogue" in ux_modules:
        return (
            "Pour ajouter un prix spécifique :\n"
            "1. Ouvrez la **« Bibliothèque personnalisée »**.\n"
            "2. Cliquez sur **« Créer Une Ligne Personnalisée »**.\n"
            "3. Renseignez le **corps de métier**, la **désignation**, "
            "l'**unité**, le **prix unitaire HT** et la **TVA**.\n"
            "4. La **Bibliothèque TRAVAUX IA** sert de base globale de prix ; "
            "votre **Bibliothèque personnalisée** conserve vos propres prix pour "
            "les prochaines générations de devis."
        )

    if {"clients", "planification", "equipes", "documents"} & ux_modules:
        steps: list[str] = []
        if "clients" in ux_modules:
            steps.append(
                "Accédez à **« CLIENT »** puis utilisez la recherche par nom, "
                "téléphone ou adresse. Pour créer une fiche, choisissez "
                "**Client Professionnel** ou **Client Particulier**."
            )
        if "planification" in ux_modules:
            steps.append(
                "Accédez à **« PLANIFICATION »** puis ouvrez la modale "
                "**Chantiers** pour consulter le projet, sa progression "
                "financière, la carte et les liens **Google Maps** / **Waze**."
            )
            if "equipes" in ux_modules:
                steps.append(
                    "Pour la logistique équipe, utilisez les informations du "
                    "chantier actif et l'itinéraire intégré avant d'envoyer les "
                    "intervenants."
                )
        if "documents" in ux_modules:
            steps.append(
                "Accédez à **« DOCUMENTS »** pour les pièces, ou à **« FINANCE »** "
                "pour suivre les volumes **DEVIS**, **ACOMPTE**, **FACTURES**, "
                "**AVOIRS** et **TOTAL DOCS**."
            )
        return "\n".join(f"{idx}. {step}" for idx, step in enumerate(steps, 1))

    if "dashboard" in ux_modules:
        return (
            "Pour lire vos finances :\n"
            "1. Accédez à l'onglet **« FINANCE »**.\n"
            "2. Consultez les widgets **CLIENTS**, **DEVIS**, **ACOMPTE**, "
            "**FACTURES**, **AVOIRS** et **TOTAL DOCS**.\n"
            "3. Analysez le tunnel **CA EN COURS -> CA SIGNÉ -> CA FACTURÉ -> "
            "CA TOTAL**.\n"
            "4. En bas, surveillez **TAUX DE CONVERSION** et **CA MOYEN PAR "
            "DEVIS SIGNÉ**. Si le taux baisse, priorisez les devis en attente."
        )

    if "assistant" in ux_modules:
        return (
            "Je peux vous guider dans Travaux IA. Dites-moi si vous voulez gérer "
            "un **client**, générer un **devis IA**, consulter **FINANCE**, "
            "retrouver un **document**, gérer une **bibliothèque de prix** ou "
            "localiser un **chantier**."
        )

    return None


def build_chatbot_provider_fallback_response(user_text: str) -> str:
    """Return a safe answer when the model provider is unavailable."""
    text = user_text.strip()
    if text:
        return (
            "Je peux vous aider sur Travaux IA, mais la réponse détaillée n'est "
            "pas disponible pour l'instant.\n"
            "Pour avancer tout de suite :\n"
            "1. Utilisez **« DEVIS IA »** pour sélectionner un client et générer "
            "un devis.\n"
            "2. Utilisez **« CLIENT »** pour rechercher ou créer une fiche.\n"
            "3. Utilisez **« FINANCE »** pour suivre CA, factures, acomptes et "
            "taux de conversion.\n"
            "4. Utilisez **« PLANIFICATION »** pour retrouver un chantier et ses "
            "liens **Google Maps** / **Waze**."
        )
    return "Bonjour ! Je peux vous aider à gérer vos clients, devis, chantiers et équipes dans Travaux IA."


def build_landing_chatbot_provider_fallback_response(user_text: str) -> str:
    """Return a safe landing-page answer when the model provider is unavailable."""
    text = user_text.strip()
    if not text:
        return (
            "Bonjour ! Travaux IA aide les professionnels du bâtiment à gérer "
            "leurs clients, générer des devis IA, organiser leurs documents et "
            "choisir l'offre adaptée à leur volume."
        )

    return (
        "Travaux IA est une application web pour les professionnels du bâtiment : "
        "gestion clients, devis IA, documents, tableaux de bord, planning et équipes.\n"
        "Pour choisir vite : **Découverte** pour tester gratuitement, **Pro** pour "
        "un artisan seul avec 30 devis IA/mois, **Expert** pour 2 utilisateurs et "
        "100 devis IA/mois, **Premium** pour 3 utilisateurs et 250 devis IA/mois, "
        "ou **Entreprise** pour un besoin sur mesure."
    )


def build_mobile_chatbot_provider_fallback_response(user_text: str) -> str:
    """Return a safe mobile-app answer when the model provider is unavailable."""
    text = user_text.strip()
    if not text:
        return (
            "Bonjour ! Je peux vous guider dans l'application mobile Travaux IA "
            "pour gérer vos clients, devis, chantiers et équipes."
        )

    return (
        "Je peux vous aider sur l'application mobile Travaux IA, mais la réponse "
        "détaillée n'est pas disponible pour l'instant.\n"
        "Pour avancer : ouvrez **« Devis IA »** pour générer ou valider un devis, "
        "**« Clients »** pour gérer une fiche client, **« Chantiers »** pour suivre "
        "les dates et le statut, ou **« Équipes »** pour vérifier une équipe "
        "**« Disponible »**."
    )


__all__ = [
    "build_chatbot_provider_fallback_response",
    "build_chatbot_static_response",
    "build_landing_chatbot_provider_fallback_response",
    "build_mobile_chatbot_provider_fallback_response",
]
