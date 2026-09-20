import responses
import yaml

from wiki_mcp import registry, tools
from wikibot.guidelines import save_guideline_pages
from wikibot.links import PageIndex, save_page_index

REVISIONS_RESPONSE = {
    "query": {
        "pages": {
            "1": {
                "pageid": 1,
                "title": "Sandbox",
                "revisions": [{"revid": 5, "slots": {"main": {"*": "old text\n"}}}],
            }
        }
    }
}


def _setup_wiki(tmp_path, wiki_name, monkeypatch, publish_mode="review"):
    config_root = tmp_path / "config" / "wikis"
    config_root.mkdir(parents=True)
    env_var = f"{wiki_name.upper()}_PW"
    config = {
        "name": wiki_name,
        "site": "https://example.wiki.gg",
        "api_path": "/api.php",
        "bot_username": "user@bot",
        "secrets_ref": f"env:{env_var}",
        "publish_mode": publish_mode,
        "template_schema_dir": "templates/",
        "guideline_pages": [],
    }
    (config_root / f"{wiki_name}.yaml").write_text(yaml.safe_dump(config))
    monkeypatch.setenv(env_var, "secret")
    monkeypatch.setenv("WIKI_MCP_CONFIG_ROOT", str(config_root))
    registry.get_config.cache_clear()
    registry.get_client.cache_clear()
    registry.get_schemas.cache_clear()
    registry.get_guidelines_text.cache_clear()
    registry.get_page_index.cache_clear()
    return config_root


def _mock_login():
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json={"query": {"tokens": {"logintoken": "t"}}})
    responses.add(responses.POST, "https://example.wiki.gg/api.php", json={"login": {"result": "Success"}})


@responses.activate
def test_propose_raw_edit_shows_diff(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "testwiki1", monkeypatch)
    _mock_login()
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)

    result = tools.propose_raw_edit("testwiki1", "Sandbox", "new text\n")

    assert result.changed
    assert "-old text" in result.diff
    assert "+new text" in result.diff


@responses.activate
def test_propose_raw_edit_no_change_when_identical(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "testwiki2", monkeypatch)
    _mock_login()
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)

    result = tools.propose_raw_edit("testwiki2", "Sandbox", "old text\n")

    assert not result.changed


@responses.activate
def test_submit_raw_edit_blocked_in_review_without_confirm(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "testwiki3", monkeypatch, publish_mode="review")
    _mock_login()
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)

    result = tools.submit_raw_edit("testwiki3", "Sandbox", "new text\n", "test edit")

    assert not result.published
    assert "review" in result.reason


@responses.activate
def test_submit_raw_edit_writes_when_confirmed(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "testwiki4", monkeypatch, publish_mode="review")
    _mock_login()
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)  # propose's get_page
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)  # submit's get_page
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json={"query": {"tokens": {"csrftoken": "c"}}})
    responses.add(responses.POST, "https://example.wiki.gg/api.php", json={"edit": {"result": "Success"}})

    result = tools.submit_raw_edit("testwiki4", "Sandbox", "new text\n", "test edit", confirm=True)

    assert result.published


@responses.activate
def test_propose_raw_edit_with_section_only_reads_that_section(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "testwiki5", monkeypatch)
    _mock_login()
    section_response = {
        "query": {
            "pages": {
                "1": {
                    "pageid": 1,
                    "title": "Sandbox",
                    "revisions": [{"revid": 5, "slots": {"main": {"*": "== Utah ==\nold rows\n"}}}],
                }
            }
        }
    }
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=section_response)

    result = tools.propose_raw_edit("testwiki5", "Sandbox", "== Utah ==\nnew rows\n", section=3)

    assert result.changed
    sent_params = responses.calls[-1].request.params
    assert sent_params["rvsection"] == "3"


@responses.activate
def test_propose_raw_edit_new_section_diffs_against_empty_without_reading(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "testwiki6", monkeypatch)
    _mock_login()
    # No REVISIONS_RESPONSE registered for a get_page call: this must not
    # attempt to read anything for section="new", or responses would error
    # on an unmatched request.
    result = tools.propose_raw_edit("testwiki6", "Sandbox", "== New Section ==\ntext\n", section="new")

    assert result.changed
    assert "+== New Section ==" in result.diff


@responses.activate
def test_submit_raw_edit_with_section_sends_section_param(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "testwiki7", monkeypatch, publish_mode="auto")
    _mock_login()
    section_response = {
        "query": {
            "pages": {
                "1": {
                    "pageid": 1,
                    "title": "Sandbox",
                    "revisions": [{"revid": 5, "slots": {"main": {"*": "old rows\n"}}}],
                }
            }
        }
    }
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=section_response)  # propose's read
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={"query": {"pages": {"1": {"pageid": 1, "title": "Sandbox", "revisions": [{"revid": 5}]}}}},
    )  # submit's get_current_revid
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json={"query": {"tokens": {"csrftoken": "c"}}})
    responses.add(responses.POST, "https://example.wiki.gg/api.php", json={"edit": {"result": "Success"}})

    result = tools.submit_raw_edit("testwiki7", "Sandbox", "new rows\n", "test edit", section=3)

    assert result.published
    sent_body = responses.calls[-1].request.body
    assert "section=3" in sent_body


def test_get_guidelines_reads_local_cache_with_no_api_call(tmp_path, monkeypatch):
    config_root = _setup_wiki(tmp_path, "testwiki8", monkeypatch)
    guidelines_dir = config_root / "testwiki8" / "guidelines"
    save_guideline_pages({"Project:Manual of Style": "Keep it in-universe."}, guidelines_dir)

    # No responses registered at all: a live API call here would error on an
    # unmatched request, since get_guidelines must be a pure local read.
    text = tools.get_guidelines("testwiki8")

    assert "Keep it in-universe." in text


def test_get_guidelines_explains_when_nothing_harvested_yet(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "testwiki9", monkeypatch)

    text = tools.get_guidelines("testwiki9")

    assert "No cached guidelines" in text


@responses.activate
def test_propose_raw_edit_flags_unresolved_link_with_suggestion(tmp_path, monkeypatch):
    config_root = _setup_wiki(tmp_path, "testwiki10", monkeypatch)
    index = PageIndex(titles=["Republic of the Rio Grande"], redirects={})
    save_page_index(index, config_root / "testwiki10" / "pages.json")
    _mock_login()
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)

    result = tools.propose_raw_edit(
        "testwiki10", "Sandbox", "Targets the [[Rio Grande Republic]] (RRG).\n"
    )

    assert len(result.link_issues) == 1
    assert result.link_issues[0].target == "Rio Grande Republic"
    assert "Republic of the Rio Grande" in result.link_issues[0].suggestions


@responses.activate
def test_propose_raw_edit_has_no_link_issues_when_no_index_harvested(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "testwiki11", monkeypatch)
    _mock_login()
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)

    result = tools.propose_raw_edit("testwiki11", "Sandbox", "Targets [[Anything]].\n")

    assert result.link_issues == []
