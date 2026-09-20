import responses
from responses import matchers

from wikibot.client import WikiClient
from wikibot.links import (
    LinkResolver,
    PageIndex,
    find_unresolved_links,
    harvest_page_index,
    load_page_index,
    save_page_index,
)
from wikibot.wikis import WikiConfig


def _config():
    return WikiConfig(
        name="test",
        site="https://example.wiki.gg",
        bot_username="user@bot",
        secrets_ref="env:UNUSED",
    )


def _index():
    return PageIndex(
        titles=["Republic of the Rio Grande", "Some Other Page"],
        redirects={"RRG": "Republic of the Rio Grande"},
    )


def test_resolver_matches_exact_title():
    resolver = LinkResolver(_index())
    assert resolver.resolve("Republic of the Rio Grande") == "Republic of the Rio Grande"


def test_resolver_matches_underscores_and_first_letter_case_insensitively():
    # MediaWiki treats underscores/spaces as interchangeable and only the
    # title's first character as case-insensitive, not every word.
    resolver = LinkResolver(_index())
    assert resolver.resolve("republic of the Rio Grande") == "Republic of the Rio Grande"
    assert resolver.resolve("Republic_of_the_Rio_Grande") == "Republic of the Rio Grande"


def test_resolver_follows_redirect_alias():
    resolver = LinkResolver(_index())
    assert resolver.resolve("RRG") == "Republic of the Rio Grande"


def test_resolver_returns_none_for_unknown_target():
    resolver = LinkResolver(_index())
    assert resolver.resolve("Rio Grande Republic") is None


def test_resolver_suggests_close_match_for_hallucinated_title():
    resolver = LinkResolver(_index())
    suggestions = resolver.suggest("Rio Grande Republic")
    assert "Republic of the Rio Grande" in suggestions


def test_find_unresolved_links_flags_hallucinated_target():
    wikitext = "Only targets the [[Rio Grande Republic]] (RRG)."
    issues = find_unresolved_links(wikitext, _index())

    assert len(issues) == 1
    assert issues[0].target == "Rio Grande Republic"
    assert "Republic of the Rio Grande" in issues[0].suggestions


def test_find_unresolved_links_accepts_real_title_and_redirect_alias():
    wikitext = "See [[Republic of the Rio Grande]] or [[RRG|the RRG]]."
    assert find_unresolved_links(wikitext, _index()) == []


def test_find_unresolved_links_skips_non_content_namespaces_and_sections():
    wikitext = "[[File:Flag.png|thumb]] [[Category:Nations]] [[#Local section]]"
    assert find_unresolved_links(wikitext, _index()) == []


def test_find_unresolved_links_deduplicates_repeated_targets():
    wikitext = "[[Rio Grande Republic]] appears twice: [[Rio Grande Republic|RRG]]."
    issues = find_unresolved_links(wikitext, _index())
    assert len(issues) == 1


@responses.activate
def test_harvest_page_index_paginates_and_resolves_redirects():
    # Non-redirect titles, paginated over two calls via apcontinue.
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={
            "continue": {"apcontinue": "Some Other Page"},
            "query": {"allpages": [{"pageid": 1, "ns": 0, "title": "Republic of the Rio Grande"}]},
        },
        match=[
            matchers.query_param_matcher(
                {
                    "action": "query",
                    "list": "allpages",
                    "apnamespace": "0",
                    "apfilterredir": "nonredirects",
                    "aplimit": "500",
                    "format": "json",
                }
            )
        ],
    )
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={"query": {"allpages": [{"pageid": 2, "ns": 0, "title": "Some Other Page"}]}},
        match=[
            matchers.query_param_matcher(
                {
                    "action": "query",
                    "list": "allpages",
                    "apnamespace": "0",
                    "apfilterredir": "nonredirects",
                    "aplimit": "500",
                    "apcontinue": "Some Other Page",
                    "format": "json",
                }
            )
        ],
    )
    # Redirect titles (no target yet — that needs a separate resolution call).
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={"query": {"allpages": [{"pageid": 3, "ns": 0, "title": "RRG"}]}},
        match=[
            matchers.query_param_matcher(
                {
                    "action": "query",
                    "list": "allpages",
                    "apnamespace": "0",
                    "apfilterredir": "redirects",
                    "aplimit": "500",
                    "format": "json",
                }
            )
        ],
    )
    # Batched titles+redirects=1 lookup resolving each redirect's target.
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={"query": {"redirects": [{"from": "RRG", "to": "Republic of the Rio Grande"}]}},
        match=[matchers.query_param_matcher({"action": "query", "titles": "RRG", "redirects": "1", "format": "json"})],
    )
    client = WikiClient(_config())

    index = harvest_page_index(client)

    assert index.titles == ["Republic of the Rio Grande", "Some Other Page"]
    assert index.redirects == {"RRG": "Republic of the Rio Grande"}


def test_save_and_load_page_index_round_trips(tmp_path):
    path = tmp_path / "pages.json"
    save_page_index(_index(), path)

    loaded = load_page_index(path)

    assert loaded == _index()


def test_load_page_index_returns_none_when_missing(tmp_path):
    assert load_page_index(tmp_path / "nonexistent.json") is None
