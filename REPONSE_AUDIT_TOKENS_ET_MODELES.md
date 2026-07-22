# Audit Technique : Modèles IA et Consommation de Tokens – TRAVAUX IA

Bonjour,

Suite à la présentation de notre nouvelle architecture hybride (moteur déterministe + IA pour l'extraction sémantique), nous souhaitons apporter des clarifications précises concernant les modèles d'Intelligence Artificielle réellement déployés et la consommation de tokens récemment observée.

Ce document complémentaire répond de manière transparente à vos interrogations sur l'utilisation de l'API OpenAI, les coûts associés et le volume de traitement.

---

## 1. Quels modèles sont réellement utilisés ?

Pour répondre à nos besoins d'extraction structurée (JSON) tout en optimisant les temps de réponse et la facturation, nous utilisons **exclusivement la famille des modèles GPT-4 d'OpenAI**. 

Le choix du modèle précis se fait de manière dynamique, en fonction de la difficulté et du contexte du prompt (routage intelligent) :

*   **GPT-4o mini**
    *   **Rôle** : Modèle optimisé, rapide et très abordable. Il est sollicité pour des tâches ciblées, rapides, ou des requêtes avec un contexte simple (ex. prétraitement, requêtes claires avec peu d'ambiguïté).
    *   **Performances** : Vitesse rapide, intelligence moyenne.
    *   **Tarification officielle** : 0,15 $ / 1M tokens (Input) | 0,60 $ / 1M tokens (Output).

*   **GPT-4** (Modèles haute capacité)
    *   **Rôle** : Modèle non-reasoning le plus intelligent de la gamme. Il prend le relais pour les requêtes complexes, ambiguës ou multi-métiers, nécessitant une compréhension sémantique fine et le respect absolu de notre format JSON strict (Structured Outputs).
    *   **Performances** : Vitesse moyenne, intelligence supérieure.
    *   **Tarification officielle** : 2,00 $ / 1M tokens (Input) | 8,00 $ / 1M tokens (Output).

L'utilisation de la famille GPT-4 nous assure la modularité nécessaire : une requête simple coûtera une fraction de centime (GPT-4o mini), tandis qu'un besoin complexe mobilisera la puissance de GPT-4 pour garantir l'absence d'hallucination (qui était le principal défaut de la version V2).

---

## 2. Pourquoi la consommation atteint-elle ~47 000 jetons par appel ?

La consommation exceptionnelle de près de **47 000 tokens par appel**, que vous avez pu observer dans nos rapports d'utilisation récents, s'explique par un contexte très spécifique : **il s'agit exclusivement de notre phase de tests intensifs (stress-tests).**

Durant cette période d'ingénierie et de développement, nous avons dû éprouver la robustesse de notre nouvelle architecture. Cette forte consommation est due à plusieurs facteurs propres aux tests :

1.  **Injection massive de contexte (K+2, règles métiers, catalogues entiers)** : Pour valider la compréhension globale du modèle, nous avons volontairement injecté dans le Prompt Système (Input) la quasi-totalité de notre bibliothèque de prix unitaires, des règles de TVA, et des définitions métiers (`BIBLIOTHÈQUE DISPONIBLE`).
2.  **Vérification de la fiabilité du JSON (Structured Outputs)** : Pour garantir que l'IA ne génère plus jamais de faux prix ou de prestations inexistantes (le but de l'audit précédent), nous devions la pousser dans ses retranchements avec des contextes extrêmement larges.
3.  **Appels non optimisés par conception** : L'objectif initial était la stabilité et la qualité du résultat (zéro hallucination), pas l'économie. Nous avons consommé énormément de tokens pour certifier l'architecture déterministe.

**En production**, ce comportement est radicalement différent :
*   Le système de requêtes ne charge que le sous-ensemble du dictionnaire métier strictement nécessaire.
*   Le routage vers des modèles comme GPT-4o mini sur certaines passes réduit massivement le coût global.

Nous restons à votre entière disposition pour toute question complémentaire sur nos processus d'optimisation de l'API OpenAI.
