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
            "Pour générer une offre détaillée :\n"
            "1. Cliquez sur **« Devis IA »** dans la sidebar de gauche.\n"
            "2. Remplissez **« Description du projet »**, **« Type de travaux »**, "
            "**« Budget estimé (€) »** et **« Matériaux souhaités »**.\n"
            "3. Cliquez sur **« Générer le devis avec l'IA »**.\n"
            "4. Relisez le tableau avec **« Désignation »**, **« Quantité »**, "
            "**« Prix Unitaire »**, **« Total HT »**, **« TVA (%) »** et "
            "**« Total TTC »**.\n"
            "5. En bas, utilisez **« Valider le devis »**, **« Envoyer au client »** "
            "ou **« Télécharger en PDF »**."
        )

    if {"clients", "planification", "equipes"} & ux_modules:
        steps: list[str] = []
        if "clients" in ux_modules:
            steps.append(
                "Allez dans **« Clients »** puis utilisez **« Ajouter un client »** "
                "en haut à droite, ou **« Modifier »** sur la ligne du client."
            )
        if "planification" in ux_modules:
            steps.append(
                "Allez dans **« Planifier Chantier »**, choisissez "
                "**« Sélectionner un Client »**, **« Sélectionner un Devis »**, les "
                "dates, l'équipe et le **« Statut »**, puis cliquez sur "
                "**« Planifier le chantier »**."
            )
        if "equipes" in ux_modules:
            steps.append(
                "Allez dans **« Gérer les équipes »** et vérifiez que la colonne "
                "**« Statut »** indique **« Disponible »** avant d'affecter une équipe."
            )
        return "\n".join(f"{idx}. {step}" for idx, step in enumerate(steps, 1))

    if "dashboard" in ux_modules:
        return (
            "Pour consulter l'activité :\n"
            "1. Cliquez sur **« Tableau de bord »** dans la sidebar de gauche.\n"
            "2. Consultez les cartes **« Total de chantiers »**, **« Équipes "
            "Actives »**, **« Devis Générés »** et **« Chantiers en cours »**.\n"
            "3. Utilisez le calendrier ou la timeline centrale pour voir le planning."
        )

    if "assistant" in ux_modules:
        return (
            "Je peux vous guider dans Travaux IA. Dites-moi si vous voulez gérer "
            "un **client**, générer un **devis**, **planifier un chantier** ou "
            "gérer une **équipe**."
        )

    return None


__all__ = ["build_chatbot_static_response"]
