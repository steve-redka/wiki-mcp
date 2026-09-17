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
