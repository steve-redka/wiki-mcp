import responses
import yaml

from wiki_mcp import registry, tools

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
