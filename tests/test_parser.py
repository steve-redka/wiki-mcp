from wikibot.parser import find_templates, get_params, set_params

WIKITEXT = """
{{Infobox nation
| name = Timekeepers
| stability = 0
| war support = 0
}}
Some prose about the Timekeepers that should be left untouched.
"""


def test_find_templates_case_and_underscore_insensitive():
    assert len(find_templates(WIKITEXT, "infobox_nation")) == 1
    assert len(find_templates(WIKITEXT, "Infobox Nation")) == 1
    assert find_templates(WIKITEXT, "Nonexistent Template") == []


def test_get_params():
    (template,) = find_templates(WIKITEXT, "Infobox nation")
    params = get_params(template)
    assert params == {"name": "Timekeepers", "stability": "0", "war support": "0"}


def test_set_params_only_changes_targeted_fields():
    new_text = set_params(WIKITEXT, "Infobox nation", {"stability": "42"})
    (template,) = find_templates(new_text, "Infobox nation")
    params = get_params(template)
    assert params["stability"] == "42"
    assert params["war support"] == "0"
    assert "Some prose about the Timekeepers" in new_text


def test_set_params_adds_new_param():
    new_text = set_params(WIKITEXT, "Infobox nation", {"unique mechanic": "Chronoshift"})
    (template,) = find_templates(new_text, "Infobox nation")
    assert get_params(template)["unique mechanic"] == "Chronoshift"
