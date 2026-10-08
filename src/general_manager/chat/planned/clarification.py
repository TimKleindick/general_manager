"""Render unresolved choices without accepting model-authored result claims."""

from collections.abc import Mapping

QUESTIONS = {
    "en": {
        "criterion": "Which criterion should I use?",
        "metric": "Which metric should I use?",
        "horizon": "What time period should I consider?",
        "population": "Which records should I include?",
        "customer_identity": "Which customer do you mean?",
        "record_selector": "Which record do you mean?",
        "unit": "Which unit should I use?",
        "forecast_method": "Which forecast method should I use?",
        "future_pricing": "Which future prices or pricing assumptions should I use?",
        "reporting_currency": "Which reporting currency should I use?",
    },
    "de": {
        "criterion": "Welches Kriterium soll ich verwenden?",
        "metric": "Welche Kennzahl soll ich verwenden?",
        "horizon": "Welchen Zeitraum soll ich betrachten?",
        "population": "Welche Datensätze soll ich einbeziehen?",
        "customer_identity": "Welchen Kunden meinst du?",
        "record_selector": "Welchen Datensatz meinst du?",
        "unit": "Welche Einheit soll ich verwenden?",
        "forecast_method": "Welche Prognosemethode soll ich verwenden?",
        "future_pricing": "Welche zukünftigen Preise oder Preisannahmen soll ich verwenden?",
        "reporting_currency": "Welche Berichtswährung soll ich verwenden?",
    },
    "fr": {
        "criterion": "Quel critère dois-je utiliser ?",
        "metric": "Quel indicateur dois-je utiliser ?",
        "horizon": "Quelle période dois-je considérer ?",
        "population": "Quels enregistrements dois-je inclure ?",
        "customer_identity": "De quel client s'agit-il ?",
        "record_selector": "De quel enregistrement s'agit-il ?",
        "unit": "Quelle unité dois-je utiliser ?",
        "forecast_method": "Quelle méthode de prévision dois-je utiliser ?",
        "future_pricing": "Quels prix futurs ou quelles hypothèses de prix dois-je utiliser ?",
        "reporting_currency": "Quelle devise de présentation dois-je utiliser ?",
    },
}
CLARIFICATION_REQUIREMENTS = tuple(QUESTIONS["en"])
CLARIFICATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["clarification"],
    "properties": {
        "clarification": {
            "type": "object",
            "additionalProperties": False,
            "required": ["language", "requirements"],
            "properties": {
                "language": {"enum": list(QUESTIONS)},
                "requirements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": len(CLARIFICATION_REQUIREMENTS),
                    "uniqueItems": True,
                    "items": {"enum": list(CLARIFICATION_REQUIREMENTS)},
                },
            },
        },
    },
}


def render_clarification(value: object) -> str:
    """Only closed choices reach the runtime-owned question templates."""
    if not isinstance(value, Mapping) or set(value) != {"language", "requirements"}:
        message = "invalid clarification fields"
        raise ValueError(message)
    language, requirements = value["language"], value["requirements"]
    if (
        not isinstance(language, str)
        or language not in QUESTIONS
        or not isinstance(requirements, list)
        or not requirements
        or any(
            not isinstance(item, str) or item not in CLARIFICATION_REQUIREMENTS
            for item in requirements
        )
        or len(requirements) != len(set(requirements))
    ):
        message = "invalid clarification choices"
        raise ValueError(message)
    return " ".join(QUESTIONS[language][item] for item in requirements)
