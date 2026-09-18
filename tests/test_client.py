import responses

from wikibot.client import WikiClient, WikiClientError
from wikibot.wikis import WikiConfig


def _config():
    return WikiConfig(
        name="test",
        site="https://example.wiki.gg",
        bot_username="user@bot",
        secrets_ref="env:UNUSED",
    )


def _logged_in_client() -> WikiClient:
    client = WikiClient(_config())
    client._logged_in = True
    return client


@responses.activate
def test_get_user_rights():
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={"query": {"userinfo": {"id": 1, "name": "user", "rights": ["edit", "upload"]}}},
    )
    client = WikiClient(_config())
    assert client.get_user_rights() == ["edit", "upload"]


@responses.activate
def test_upload_file_success():
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={"query": {"tokens": {"csrftoken": "abc+\\"}}},
    )
    responses.add(
        responses.POST,
        "https://example.wiki.gg/api.php",
        json={"upload": {"result": "Success", "filename": "Flag.png"}},
    )
    client = _logged_in_client()
    result = client.upload_file("Flag.png", b"fake-image-bytes", comment="test upload")
    assert result["result"] == "Success"


@responses.activate
def test_upload_file_warning_raises_by_default():
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={"query": {"tokens": {"csrftoken": "abc+\\"}}},
    )
    responses.add(
        responses.POST,
        "https://example.wiki.gg/api.php",
        json={"upload": {"result": "Warning", "warnings": {"exists": "Flag.png"}}},
    )
    client = _logged_in_client()
    try:
        client.upload_file("Flag.png", b"fake-image-bytes")
        assert False, "expected WikiClientError"
    except WikiClientError as e:
        assert "ignore_warnings" in str(e)


def test_upload_file_requires_login():
    client = WikiClient(_config())
    try:
        client.upload_file("Flag.png", b"data")
        assert False, "expected WikiClientError"
    except WikiClientError as e:
        assert "login" in str(e)


@responses.activate
def test_get_page_with_section_only_requests_that_section():
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={
            "query": {
                "pages": {
                    "1": {
                        "pageid": 1,
                        "title": "Big Page",
                        "revisions": [{"revid": 9, "slots": {"main": {"*": "== Section 2 ==\ncontent\n"}}}],
                    }
                }
            }
        },
    )
    client = WikiClient(_config())
    page = client.get_page("Big Page", section=2)

    assert page.wikitext == "== Section 2 ==\ncontent\n"
    assert page.revid == 9
    sent_params = responses.calls[0].request.params
    assert sent_params["rvsection"] == "2"


@responses.activate
def test_list_sections():
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={
            "parse": {
                "sections": [
                    {"index": "1", "level": "2", "line": "Overview", "anchor": "Overview"},
                    {"index": "2", "level": "2", "line": "Utah", "anchor": "Utah"},
                ]
            }
        },
    )
    client = WikiClient(_config())
    sections = client.list_sections("Big Page")

    assert sections == [
        {"index": "1", "level": "2", "line": "Overview", "anchor": "Overview"},
        {"index": "2", "level": "2", "line": "Utah", "anchor": "Utah"},
    ]


@responses.activate
def test_get_current_revid_fetches_ids_only_no_content():
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={"query": {"pages": {"1": {"pageid": 1, "title": "Big Page", "revisions": [{"revid": 9}]}}}},
    )
    client = WikiClient(_config())
    assert client.get_current_revid("Big Page") == 9
    sent_params = responses.calls[0].request.params
    assert sent_params["rvprop"] == "ids"


@responses.activate
def test_get_current_revid_none_when_missing():
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={"query": {"pages": {"-1": {"title": "Nope", "missing": ""}}}},
    )
    client = WikiClient(_config())
    assert client.get_current_revid("Nope") is None


@responses.activate
def test_edit_page_with_section_sends_section_param():
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={"query": {"tokens": {"csrftoken": "abc+\\"}}},
    )
    responses.add(responses.POST, "https://example.wiki.gg/api.php", json={"edit": {"result": "Success"}})
    client = _logged_in_client()
    client.edit_page("Big Page", "new section text", summary="edit section 2", base_revid=9, section=2)

    sent_body = responses.calls[1].request.body
    assert "section=2" in sent_body
