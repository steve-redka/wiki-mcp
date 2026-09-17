from wikibot.schema import TemplateParam, TemplateSchema
from wikibot.validate import validate_params

SCHEMA = TemplateSchema(
    template_name="Infobox nation",
    source="templatedata",
    params=[
        TemplateParam(name="name", required=True),
        TemplateParam(name="stability", required=False),
    ],
)


def test_unknown_param_is_flagged():
    result = validate_params(SCHEMA, {"name": "Timekeepers", "hallucinated param": "x"})
    assert result.unknown_params == ["hallucinated param"]
    assert not result.ok


def test_missing_required_param_is_flagged():
    result = validate_params(SCHEMA, {"stability": "0"})
    assert result.missing_required_params == ["name"]
    assert not result.ok


def test_valid_params_pass():
    result = validate_params(SCHEMA, {"name": "Timekeepers", "stability": "0"})
    assert result.ok
