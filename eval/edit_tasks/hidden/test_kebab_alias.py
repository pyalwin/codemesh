from pydantic.alias_generators import to_snake


def test_multiple_hyphens_and_case_boundaries():
    assert to_snake("API-TokenValue") == "api_token_value"
    assert to_snake("kebab-Snake") == "kebab_snake"


def test_existing_non_kebab_conversions():
    assert to_snake("camelToSnake") == "camel_to_snake"
    assert to_snake("CAMELToSnake") == "camel_to_snake"

