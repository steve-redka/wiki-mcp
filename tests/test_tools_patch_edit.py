import pytest
import responses
import yaml

from wiki_mcp import registry, tools
from wikibot.links import PageIndex, save_page_index

REVISIONS_RESPONSE = {
    "query": {
        "pages": {
            "1": {
                "pageid": 1,
                "title": "Sandbox",
                "revisions": [{"revid": 5, "slots": {"main": {"*": "The fox is quick.\nThe fox is brown.\n"}}}],
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
def test_propose_patch_edit_shows_diff_for_unique_match(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "patchwiki1", monkeypatch)
    _mock_login()
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)

    result = tools.propose_patch_edit("patchwiki1", "Sandbox", "The fox is brown.", "The fox is red.")

    assert result.changed
    assert "-The fox is brown." in result.diff
    assert "+The fox is red." in result.diff


@responses.activate
def test_propose_patch_edit_no_change_when_strings_identical(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "patchwiki2", monkeypatch)
    _mock_login()
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)

    result = tools.propose_patch_edit("patchwiki2", "Sandbox", "The fox is brown.", "The fox is brown.")

    assert not result.changed


@responses.activate
def test_propose_patch_edit_rejects_missing_old_string(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "patchwiki3", monkeypatch)
    _mock_login()
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)

    with pytest.raises(ValueError, match="not found"):
        tools.propose_patch_edit("patchwiki3", "Sandbox", "The fox is purple.", "The fox is red.")


@responses.activate
def test_propose_patch_edit_rejects_ambiguous_old_string(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "patchwiki4", monkeypatch)
    _mock_login()
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)

    with pytest.raises(ValueError, match="matches 2 times"):
        tools.propose_patch_edit("patchwiki4", "Sandbox", "The fox is", "The dog is")


def test_propose_patch_edit_rejects_new_section(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "patchwiki5", monkeypatch)

    with pytest.raises(ValueError, match="new"):
        tools.propose_patch_edit("patchwiki5", "Sandbox", "old", "new", section="new")


@responses.activate
def test_submit_patch_edit_blocked_in_review_without_confirm(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "patchwiki6", monkeypatch, publish_mode="review")
    _mock_login()
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)

    result = tools.submit_patch_edit(
        "patchwiki6", "Sandbox", "The fox is brown.", "The fox is red.", "test edit"
    )

    assert not result.published
    assert "review" in result.reason


@responses.activate
def test_submit_patch_edit_writes_when_confirmed(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "patchwiki7", monkeypatch, publish_mode="review")
    _mock_login()
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)  # patch's own read
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={"query": {"pages": {"1": {"pageid": 1, "title": "Sandbox", "revisions": [{"revid": 5}]}}}},
    )  # submit's get_current_revid
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json={"query": {"tokens": {"csrftoken": "c"}}})
    responses.add(responses.POST, "https://example.wiki.gg/api.php", json={"edit": {"result": "Success"}})

    result = tools.submit_patch_edit(
        "patchwiki7", "Sandbox", "The fox is brown.", "The fox is red.", "test edit", confirm=True
    )

    assert result.published
    sent_body = responses.calls[-1].request.body
    assert "The+fox+is+red." in sent_body or "The%20fox%20is%20red." in sent_body


@responses.activate
def test_submit_patch_edit_no_change_when_strings_identical(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "patchwiki8", monkeypatch, publish_mode="auto")
    _mock_login()
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)

    result = tools.submit_patch_edit(
        "patchwiki8", "Sandbox", "The fox is brown.", "The fox is brown.", "test edit"
    )

    assert not result.published
    assert "No changes" in result.reason


@responses.activate
def test_submit_patch_edit_with_section_sends_section_param(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, "patchwiki9", monkeypatch, publish_mode="auto")
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
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=section_response)  # patch's own read
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={"query": {"pages": {"1": {"pageid": 1, "title": "Sandbox", "revisions": [{"revid": 5}]}}}},
    )  # submit's get_current_revid
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json={"query": {"tokens": {"csrftoken": "c"}}})
    responses.add(responses.POST, "https://example.wiki.gg/api.php", json={"edit": {"result": "Success"}})

    result = tools.submit_patch_edit(
        "patchwiki9", "Sandbox", "old rows", "new rows", "test edit", section=3
    )

    assert result.published
    sent_body = responses.calls[-1].request.body
    assert "section=3" in sent_body


@responses.activate
def test_propose_patch_edit_flags_unresolved_link_with_suggestion(tmp_path, monkeypatch):
    config_root = _setup_wiki(tmp_path, "patchwiki10", monkeypatch)
    index = PageIndex(titles=["Republic of the Rio Grande"], redirects={})
    save_page_index(index, config_root / "patchwiki10" / "pages.json")
    _mock_login()
    responses.add(responses.GET, "https://example.wiki.gg/api.php", json=REVISIONS_RESPONSE)

    result = tools.propose_patch_edit(
        "patchwiki10", "Sandbox", "The fox is brown.", "Targets the [[Rio Grande Republic]] (RRG)."
    )

    assert len(result.link_issues) == 1
    assert result.link_issues[0].target == "Rio Grande Republic"
    assert "Republic of the Rio Grande" in result.link_issues[0].suggestions
