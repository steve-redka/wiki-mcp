from wikibot.diff import diff_params


def test_diff_only_includes_changed_params():
    current = {"stability": "0", "war support": "0", "unrelated": "kept"}
    proposed = {"stability": "42", "war support": "0"}
    diff = diff_params("Infobox nation", current, proposed)
    assert [c.name for c in diff.changes] == ["stability"]
    assert diff.changes[0].old_value == "0"
    assert diff.changes[0].new_value == "42"


def test_diff_flags_new_param_with_no_old_value():
    diff = diff_params("Infobox nation", current={}, proposed={"unique mechanic": "Chronoshift"})
    assert diff.changes[0].old_value is None
    assert diff.changes[0].new_value == "Chronoshift"


def test_empty_diff_when_nothing_changes():
    diff = diff_params("Infobox nation", current={"a": "1"}, proposed={"a": "1"})
    assert diff.is_empty
