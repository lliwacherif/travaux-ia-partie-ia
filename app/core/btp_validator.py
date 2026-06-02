from fastapi import HTTPException
import re

# Strict blacklist of words/categories that immediately block the generation
NON_BTP_BLACKLIST = [
    # Alimentaire
    "poisson", "poulet", "pizza", "hamburger", "kebab", "restaurant", "menu", "frites",
    # Vestimentaire
    "pantalon", "chemise", "t-shirt", "chaussure", "veste", "robe",
    # Loisirs/Tourisme
    "voyage", "hotel", "console", "jeux vidéo", "avion", "billet", "spectacle",
    # Agriculture/Élevage
    "vache", "tracteur", "champs", "récolte",
    # Automobile/Transport
    "voiture", "pneu", "vidange", "freins", "moto"
]

# (Optional) We can also add a whitelist to ensure at least one BTP term is present, 
# but a blacklist is the first strong defense.
BTP_KEYWORDS = [
    "toiture", "charpente", "placo", "maconnerie", "maçonnerie", "peinture", 
    "carrelage", "plomberie", "electricite", "électricité", "chauffage", 
    "menuiserie", "sol", "mur", "plafond", "fenetre", "fenêtre", "porte",
    "démolition", "demolition", "terrassement", "isolation", "enduit", "rénovation"
]

def validate_btp_context(description: str) -> None:
    """
    Validates that the user's description is actually related to construction/BTP.
    Raises an HTTPException (400) if off-topic words are detected.
    """
    desc_lower = description.lower()
    
    # 1. Check blacklist
    for word in NON_BTP_BLACKLIST:
        # Match whole words to avoid false positives
        if re.search(r'\b' + re.escape(word) + r'\b', desc_lower):
            raise HTTPException(
                status_code=400, 
                detail=f"Mots hors contexte BTP détectés: '{word}'. Ce service est réservé aux devis de travaux."
            )
            
    # 2. Check if there's at least one BTP keyword (Optional but recommended for extreme strictness)
    # has_btp_keyword = any(re.search(r'\b' + re.escape(kw) + r'\b', desc_lower) for kw in BTP_KEYWORDS)
    # if not has_btp_keyword:
    #     raise HTTPException(
    #         status_code=400, 
    #         detail="Aucun mot clé lié au bâtiment (BTP) n'a été détecté dans votre description."
    #     )
    
    return True
