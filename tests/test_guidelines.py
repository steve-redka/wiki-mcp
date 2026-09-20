import responses

from wikibot.client import WikiClient
from wikibot.guidelines import (
    CUSTOM_GUIDELINES_TEMPLATE,
    ensure_custom_guidelines_file,
    harvest_guideline_pages,
    load_guidelines_text,
    save_guideline_pages,
)
from wikibot.wikis import WikiConfig


def _config():
    return WikiConfig(
        name="test",
        site="https://example.wiki.gg",
        bot_username="user@bot",
        secrets_ref="env:UNUSED",
    )


@responses.activate
def test_harvest_guideline_pages_skips_missing_titles():
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={
            "query": {
                "pages": {
                    "1": {
                        "pageid": 1,
                        "title": "Project:Manual of Style",
                        "revisions": [{"revid": 1, "slots": {"main": {"*": "Be concise."}}}],
                    }
                }
            }
        },
    )
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={"query": {"pages": {"-1": {"title": "Project:Nope", "missing": ""}}}},
    )
    client = WikiClient(_config())

    pages = harvest_guideline_pages(client, ["Project:Manual of Style", "Project:Nope"])

    assert pages == {"Project:Manual of Style": "Be concise."}


def test_load_guidelines_text_combines_harvested_and_custom(tmp_path):
    directory = tmp_path / "guidelines"
    save_guideline_pages({"Project:Manual of Style": "Use plain language."}, directory)
    (directory / "custom.md").write_text("Don't gloss in-universe tags like (RRG) in prose.")

    text = load_guidelines_text(directory)

    assert "Project:Manual of Style" in text
    assert "Use plain language." in text
    assert "Your personal preferences" in text
    assert "Don't gloss in-universe tags" in text


def test_load_guidelines_text_omits_untouched_custom_template(tmp_path):
    directory = tmp_path / "guidelines"
    ensure_custom_guidelines_file(directory)

    text = load_guidelines_text(directory)

    assert text == ""


def test_ensure_custom_guidelines_file_does_not_overwrite_existing(tmp_path):
    directory = tmp_path / "guidelines"
    path = ensure_custom_guidelines_file(directory)
    assert path.read_text() == CUSTOM_GUIDELINES_TEMPLATE

    path.write_text("My preference.")
    ensure_custom_guidelines_file(directory)

    assert path.read_text() == "My preference."


def test_load_guidelines_text_empty_when_no_cache(tmp_path):
    assert load_guidelines_text(tmp_path / "nonexistent") == ""
