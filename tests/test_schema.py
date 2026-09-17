import responses

from wikibot.client import WikiClient
from wikibot.schema import harvest_by_sampling, harvest_from_doc
from wikibot.wikis import WikiConfig

# Real content from https://oldworldblues.wiki.gg/wiki/Template:Nation/doc
NATION_DOC_WIKITEXT = """This is the infobox for nations

Here is the pre to copy and paste into pages

<pre>
{{Nation
|country_name=
|flag=
|caption=
|country_tag=
|leader=
|capital=
|government=
|faction=
|type=
<!--      Game Information-->
|unique_focus_tree=
|potential_leaders=
|unit_focus=
<!--      Stats-->
|population=
|manpower=
|stability=
|warsupport=
<!--factories-->
|arms=
|civilian=
|naval=
<!--resources-->
|water=
|scrap=
|circuitry=
|energy=
|composite=
|advancedcomps=
}}
</pre>"""


def _config():
    return WikiConfig(
        name="test",
        site="https://example.wiki.gg",
        bot_username="user@bot",
        secrets_ref="env:UNUSED",
    )


@responses.activate
def test_harvest_from_doc_parses_pre_block_example():
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={
            "query": {
                "pages": {
                    "1": {
                        "pageid": 1,
                        "title": "Template:Nation/doc",
                        "revisions": [
                            {"revid": 1, "slots": {"main": {"*": NATION_DOC_WIKITEXT}}}
                        ],
                    }
                }
            }
        },
    )
    client = WikiClient(_config())
    schema = harvest_from_doc(client, "Nation")

    assert schema is not None
    assert schema.source == "doc"
    assert schema.template_name == "Nation"
    assert "country_name" in schema.param_names
    assert "stability" in schema.param_names
    assert "advancedcomps" in schema.param_names
    assert len(schema.param_names) == 25


@responses.activate
def test_harvest_from_doc_returns_none_when_doc_page_missing():
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json={"query": {"pages": {"-1": {"title": "Template:Sic/doc", "missing": ""}}}},
    )
    client = WikiClient(_config())
    assert harvest_from_doc(client, "Sic") is None


def _page_response(title: str, wikitext: str) -> dict:
    return {
        "query": {
            "pages": {
                "1": {
                    "pageid": 1,
                    "title": title,
                    "revisions": [{"revid": 1, "slots": {"main": {"*": wikitext}}}],
                }
            }
        }
    }


@responses.activate
def test_harvest_by_sampling_infers_params_from_real_usage():
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json=_page_response("Timekeepers", "{{Nation|country_name=Timekeepers|stability=0}}"),
    )
    client = WikiClient(_config())
    schema = harvest_by_sampling(client, "Nation", ["Timekeepers"])

    assert schema is not None
    assert schema.source == "inferred"
    assert schema.param_names == {"country_name", "stability"}


@responses.activate
def test_harvest_by_sampling_bare_invocation_is_empty_not_none():
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json=_page_response("Timekeepers", "{{sic}}"),
    )
    client = WikiClient(_config())
    schema = harvest_by_sampling(client, "sic", ["Timekeepers"])

    assert schema is not None
    assert schema.params == []


@responses.activate
def test_harvest_by_sampling_returns_none_when_template_never_used():
    responses.add(
        responses.GET,
        "https://example.wiki.gg/api.php",
        json=_page_response("Timekeepers", "Some unrelated prose, no templates here."),
    )
    client = WikiClient(_config())
    assert harvest_by_sampling(client, "Nation", ["Timekeepers"]) is None
