from api.main import _generic_audit_action


def test_delete_actions_are_distinct_from_generic_changes():
    assert _generic_audit_action("DELETE", "/api/papers/3006") == (
        "paper.delete", "paper", "3006",
    )
    assert _generic_audit_action("DELETE", "/api/drafts/638") == (
        "draft.delete", "draft", "638",
    )


def test_non_delete_mutations_keep_compatible_change_actions():
    assert _generic_audit_action("PATCH", "/api/papers/3006") == (
        "paper.change", "paper", "3006",
    )
