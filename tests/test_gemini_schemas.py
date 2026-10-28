from schemas import CriticReport, PlanResponse
from services.copilot import CopilotRefresh


def _find_schema_keywords(value, keywords, path="$"):
    if isinstance(value, dict):
        for keyword in keywords.intersection(value):
            yield path
        for key, child in value.items():
            yield from _find_schema_keywords(child, keywords, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _find_schema_keywords(child, keywords, f"{path}[{index}]")


def test_gemini_response_schemas_do_not_contain_defaults():
    for model in (PlanResponse, CriticReport, CopilotRefresh):
        schema = model.model_json_schema()
        unsupported = list(_find_schema_keywords(schema, {"default", "exclusiveMinimum"}))
        assert unsupported == [], (model.__name__, unsupported)
