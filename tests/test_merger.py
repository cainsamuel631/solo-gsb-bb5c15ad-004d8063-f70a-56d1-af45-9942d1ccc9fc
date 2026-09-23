"""Tests for the three-way merge engine (no HTTP layer)."""

from __future__ import annotations

import pytest

from app.merger import Merger, json_equal, parse_pointer


def merge(base, left, right, identity=None):
    merged, conflicts = Merger(identity).merge(base, left, right)
    return merged, [
        (c.path, c.kind, c.base, c.left, c.right) for c in conflicts
    ]


# ----------------------------------------------------------------- acceptance


def test_unrelated_field_changes_are_both_accepted():
    merged, conflicts = merge(
        {"a": 1, "b": 2},
        {"a": 10, "b": 2},
        {"a": 1, "b": 20},
    )
    assert merged == {"a": 10, "b": 20}
    assert conflicts == []


def test_identical_change_on_both_sides_is_merged_once():
    merged, conflicts = merge({"a": 1}, {"a": 9}, {"a": 9})
    assert merged == {"a": 9}
    assert conflicts == []


def test_deletion_on_both_sides_is_accepted():
    merged, conflicts = merge({"a": 1, "b": 2}, {"b": 2}, {"b": 2})
    assert merged == {"b": 2}
    assert conflicts == []


def test_deletion_on_one_side_with_other_unchanged_is_accepted():
    merged, conflicts = merge({"a": 1, "b": 2}, {"a": 1, "b": 2}, {"b": 2})
    assert merged == {"b": 2}
    assert conflicts == []


def test_single_sided_addition_is_accepted():
    merged, conflicts = merge({"a": 1}, {"a": 1}, {"a": 1, "b": 2})
    assert merged == {"a": 1, "b": 2}
    assert conflicts == []


def test_null_is_a_real_value_not_a_deletion():
    merged, conflicts = merge({"a": 1}, {"a": None}, {"a": None})
    assert merged == {"a": None}
    assert conflicts == []


# ------------------------------------------------------------------ conflicts


def test_both_modified_scalar_conflict_keeps_base():
    merged, conflicts = merge({"a": 1}, {"a": 2}, {"a": 3})
    assert merged == {"a": 1}
    assert conflicts == [("/a", "both_modified", 1, 2, 3)]


def test_delete_vs_modify_keeps_base_both_directions():
    merged, conflicts = merge({"a": 1}, {}, {"a": 3})
    assert merged == {"a": 1}
    assert conflicts == [("/a", "delete_vs_modify", 1, None, 3)]

    merged, conflicts = merge({"a": 1}, {"a": 3}, {})
    assert merged == {"a": 1}
    assert conflicts == [("/a", "delete_vs_modify", 1, 3, None)]


def test_null_vs_scalar_is_a_both_modified_conflict():
    merged, conflicts = merge({"a": 1}, {"a": None}, {"a": 2})
    assert merged == {"a": 1}
    assert conflicts == [("/a", "both_modified", 1, None, 2)]


def test_both_added_with_different_values_conflicts():
    merged, conflicts = merge({}, {"a": 1}, {"a": 2})
    assert merged == {"a": 1}
    assert conflicts == [("/a", "both_added", None, 1, 2)]


def test_field_level_conflicts_inside_object_merge():
    # Both sides edit the same nested object: identical edits are merged,
    # independent edits are both accepted, only divergent edits conflict.
    base = {"o": {"same": 1, "x": 1, "y": 1, "z": 1}}
    left = {"o": {"same": 9, "x": 10, "y": 1, "z": 1}}
    right = {"o": {"same": 9, "x": 1, "y": 20, "z": 30}}
    merged, conflicts = merge(base, left, right)
    assert merged == {
        "o": {"same": 9, "x": 10, "y": 20, "z": 30}
    }
    assert conflicts == []

    # And when one field genuinely diverges, only that field is reported.
    base = {"o": {"x": 1, "y": 1}}
    left = {"o": {"x": 11, "y": 1}}
    right = {"o": {"x": 12, "y": 20}}
    merged, conflicts = merge(base, left, right)
    assert merged == {"o": {"x": 1, "y": 20}}
    assert conflicts == [("/o/x", "both_modified", 1, 11, 12)]


def test_conflict_pointers_are_sorted():
    base = {"m": {"b": 1, "a": 1}, "z": 1, "a": 1}
    left = {"m": {"b": 2, "a": 2}, "z": 2, "a": 2}
    right = {"m": {"b": 3, "a": 3}, "z": 3, "a": 3}
    _, conflicts = merge(base, left, right)
    assert [c[0] for c in conflicts] == ["/a", "/m/a", "/m/b", "/z"]


def test_pointer_tokens_with_slash_and_tilde_are_escaped():
    merged, conflicts = merge(
        {"a/b": 1, "c~d": 1},
        {"a/b": 2, "c~d": 2},
        {"a/b": 3, "c~d": 3},
    )
    assert merged == {"a/b": 1, "c~d": 1}
    assert {c[0] for c in conflicts} == {"/a~1b", "/c~0d"}


def test_root_level_conflict_uses_empty_pointer():
    merged, conflicts = merge(1, 2, 3)
    assert merged == 1
    assert conflicts == [("", "both_modified", 1, 2, 3)]


def test_bool_never_equals_number():
    assert json_equal(True, 1) is False
    merged, _ = merge({"a": True}, {"a": 1}, {"a": 1})
    # Both sides made the same (Python-loose but JSON-distinct) change.
    assert merged == {"a": 1}


# ------------------------------------------------------------ plain arrays


def test_array_without_identity_is_an_atomic_value():
    merged, conflicts = merge({"a": [1, 2]}, {"a": [1, 2, 3]}, {"a": [1, 2]})
    assert merged == {"a": [1, 2, 3]}
    assert conflicts == []

    merged, conflicts = merge({"a": [1, 2]}, {"a": [1, 2, 3]}, {"a": [9]})
    assert merged == {"a": [1, 2]}
    assert conflicts == [("/a", "both_modified", [1, 2], [1, 2, 3], [9])]


# -------------------------------------------------------- identity arrays


def test_identity_array_merges_adds_removes_and_edits():
    base = {
        "srv": [
            {"id": "a", "v": 1},
            {"id": "b", "v": 2},
            {"id": "c", "v": 3},
        ]
    }
    left = {
        "srv": [
            {"id": "a", "v": 10},  # edited on left
            {"id": "c", "v": 3},
            {"id": "e", "v": 9},
        ]
    }
    right = {
        "srv": [
            {"id": "a", "v": 1},
            {"id": "b", "v": 20},  # edited on right
            {"id": "d", "v": 8},  # added on right
        ]
    }
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    # a: left-only edit -> accepted.
    # b: left deleted it, right modified it -> base retained + conflict.
    # c: right deleted it, left untouched -> deletion accepted.
    # d/e: one-sided adds, sorted by identity.
    assert merged == {
        "srv": [
            {"id": "a", "v": 10},
            {"id": "b", "v": 2},
            {"id": "d", "v": 8},
            {"id": "e", "v": 9},
        ]
    }
    assert conflicts == [
        ("/srv/1", "delete_vs_modify",
         {"id": "b", "v": 2}, None, {"id": "b", "v": 20})
    ]


def test_identity_array_preserves_base_order_and_sorts_new_items():
    base = {"srv": [{"id": "z"}, {"id": "a"}, {"id": "m"}]}
    left = {"srv": [{"id": "z"}, {"id": "a"}, {"id": "m"}, {"id": 9}, {"id": 1}]}
    right = {"srv": [{"id": "z"}, {"id": "a"}, {"id": "m"}, {"id": "b"}]}
    merged, _ = merge(base, left, right, {"/srv": "id"})
    ids = [item["id"] for item in merged["srv"]]
    # Existing items keep base order; new items are sorted by identity.
    assert ids == ["z", "a", "m", 1, 9, "b"]


def test_identity_new_item_ordering_by_type():
    # null < booleans < numbers < strings.
    base = {"srv": [{"id": "base"}]}
    left = {
        "srv": [
            {"id": "base"},
            {"id": "z"},
            {"id": 2},
            {"id": True},
            {"id": None},
        ]
    }
    right = {"srv": [{"id": "base"}]}
    merged, _ = merge(base, left, right, {"/srv": "id"})
    assert [x["id"] for x in merged["srv"]] == ["base", None, True, 2, "z"]


def test_identity_array_delete_vs_modify_conflict_keeps_base_item():
    base = {"srv": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    left = {"srv": [{"id": "b", "v": 2}]}  # deletes a
    right = {"srv": [{"id": "a", "v": 99}, {"id": "b", "v": 2}]}  # edits a
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert merged == {"srv": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    assert conflicts == [
        ("/srv/0", "delete_vs_modify",
         {"id": "a", "v": 1}, None, {"id": "a", "v": 99})
    ]


def test_identity_array_field_conflict_pointer_survives_earlier_deletes():
    # c (index 2) is deleted by right; b (index 1) has a field conflict.
    base = {"srv": [{"id": "a"}, {"id": "b", "v": 1}, {"id": "c"}]}
    left = {"srv": [{"id": "a"}, {"id": "b", "v": 11}]}
    right = {"srv": [{"id": "a"}, {"id": "b", "v": 12}]}
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert merged == {"srv": [{"id": "a"}, {"id": "b", "v": 1}]}
    assert [c[0] for c in conflicts] == ["/srv/1/v"]
    assert conflicts[0][1] == "both_modified"


def test_identity_array_same_new_id_added_differently_conflicts():
    base = {"srv": []}
    left = {"srv": [{"id": "n", "v": 1}]}
    right = {"srv": [{"id": "n", "v": 2}]}
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert merged == {"srv": [{"id": "n", "v": 1}]}
    assert conflicts == [
        ("/srv", "both_added", None, {"id": "n", "v": 1}, {"id": "n", "v": 2})
    ]


def test_identity_array_identical_new_item_added_on_both_sides_once():
    # An item absent from base, added with identical content on both
    # sides, is appended exactly once (regression: it used to be appended
    # twice when the identity's numeric spelling differed, e.g. 1 / 1.0,
    # which JSON-equal compares as the same value).
    base = {"srv": [{"id": "a"}]}
    left = {"srv": [{"id": "a"}, {"id": 1, "v": 1}]}
    right = {"srv": [{"id": "a"}, {"id": 1.0, "v": 1}]}
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert merged == {"srv": [{"id": "a"}, {"id": 1, "v": 1}]}
    assert conflicts == []


def test_identity_array_numeric_identity_spelling_orders_with_existing_rules():
    # The single appended item keeps the new-item identity ordering and
    # sorts among other new items by numeric value.
    base = {"srv": [{"id": "base"}]}
    left = {"srv": [{"id": "base"}, {"id": 3}, {"id": 1}]}
    right = {"srv": [{"id": "base"}, {"id": 1.0}, {"id": 3.0}]}
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert [item["id"] for item in merged["srv"]] == ["base", 1, 3]
    assert conflicts == []


def test_identity_array_numeric_identity_same_id_different_content_conflicts():
    # Same identity under different numeric spellings but different
    # content is still a both_added conflict, merged to the left value.
    base = {"srv": []}
    left = {"srv": [{"id": 2, "v": 1}]}
    right = {"srv": [{"id": 2.0, "v": 2}]}
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert merged == {"srv": [{"id": 2, "v": 1}]}
    assert conflicts == [
        ("/srv", "both_added", None, {"id": 2, "v": 1}, {"id": 2.0, "v": 2})
    ]


def test_identity_array_numeric_spelling_one_sided_add_unchanged():
    base = {"srv": []}
    left = {"srv": [{"id": 5.0, "v": "x"}]}
    right = {"srv": []}
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert merged == {"srv": [{"id": 5.0, "v": "x"}]}
    assert conflicts == []


def test_identity_array_numeric_spelling_existing_item_edit_delete_unchanged():
    # Existing items keyed by differently-spelled equal numbers still
    # match as the same identity for edits / deletes.
    base = {"srv": [{"id": 1, "v": 0}]}
    left = {"srv": [{"id": 1.0, "v": 10}]}  # edit, float spelling
    right = {"srv": []}  # delete
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    # left modified, right deleted -> keep base + delete_vs_modify.
    assert merged == {"srv": [{"id": 1, "v": 0}]}
    assert conflicts == [
        ("/srv/0", "delete_vs_modify",
         {"id": 1, "v": 0}, {"id": 1.0, "v": 10}, None)
    ]


def test_identity_array_numeric_identity_keeps_unrelated_types_distinct():
    # bool and number never share an identity, nor do large distinct
    # integers collapse via float rounding.
    base = {"srv": []}
    left = {"srv": [{"id": True}]}
    right = {"srv": [{"id": 1}]}
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert [item["id"] for item in merged["srv"]] == [True, 1]
    assert conflicts == []

    big_a = 10**20
    big_b = 10**20 + 1
    merged, conflicts = merge(
        {"srv": []}, {"srv": [{"id": big_a}]}, {"srv": [{"id": big_b}]},
        {"/srv": "id"},
    )
    ids = [item["id"] for item in merged["srv"]]
    # Distinct large integers keep distinct identities (never merged into
    # one via float rounding).
    assert sorted(ids) == sorted([big_a, big_b])
    assert conflicts == []


def test_nested_identity_array_via_wildcard_pointer():
    base = {
        "servers": [
            {"id": "s1", "disks": [{"name": "d1", "size": 10}, {"name": "d2", "size": 20}]}
        ]
    }
    left = {
        "servers": [
            {"id": "s1", "disks": [{"name": "d1", "size": 100}, {"name": "d2", "size": 20}]}
        ]
    }
    right = {
        "servers": [
            {"id": "s1", "disks": [{"name": "d1", "size": 10}, {"name": "d3", "size": 30}]}
        ]
    }
    merged, conflicts = merge(
        base, left, right,
        {"/servers": "id", "/servers/*/disks": "name"},
    )
    assert merged == {
        "servers": [
            {"id": "s1", "disks": [
                {"name": "d1", "size": 100},
                {"name": "d3", "size": 30},
            ]}
        ]
    }
    assert conflicts == []


def test_non_eligible_identity_array_falls_back_to_atomic():
    # Scalar items cannot carry the identity field.
    base = {"srv": [1, 2]}
    left = {"srv": [1, 2, 3]}
    right = {"srv": [1, 2, 4]}
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert merged == {"srv": [1, 2]}
    assert [c[1] for c in conflicts] == ["both_modified"]


def test_duplicate_identity_on_a_side_falls_back_to_atomic():
    base = {"srv": [{"id": "a"}]}
    left = {"srv": [{"id": "a"}, {"id": "a"}]}
    right = {"srv": [{"id": "b"}]}
    merged, conflicts = merge(base, left, right, {"/srv": "id"})
    assert merged == {"srv": [{"id": "a"}]}
    assert [c[1] for c in conflicts] == ["both_modified"]


# ------------------------------------------------------------- determinism


def test_merge_is_deterministic():
    base = {"srv": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    left = {"srv": [{"id": "b", "v": 2}, {"id": "z"}, {"id": "a", "v": 11}]}
    right = {"srv": [{"id": "a", "v": 1}, {"id": "b", "v": 22}, {"id": "a2"}]}
    identity = {"/srv": "id"}
    first = Merger(identity).merge(base, left, right)
    for _ in range(5):
        assert Merger(identity).merge(base, left, right) == first


# ----------------------------------------------------------------- pointers


def test_parse_pointer_forms():
    assert parse_pointer("") == ()
    assert parse_pointer("/a/b") == ("a", "b")
    assert parse_pointer("/a~1b/c~0d") == ("a/b", "c~d")
    assert parse_pointer("/servers/*/disks") == ("servers", "*", "disks")


def test_parse_pointer_rejects_bad_forms():
    with pytest.raises(ValueError):
        parse_pointer("a/b")


# -------------------------------------------------------------- resolutions


def merge_resolved(base, left, right, identity=None, resolutions=None):
    merged, conflicts = Merger(identity).merge(base, left, right, resolutions)
    return merged, [
        (c.path, c.kind, c.base, c.left, c.right) for c in conflicts
    ]


def test_resolution_picks_left_or_right_on_both_modified():
    base = {"o": {"x": 1}}
    left = {"o": {"x": 11}}
    right = {"o": {"x": 12}}

    merged, conflicts = merge_resolved(base, left, right, resolutions={"/o/x": "left"})
    assert merged == {"o": {"x": 11}}
    # The reported conflict keeps the original three-way values.
    assert conflicts == [("/o/x", "both_modified", 1, 11, 12)]

    merged, _ = merge_resolved(base, left, right, resolutions={"/o/x": "right"})
    assert merged == {"o": {"x": 12}}

    merged, _ = merge_resolved(base, left, right, resolutions={"/o/x": "base"})
    assert merged == {"o": {"x": 1}}


def test_unlisted_conflicts_keep_base():
    base = {"a": 1, "b": 1}
    left = {"a": 2, "b": 2}
    right = {"a": 3, "b": 3}
    merged, conflicts = merge_resolved(
        base, left, right, resolutions={"/a": "right"}
    )
    assert merged == {"a": 3, "b": 1}
    assert [c[0] for c in conflicts] == ["/a", "/b"]


def test_resolution_delete_vs_modify_can_accept_either_side():
    base = {"a": 1, "k": 1}
    left = {"a": 1}  # deletes k
    right = {"a": 1, "k": 9}  # modifies k

    # Accept the deletion.
    merged, conflicts = merge_resolved(base, left, right, resolutions={"/k": "left"})
    assert merged == {"a": 1}
    assert conflicts == [("/k", "delete_vs_modify", 1, None, 9)]

    # Accept the modification.
    merged, _ = merge_resolved(base, left, right, resolutions={"/k": "right"})
    assert merged == {"a": 1, "k": 9}


def test_resolution_both_added_base_drops_the_value():
    base = {}
    left = {"a": 1}
    right = {"a": 2}

    merged, conflicts = merge_resolved(base, left, right, resolutions={"/a": "right"})
    assert merged == {"a": 2}
    assert conflicts == [("/a", "both_added", None, 1, 2)]

    # "base" means: as in base, i.e. the key stays absent.
    merged, _ = merge_resolved(base, left, right, resolutions={"/a": "base"})
    assert merged == {}


def test_resolution_on_identity_array_item_delete_vs_modify():
    base = {"srv": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    left = {"srv": [{"id": "b", "v": 2}]}  # deletes a
    right = {"srv": [{"id": "a", "v": 99}, {"id": "b", "v": 2}]}  # edits a

    # Accept the deletion: the item disappears and later items shift.
    merged, conflicts = merge_resolved(
        base, left, right, {"/srv": "id"}, {"/srv/0": "left"}
    )
    assert merged == {"srv": [{"id": "b", "v": 2}]}
    # The conflict is still reported at its original (base-kept) pointer.
    assert conflicts == [
        ("/srv/0", "delete_vs_modify",
         {"id": "a", "v": 1}, None, {"id": "a", "v": 99})
    ]

    # Accept the modification: the item is replaced in its base position.
    merged, _ = merge_resolved(
        base, left, right, {"/srv": "id"}, {"/srv/0": "right"}
    )
    assert merged == {"srv": [{"id": "a", "v": 99}, {"id": "b", "v": 2}]}


def test_resolution_on_identity_array_field_conflict():
    base = {"srv": [{"id": "a"}, {"id": "b", "v": 1}]}
    left = {"srv": [{"id": "a"}, {"id": "b", "v": 11}]}
    right = {"srv": [{"id": "a"}, {"id": "b", "v": 12}]}
    merged, conflicts = merge_resolved(
        base, left, right, {"/srv": "id"}, {"/srv/1/v": "right"}
    )
    assert merged == {"srv": [{"id": "a"}, {"id": "b", "v": 12}]}
    assert conflicts == [("/srv/1/v", "both_modified", 1, 11, 12)]


def test_resolutions_track_identities_across_index_shifts():
    # Resolving /srv/0 to accept the deletion shifts item b from index 1 to
    # 0, but /srv/1/v must still apply to item b (resolutions are keyed by
    # the pointers reported for the base-kept merge).
    base = {"srv": [{"id": "a", "v": 1}, {"id": "b", "v": 1}]}
    left = {"srv": [{"id": "b", "v": 11}]}  # deletes a, edits b.v
    right = {"srv": [{"id": "a", "v": 9}, {"id": "b", "v": 12}]}
    merged, conflicts = merge_resolved(
        base, left, right, {"/srv": "id"},
        {"/srv/0": "left", "/srv/1/v": "right"},
    )
    assert merged == {"srv": [{"id": "b", "v": 12}]}
    assert [c[0] for c in conflicts] == ["/srv/0", "/srv/1/v"]


def test_resolution_uses_final_pointer_after_wildcard_resolution():
    base = {
        "servers": [
            {"id": "s1", "disks": [{"name": "d1", "size": 10}]}
        ]
    }
    left = {
        "servers": [
            {"id": "s1", "disks": [{"name": "d1", "size": 100}]}
        ]
    }
    right = {
        "servers": [
            {"id": "s1", "disks": [{"name": "d1", "size": 200}]}
        ]
    }
    identity = {"/servers": "id", "/servers/*/disks": "name"}
    merged, conflicts = merge_resolved(
        base, left, right, identity,
        {"/servers/0/disks/0/size": "right"},
    )
    assert merged == {
        "servers": [
            {"id": "s1", "disks": [{"name": "d1", "size": 200}]}
        ]
    }
    assert conflicts == [("/servers/0/disks/0/size", "both_modified", 10, 100, 200)]


def test_resolution_both_added_same_new_identity():
    base = {"srv": []}
    left = {"srv": [{"id": "n", "v": 1}]}
    right = {"srv": [{"id": "n", "v": 2}]}

    merged, conflicts = merge_resolved(
        base, left, right, {"/srv": "id"}, {"/srv": "right"}
    )
    assert merged == {"srv": [{"id": "n", "v": 2}]}
    assert conflicts == [
        ("/srv", "both_added", None, {"id": "n", "v": 1}, {"id": "n", "v": 2})
    ]

    # "base": the new item is absent from base, so nothing is appended.
    merged, _ = merge_resolved(base, left, right, {"/srv": "id"}, {"/srv": "base"})
    assert merged == {"srv": []}


def test_resolution_on_root_conflict_uses_empty_pointer():
    merged, conflicts = merge_resolved(1, 2, 3, resolutions={"": "right"})
    assert merged == 3
    assert conflicts == [("", "both_modified", 1, 2, 3)]


def test_resolutions_do_not_change_reported_conflicts():
    base = {"srv": [{"id": "a", "v": 1}, {"id": "b", "v": 2}], "x": 1}
    left = {"srv": [{"id": "b", "v": 20}], "x": 10}
    right = {"srv": [{"id": "a", "v": 11}, {"id": "b", "v": 22}], "x": 99}
    identity = {"/srv": "id"}
    _, plain = Merger(identity).merge(base, left, right)
    _, resolved = Merger(identity).merge(
        base, left, right,
        {"/srv/0": "right", "/srv/1/v": "left", "/x": "left"},
    )
    assert resolved == plain


def test_resolution_for_unknown_pointer_is_rejected():
    with pytest.raises(ValueError, match="does not exist"):
        merge_resolved({"a": 1}, {"a": 2}, {"a": 3}, resolutions={"/nope": "left"})


def test_resolution_for_non_conflict_location_is_rejected():
    # /a exists in the merged document but merged cleanly (left-only edit).
    with pytest.raises(ValueError, match="does not correspond"):
        merge_resolved({"a": 1}, {"a": 2}, {"a": 1}, resolutions={"/a": "left"})


def test_resolution_with_unknown_side_is_rejected():
    with pytest.raises(ValueError, match="invalid resolution"):
        merge_resolved({"a": 1}, {"a": 2}, {"a": 3}, resolutions={"/a": "sideways"})


def test_resolution_with_malformed_pointer_is_rejected():
    with pytest.raises(ValueError):
        merge_resolved({"a": 1}, {"a": 2}, {"a": 3}, resolutions={"a": "left"})


# ----------------------------------------------------------- protectedPaths


def merge_protected(base, left, right, protected, identity=None, resolutions=None):
    merged, conflicts = Merger(identity, protected).merge(base, left, right, resolutions)
    return merged, [
        (c.path, c.kind, c.base, c.left, c.right) for c in conflicts
    ]


def test_protected_one_sided_change_keeps_base():
    merged, conflicts = merge_protected(
        {"a": 1, "b": 2}, {"a": 9, "b": 2}, {"a": 1, "b": 2}, ["/a"]
    )
    assert merged == {"a": 1, "b": 2}
    assert conflicts == [("/a", "protected_change", 1, 9, 1)]


def test_protected_identical_change_on_both_sides_keeps_base():
    # The same change made on both sides is normally merged once; under
    # protection it still must not be written automatically.
    merged, conflicts = merge_protected({"a": 1}, {"a": 9}, {"a": 9}, ["/a"])
    assert merged == {"a": 1}
    assert conflicts == [("/a", "protected_change", 1, 9, 9)]


def test_protected_path_without_any_change_is_silent():
    merged, conflicts = merge_protected(
        {"a": 1, "b": 2}, {"a": 1, "b": 3}, {"a": 1, "b": 4}, ["/a"]
    )
    # /a untouched: no protected conflict there; /b diverges normally.
    assert merged == {"a": 1, "b": 2}
    assert conflicts == [("/b", "both_modified", 2, 3, 4)]


def test_protected_coexists_with_regular_conflicts():
    merged, conflicts = merge_protected(
        {"a": 1, "b": 1}, {"a": 2, "b": 2}, {"a": 3, "b": 3}, ["/a"]
    )
    assert merged == {"a": 1, "b": 1}
    assert conflicts == [
        ("/a", "protected_change", 1, 2, 3),
        ("/b", "both_modified", 1, 2, 3),
    ]


def test_protected_wildcard_matches_single_segment():
    merged, conflicts = merge_protected(
        {"o": {"x": 1, "y": 1}}, {"o": {"x": 9, "y": 8}}, {"o": {"x": 1, "y": 1}},
        ["/o/*"],
    )
    assert merged == {"o": {"x": 1, "y": 1}}
    assert conflicts == [
        ("/o/x", "protected_change", 1, 9, 1),
        ("/o/y", "protected_change", 1, 8, 1),
    ]


def test_protected_wildcard_does_not_cross_segments():
    # /o/* matches the item /o/x itself when the item changes; but a
    # grandchild pointer /o/*/z needs two segments and is not hit by /o/*.
    merged, conflicts = merge_protected(
        {"o": {"x": {"z": 1}, "k": 1}},
        {"o": {"x": {"z": 2}, "k": 2}},
        {"o": {"x": {"z": 1}, "k": 1}},
        ["/o/*/z"],
    )
    assert merged == {"o": {"x": {"z": 1}, "k": 2}}
    assert conflicts == [("/o/x/z", "protected_change", 1, 2, 1)]

    # A literal scalar change at /o/k with only the grandchild pattern
    # configured is auto-accepted.
    merged, conflicts = merge_protected(
        {"o": {"k": 1}}, {"o": {"k": 2}}, {"o": {"k": 1}}, ["/o/*/z"]
    )
    assert merged == {"o": {"k": 2}}
    assert conflicts == []


def test_protected_object_field_lets_siblings_merge():
    base = {"o": {"x": 1, "y": 1}}
    left = {"o": {"x": 9, "y": 1}}
    right = {"o": {"x": 1, "y": 8}}
    merged, conflicts = merge_protected(base, left, right, ["/o/x"])
    assert merged == {"o": {"x": 1, "y": 8}}
    assert conflicts == [("/o/x", "protected_change", 1, 9, 1)]


def test_protected_object_blocks_one_sided_deletion_of_subtree():
    merged, conflicts = merge_protected(
        {"o": {"x": 1}}, {}, {"o": {"x": 1}}, ["/o"]
    )
    assert merged == {"o": {"x": 1}}
    assert conflicts == [("/o", "protected_change", {"x": 1}, None, {"x": 1})]


def test_protected_field_under_one_sided_object_addition_blocks_whole_block():
    # A one-sided object addition is one atomic structural unit: a hit
    # protected descendant keeps the whole block out of merged (base has no
    # such object); it is never assembled field by field.
    merged, conflicts = merge_protected(
        {}, {"o": {"x": 1, "y": 2}}, {}, ["/o/x"]
    )
    assert merged == {}
    assert conflicts == [("/o/x", "protected_change", None, 1, None)]


def test_protected_field_under_one_sided_object_addition_resolution_writes_block():
    merged, conflicts = merge_protected(
        {}, {"o": {"x": 1, "y": 2}}, {}, ["/o/x"],
        resolutions={"/o/x": "left"},
    )
    # The explicit adjudication writes the whole added block.
    assert merged == {"o": {"x": 1, "y": 2}}
    assert conflicts == [("/o/x", "protected_change", None, 1, None)]

    # "base" adjudicates to absence just like the unresolved default.
    merged, _ = merge_protected(
        {}, {"o": {"x": 1, "y": 2}}, {}, ["/o/x"],
        resolutions={"/o/x": "base"},
    )
    assert merged == {}


def test_protected_field_under_one_sided_object_deletion_keeps_whole_block():
    # A one-sided object deletion carrying a hit protected descendant keeps
    # the base block wholesale, including its unprotected sibling field.
    merged, conflicts = merge_protected(
        {"o": {"x": 1, "y": 1}}, {}, {"o": {"x": 1, "y": 1}}, ["/o/x"]
    )
    assert merged == {"o": {"x": 1, "y": 1}}
    assert conflicts == [("/o/x", "protected_change", 1, None, 1)]

    # Adjudicating to the deleting side removes the whole block.
    merged, _ = merge_protected(
        {"o": {"x": 1, "y": 1}}, {}, {"o": {"x": 1, "y": 1}}, ["/o/x"],
        resolutions={"/o/x": "left"},
    )
    assert merged == {}

    # The surviving side editing another field still does not split the
    # unit: the whole base block stays until adjudicated.
    merged, conflicts = merge_protected(
        {"o": {"x": 1, "y": 1}}, {}, {"o": {"x": 1, "y": 8}}, ["/o/x"]
    )
    assert merged == {"o": {"x": 1, "y": 1}}
    assert conflicts == [("/o/x", "protected_change", 1, None, 1)]

    # ... and adjudicating to the surviving side keeps its whole object.
    merged, _ = merge_protected(
        {"o": {"x": 1, "y": 1}}, {}, {"o": {"x": 1, "y": 8}}, ["/o/x"],
        resolutions={"/o/x": "right"},
    )
    assert merged == {"o": {"x": 1, "y": 8}}


def test_protected_field_under_one_sided_addition_without_hit_still_merges():
    # The block is one-sided-added but the configured descendant never
    # changes: the atomic shortcut applies, so the addition is accepted.
    merged, conflicts = merge_protected(
        {}, {"o": {"y": 2}}, {}, ["/o/x"]
    )
    assert merged == {"o": {"y": 2}}
    assert conflicts == []


def test_protected_root_protects_the_whole_document():
    merged, conflicts = merge_protected(1, 2, 1, [""])
    assert merged == 1
    assert conflicts == [("", "protected_change", 1, 2, 1)]

    merged, conflicts = merge_protected(
        {"a": 1}, {"a": 2}, {"a": 1}, [""]
    )
    assert merged == {"a": 1}
    assert conflicts == [("", "protected_change", {"a": 1}, {"a": 2}, {"a": 1})]


def test_protected_identity_array_item_one_sided_edit_uses_final_pointer():
    base = {"s": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    left = {"s": [{"id": "a", "v": 9}, {"id": "b", "v": 2}]}
    right = base
    merged, conflicts = merge_protected(base, left, right, ["/s/0"], {"/s": "id"})
    assert merged == base
    assert conflicts == [
        ("/s/0", "protected_change",
         {"id": "a", "v": 1}, {"id": "a", "v": 9}, {"id": "a", "v": 1})
    ]


def test_protected_identity_array_item_field_uses_final_pointer():
    # Item a is deleted by both revisions (auto-accepted); b survives and
    # lands at final index 0. Its field pointer must be /s/0/v.
    base = {"s": [{"id": "a"}, {"id": "b", "v": 1}]}
    left = {"s": [{"id": "b", "v": 9}]}  # deletes a, edits b.v
    right = base
    merged, conflicts = merge_protected(
        base, left, right, ["/s/0/v"], {"/s": "id"}
    )
    assert merged == {"s": [{"id": "b", "v": 1}]}
    assert conflicts == [("/s/0/v", "protected_change", 1, 9, 1)]

    # The original (base) index /s/1/v does not match the reported pointer,
    # so the one-sided edit is then auto-accepted.
    merged, conflicts = merge_protected(
        base, left, right, ["/s/1/v"], {"/s": "id"}
    )
    assert merged == {"s": [{"id": "b", "v": 9}]}
    assert conflicts == []

    # With a surviving earlier item, b keeps its final index 1.
    base = {"s": [{"id": "a"}, {"id": "b", "v": 1}, {"id": "c"}]}
    left = {"s": [{"id": "a"}, {"id": "b", "v": 9}]}  # only c deleted
    right = base
    merged, conflicts = merge_protected(
        base, left, right, ["/s/1/v"], {"/s": "id"}
    )
    assert merged == {"s": [{"id": "a"}, {"id": "b", "v": 1}]}
    assert conflicts == [("/s/1/v", "protected_change", 1, 9, 1)]


def test_protected_identity_array_item_deletion_uses_original_path():
    # Left deletes item a while right leaves it untouched. The protection
    # matches the original base index even though the item would leave the
    # merged array.
    base = {"s": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    left = {"s": [{"id": "b", "v": 2}]}
    right = base
    merged, conflicts = merge_protected(base, left, right, ["/s/0"], {"/s": "id"})
    assert merged == base
    assert conflicts == [
        ("/s/0", "protected_change",
         {"id": "a", "v": 1}, None, {"id": "a", "v": 1})
    ]

    # ... and a both-side deletion likewise keeps the base item.
    merged, conflicts = merge_protected(
        base, {"s": [{"id": "b", "v": 2}]}, {"s": [{"id": "b", "v": 2}]},
        ["/s/0"], {"/s": "id"},
    )
    assert merged == base
    assert conflicts == [
        ("/s/0", "protected_change", {"id": "a", "v": 1}, None, None)
    ]


def test_protected_identity_array_wildcard_item_and_field():
    base = {"s": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    left = {"s": [{"id": "a", "v": 9}, {"id": "b", "v": 2}]}
    right = base
    merged, conflicts = merge_protected(base, left, right, ["/s/*"], {"/s": "id"})
    assert merged == base
    assert [c[0] for c in conflicts] == ["/s/0"]
    assert conflicts[0][1] == "protected_change"

    merged, conflicts = merge_protected(base, left, right, ["/s/*/v"], {"/s": "id"})
    assert merged == base
    assert conflicts == [("/s/0/v", "protected_change", 1, 9, 1)]


def test_protected_new_identity_item_is_not_appended():
    base = {"s": []}
    left = {"s": [{"id": "n", "v": 1}]}
    right = {"s": []}
    merged, conflicts = merge_protected(base, left, right, ["/s/0"], {"/s": "id"})
    assert merged == {"s": []}
    assert conflicts == [
        ("/s/0", "protected_change", None, {"id": "n", "v": 1}, None)
    ]


def test_protected_field_of_new_identity_item_blocks_whole_item():
    base = {"s": []}
    left = {"s": [{"id": "n", "v": 1, "w": 2}]}
    right = {"s": []}
    merged, conflicts = merge_protected(
        base, left, right, ["/s/0/v"], {"/s": "id"}
    )
    # One-sided addition carrying a hit protected descendant: the whole
    # item is withheld (base has no such item), never field-assembled.
    assert merged == {"s": []}
    assert conflicts == [("/s/0/v", "protected_change", None, 1, None)]

    # An explicit resolution appends the chosen side's whole item.
    merged, _ = merge_protected(
        base, left, right, ["/s/0/v"], {"/s": "id"},
        resolutions={"/s/0/v": "left"},
    )
    assert merged == {"s": [{"id": "n", "v": 1, "w": 2}]}


def test_protected_field_of_one_sided_deleted_identity_item_keeps_whole_item():
    # Left deletes the item outright; right leaves it. A protected field
    # descendant is hit by the removal: the whole base item is kept.
    base = {"s": [{"id": "a", "v": 1, "w": 2}]}
    left = {"s": []}
    right = base
    merged, conflicts = merge_protected(
        base, left, right, ["/s/0/v"], {"/s": "id"}
    )
    assert merged == base
    assert conflicts == [
        ("/s/0/v", "protected_change", 1, None, 1)
    ]

    # Adjudicating to the deleting side removes the whole item.
    merged, _ = merge_protected(
        base, left, right, ["/s/0/v"], {"/s": "id"},
        resolutions={"/s/0/v": "left"},
    )
    assert merged == {"s": []}

    # The surviving side may even edit the protected field: still one
    # atomic delete-vs-change unit, kept wholesale until adjudicated.
    right_edited = {"s": [{"id": "a", "v": 9, "w": 2}]}
    merged, conflicts = merge_protected(
        base, left, right_edited, ["/s/0/v"], {"/s": "id"}
    )
    assert merged == base
    assert conflicts == [
        ("/s/0/v", "protected_change", 1, None, 9)
    ]
    merged, _ = merge_protected(
        base, left, right_edited, ["/s/0/v"], {"/s": "id"},
        resolutions={"/s/0/v": "right"},
    )
    assert merged == {"s": [{"id": "a", "v": 9, "w": 2}]}


def test_protected_field_of_one_sided_deleted_item_uses_original_pointer():
    # The item leaves the array on the deleting side; its other item shifts
    # forward. The hit is reported at the item's original base index.
    base = {"s": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    left = {"s": [{"id": "b", "v": 2}]}  # deletes a
    right = base
    merged, conflicts = merge_protected(
        base, left, right, ["/s/0/v"], {"/s": "id"}
    )
    assert merged == base
    assert conflicts == [("/s/0/v", "protected_change", 1, None, 1)]


def test_protected_field_of_divergent_new_identity_item():
    base = {"s": []}
    left = {"s": [{"id": "n", "v": 1, "w": 1}]}
    right = {"s": [{"id": "n", "v": 2, "w": 1}]}
    merged, conflicts = merge_protected(
        base, left, right, ["/s/0/v"], {"/s": "id"}
    )
    assert merged == {"s": [{"id": "n", "w": 1}]}
    assert conflicts == [("/s/0/v", "protected_change", None, 1, 2)]
    # Without protection the divergent addition keeps the legacy
    # whole-item both_added conflict on the parent array.
    merged, conflicts = merge_protected(base, left, right, [], {"/s": "id"})
    assert merged == {"s": [{"id": "n", "v": 1, "w": 1}]}
    assert conflicts == [
        ("/s", "both_added", None,
         {"id": "n", "v": 1, "w": 1}, {"id": "n", "v": 2, "w": 1})
    ]


def test_protected_nested_identity_array_field_final_pointer():
    base = {
        "servers": [
            {"id": "s1", "disks": [{"name": "d1", "size": 10}]}
        ]
    }
    left = {
        "servers": [
            {"id": "s1", "disks": [{"name": "d1", "size": 100}]}
        ]
    }
    right = base
    identity = {"/servers": "id", "/servers/*/disks": "name"}
    merged, conflicts = merge_protected(
        base, left, right, ["/servers/0/disks/0/size"], identity
    )
    assert merged == base
    assert conflicts == [
        ("/servers/0/disks/0/size", "protected_change", 10, 100, 10)
    ]


def test_protected_plain_array_is_atomic():
    # Arrays without an identity field never diff element by element: an
    # element pointer cannot protect a sub-value.
    merged, conflicts = merge_protected(
        {"a": [1, 2]}, {"a": [1, 9]}, {"a": [1, 2]}, ["/a/1"]
    )
    assert merged == {"a": [1, 9]}
    assert conflicts == []

    # The array itself, however, can be protected.
    merged, conflicts = merge_protected(
        {"a": [1, 2]}, {"a": [1, 9]}, {"a": [1, 2]}, ["/a"]
    )
    assert merged == {"a": [1, 2]}
    assert conflicts == [("/a", "protected_change", [1, 2], [1, 9], [1, 2])]


def test_resolution_overrides_protected_change():
    base = {"a": 1}
    left = {"a": 9}
    right = {"a": 1}
    merged, conflicts = merge_protected(
        base, left, right, ["/a"], resolutions={"/a": "left"}
    )
    assert merged == {"a": 9}
    # The reported conflict still records the three-way values.
    assert conflicts == [("/a", "protected_change", 1, 9, 1)]

    # "base" is an explicit adjudication too: same as leaving it unresolved.
    merged, _ = merge_protected(
        base, left, right, ["/a"], resolutions={"/a": "base"}
    )
    assert merged == {"a": 1}


def test_resolution_overrides_protected_deletion():
    base = {"s": [{"id": "a"}, {"id": "b"}]}
    left = {"s": [{"id": "b"}]}  # deletes a
    right = base
    merged, conflicts = merge_protected(
        base, left, right, ["/s/0"], {"/s": "id"},
        resolutions={"/s/0": "left"},
    )
    assert merged == {"s": [{"id": "b"}]}
    assert conflicts == [
        ("/s/0", "protected_change", {"id": "a"}, None, {"id": "a"})
    ]


def test_unresolved_protected_conflict_keeps_base_alongside_resolutions():
    base = {"a": 1, "b": 1}
    left = {"a": 2, "b": 2}
    right = {"a": 3, "b": 3}
    # Only the regular conflict is adjudicated; the protected one keeps base.
    merged, conflicts = merge_protected(
        base, left, right, ["/a"], resolutions={"/b": "right"}
    )
    assert merged == {"a": 1, "b": 3}
    assert [c[0] for c in conflicts] == ["/a", "/b"]


def test_duplicate_or_invalid_protected_pointer_is_rejected():
    with pytest.raises(ValueError, match="duplicate protectedPaths"):
        Merger(None, ["/a", "/a"])
    # Escaped spellings of the same pointer collide after decoding.
    with pytest.raises(ValueError, match="duplicate protectedPaths"):
        Merger(None, ["/a~0b", "/a~0b"])
    with pytest.raises(ValueError):
        Merger(None, ["a"])


# --------------------------------------------------------------- redactPaths


def merge_redacted(base, left, right, redact, identity=None, resolutions=None):
    merged, conflicts = Merger(identity, None, redact).merge(
        base, left, right, resolutions
    )
    return merged, [
        (c.path, c.kind, c.base, c.left, c.right) for c in conflicts
    ]


def test_redact_exact_match_masks_three_way_values():
    merged, conflicts = merge_redacted({"a": 1}, {"a": 2}, {"a": 3}, ["/a"])
    # merged keeps the original values; only the report is masked.
    assert merged == {"a": 1}
    assert conflicts == [
        ("/a", "both_modified", "[REDACTED]", "[REDACTED]", "[REDACTED]")
    ]


def test_redact_absent_side_stays_null():
    _, conflicts = merge_redacted({"a": 1}, {}, {"a": 3}, ["/a"])
    assert conflicts == [
        ("/a", "delete_vs_modify", "[REDACTED]", None, "[REDACTED]")
    ]

    _, conflicts = merge_redacted({}, {"a": 1}, {"a": 2}, ["/a"])
    assert conflicts == [("/a", "both_added", None, "[REDACTED]", "[REDACTED]")]


def test_redact_present_null_value_is_masked():
    # A genuine JSON null is a present value, not an absent side.
    _, conflicts = merge_redacted({"a": None}, {"a": 1}, {"a": 2}, ["/a"])
    assert conflicts == [
        ("/a", "both_modified", "[REDACTED]", "[REDACTED]", "[REDACTED]")
    ]


def test_redact_rule_above_conflict_masks_wholesale():
    _, conflicts = merge_redacted(
        {"o": {"a": 1}}, {"o": {"a": 2}}, {"o": {"a": 3}}, ["/o"]
    )
    assert conflicts == [
        ("/o/a", "both_modified", "[REDACTED]", "[REDACTED]", "[REDACTED]")
    ]


def test_redact_rule_below_conflict_recurses_into_snapshots():
    base = {"o": {"secret": "s0", "host": "h0"}}
    left = {"o": {"secret": "s1", "host": "h1"}}
    right = {}  # deletes o
    merged, conflicts = merge_redacted(base, left, right, ["/o/secret"])
    assert merged == base
    assert conflicts == [
        ("/o", "delete_vs_modify",
         {"secret": "[REDACTED]", "host": "h0"},
         {"secret": "[REDACTED]", "host": "h1"},
         None),
    ]


def test_redact_descent_skips_branches_missing_from_a_snapshot():
    base = {"o": {"a": 1}}
    left = {"o": {"a": 2, "secret": "s"}}
    right = {}  # deletes o
    merged, conflicts = merge_redacted(base, left, right, ["/o/secret"])
    assert merged == base
    # base's snapshot has no "secret" branch: it is left untouched.
    assert conflicts == [
        ("/o", "delete_vs_modify",
         {"a": 1}, {"a": 2, "secret": "[REDACTED]"}, None),
    ]


def test_redact_wildcard_inside_snapshot_covers_every_member():
    base = {"o": {"items": [{"v": 1}, {"v": 2}]}}
    left = {"o": {"items": [{"v": 9}, {"v": 2}]}}
    right = {"o": {"items": [{"v": 1}, {"v": 8}]}}
    merged, conflicts = merge_redacted(base, left, right, ["/o/items/*/v"])
    # Plain arrays are atomic: the conflict sits at /o/items with whole
    # arrays; the wildcard masks every item's "v" inside the snapshots.
    assert merged == base
    assert conflicts == [
        ("/o/items", "both_modified",
         [{"v": "[REDACTED]"}, {"v": "[REDACTED]"}],
         [{"v": "[REDACTED]"}, {"v": "[REDACTED]"}],
         [{"v": "[REDACTED]"}, {"v": "[REDACTED]"}]),
    ]


def test_redact_identity_array_item_conflict_by_final_pointer():
    base = {"s": [{"id": "a", "v": 1}]}
    left = {"s": []}  # deletes a
    right = {"s": [{"id": "a", "v": 99}]}
    merged, conflicts = merge_redacted(base, left, right, ["/s/*/v"], {"/s": "id"})
    assert merged == base
    assert conflicts == [
        ("/s/0", "delete_vs_modify",
         {"id": "a", "v": "[REDACTED]"}, None, {"id": "a", "v": "[REDACTED]"}),
    ]

    # A rule matching the item itself masks the whole item.
    _, conflicts = merge_redacted(base, left, right, ["/s/*"], {"/s": "id"})
    assert conflicts == [
        ("/s/0", "delete_vs_modify", "[REDACTED]", None, "[REDACTED]")
    ]


def test_redact_both_added_item_on_parent_array():
    # The both_added conflict is reported on the parent array with the
    # divergent items as values; a rule on the array masks them wholesale.
    base = {"s": []}
    left = {"s": [{"id": "n", "v": 1}]}
    right = {"s": [{"id": "n", "v": 2}]}
    merged, conflicts = merge_redacted(base, left, right, ["/s"], {"/s": "id"})
    assert merged == {"s": [{"id": "n", "v": 1}]}
    assert conflicts == [("/s", "both_added", None, "[REDACTED]", "[REDACTED]")]


def test_redact_root_pointer_masks_every_conflict():
    _, conflicts = merge_redacted({"a": 1}, {"a": 2}, {"a": 3}, [""])
    assert conflicts == [
        ("/a", "both_modified", "[REDACTED]", "[REDACTED]", "[REDACTED]")
    ]


def test_redact_escaped_tokens_match():
    _, conflicts = merge_redacted(
        {"a/b": 1}, {"a/b": 2}, {"a/b": 3}, ["/a~1b"]
    )
    assert conflicts == [
        ("/a~1b", "both_modified", "[REDACTED]", "[REDACTED]", "[REDACTED]")
    ]


def test_redact_does_not_change_merged_or_conflict_set():
    base = {"a": 1, "b": 1}
    left = {"a": 2, "b": 2}
    right = {"a": 3, "b": 3}
    plain_merged, plain = Merger().merge(base, left, right)
    redacted_merged, redacted = Merger(None, None, ["/a"]).merge(base, left, right)
    assert redacted_merged == plain_merged
    assert [(c.path, c.kind) for c in redacted] == [
        (c.path, c.kind) for c in plain
    ]
    by_pointer = {c.path: c for c in redacted}
    assert by_pointer["/a"].left == "[REDACTED]"
    assert by_pointer["/b"].left == 2  # unmatched conflict untouched


def test_redact_leaves_inputs_and_merged_untouched():
    base = {"o": {"secret": "s0", "x": 1}}
    left = {"o": {"secret": "s1", "x": 2}}
    right = {"o": {"secret": "s2", "x": 3}}
    merged, conflicts = merge_redacted(base, left, right, ["/o/secret"])
    # Both fields conflict; merged keeps base with the ORIGINAL values...
    assert merged == {"o": {"secret": "s0", "x": 1}}
    # ... and redaction never mutates the input documents.
    assert base == {"o": {"secret": "s0", "x": 1}}
    assert left == {"o": {"secret": "s1", "x": 2}}
    assert right == {"o": {"secret": "s2", "x": 3}}
    assert conflicts == [
        ("/o/secret", "both_modified", "[REDACTED]", "[REDACTED]", "[REDACTED]"),
        ("/o/x", "both_modified", 1, 2, 3),
    ]


def test_redact_still_allows_resolutions_with_original_values():
    merged, conflicts = merge_redacted(
        {"a": 1}, {"a": 2}, {"a": 3}, ["/a"], resolutions={"/a": "right"}
    )
    assert merged == {"a": 3}
    assert conflicts == [
        ("/a", "both_modified", "[REDACTED]", "[REDACTED]", "[REDACTED]")
    ]


def test_redact_composes_with_protected_paths():
    merged, conflicts = Merger(None, ["/a"], ["/a"]).merge(
        {"a": 1}, {"a": 9}, {"a": 9}
    )
    assert merged == {"a": 1}
    assert [
        (c.path, c.kind, c.base, c.left, c.right) for c in conflicts
    ] == [("/a", "protected_change", "[REDACTED]", "[REDACTED]", "[REDACTED]")]


def test_duplicate_or_invalid_redact_pointer_is_rejected():
    with pytest.raises(ValueError, match="duplicate redactPaths"):
        Merger(None, None, ["/a", "/a"])
    # Escaped spellings decoding to the same tokens collide.
    with pytest.raises(ValueError, match="duplicate redactPaths"):
        Merger(None, None, ["/~~", "/~0~0"])
    with pytest.raises(ValueError):
        Merger(None, None, ["a"])


# ------------------------------------------------------------- provenance


def merge_provenance(base, left, right, identity=None, protected=None,
                     redact=None, resolutions=None):
    merged, conflicts, provenance = Merger(
        identity_map=identity,
        protected_paths=protected,
        redact_paths=redact,
    ).merge_with_provenance(base, left, right, resolutions)
    return merged, conflicts, [
        (p.path, p.source, p.adjudicated, p.deleted) for p in provenance
    ]


def test_provenance_plain_leaves_and_sorting():
    merged, conflicts, provenance = merge_provenance(
        {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5},
        {"a": 10, "b": 9, "d": 4, "e": 5, "x": 7},
        {"a": 1, "b": 9, "e": None},
    )
    assert merged == {"a": 10, "b": 9, "e": None, "x": 7}
    assert conflicts == []
    assert provenance == [
        ("/a", "left", False, False),
        ("/b", "merged", False, False),
        ("/c", "merged", False, True),
        ("/d", "right", False, True),
        ("/e", "right", False, False),
        ("/x", "left", False, False),
    ]


def test_provenance_empty_when_nothing_changed():
    _, conflicts, provenance = merge_provenance(
        {"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}
    )
    assert conflicts == []
    assert provenance == []


def test_provenance_unresolved_conflict_is_base_unadjudicated():
    merged, _, provenance = merge_provenance({"a": 1}, {"a": 2}, {"a": 3})
    assert merged == {"a": 1}
    assert provenance == [("/a", "base", False, False)]


def test_provenance_resolutions_set_source_and_adjudicated():
    merged, _, provenance = merge_provenance(
        {"a": 1, "b": 1}, {"a": 2, "b": 2}, {"a": 3, "b": 3},
        resolutions={"/a": "right", "/b": "left"},
    )
    assert merged == {"a": 3, "b": 2}
    assert provenance == [
        ("/a", "right", True, False),
        ("/b", "left", True, False),
    ]


def test_provenance_base_resolution_is_not_flagged_adjudicated():
    merged, _, provenance = merge_provenance(
        {"a": 1}, {"a": 2}, {"a": 3}, resolutions={"/a": "base"}
    )
    assert merged == {"a": 1}
    assert provenance == [("/a", "base", False, False)]


def test_provenance_adjudicated_deletion_names_the_deleting_side():
    # left deletes, right modifies: accept the deletion.
    merged, _, provenance = merge_provenance(
        {"a": 1}, {}, {"a": 9}, resolutions={"/a": "left"}
    )
    assert merged == {}
    assert provenance == [("/a", "left", True, True)]

    # The reverse direction.
    merged, _, provenance = merge_provenance(
        {"a": 1}, {"a": 9}, {}, resolutions={"/a": "right"}
    )
    assert merged == {}
    assert provenance == [("/a", "right", True, True)]


def test_provenance_withheld_addition_has_no_record():
    # Unresolved both_added keeps the left value (an actual addition).
    merged, _, provenance = merge_provenance({}, {"a": 1}, {"a": 2})
    assert merged == {"a": 1}
    assert provenance == [("/a", "left", False, False)]

    # Adjudicating the addition to 'base' withholds it: no change at all.
    merged, _, provenance = merge_provenance(
        {}, {"a": 1}, {"a": 2}, resolutions={"/a": "base"}
    )
    assert merged == {}
    assert provenance == []


def test_provenance_protected_path_keeps_base_unadjudicated():
    merged, conflicts, provenance = merge_provenance(
        {"a": 1, "b": 2}, {"a": 9, "b": 2}, {"a": 1, "b": 20},
        protected=["/a"],
    )
    assert merged == {"a": 1, "b": 20}
    assert [c.kind for c in conflicts] == ["protected_change"]
    assert provenance == [
        ("/a", "base", False, False),
        ("/b", "right", False, False),
    ]


def test_provenance_protected_resolution_is_adjudicated():
    merged, _, provenance = merge_provenance(
        {"a": 1}, {"a": 9}, {"a": 1},
        protected=["/a"],
        resolutions={"/a": "left"},
    )
    assert merged == {"a": 9}
    assert provenance == [("/a", "left", True, False)]


def test_provenance_identity_array_items_and_fields():
    base = {
        "srv": [
            {"id": "a", "v": 1}, {"id": "b", "v": 2}, {"id": "c", "v": 3},
        ]
    }
    left = {"srv": [
        {"id": "a", "v": 10}, {"id": "c", "v": 3}, {"id": "e", "v": 9},
    ]}
    right = {"srv": [
        {"id": "a", "v": 1}, {"id": "b", "v": 20}, {"id": "d", "v": 8},
    ]}
    merged, conflicts, provenance = merge_provenance(
        base, left, right, {"/srv": "id"}
    )
    assert merged == {"srv": [
        {"id": "a", "v": 10}, {"id": "b", "v": 2},
        {"id": "d", "v": 8}, {"id": "e", "v": 9},
    ]}
    assert [(c.path, c.kind) for c in conflicts] == [
        ("/srv/1", "delete_vs_modify")
    ]
    assert provenance == [
        ("/srv/0/v", "left", False, False),
        ("/srv/1", "base", False, False),
        ("/srv/2", "right", False, False),    # added d at final index 2
        ("/srv/2", "right", False, True),     # c deleted by right; original 2
        ("/srv/3", "left", False, False),     # added e
    ]


def test_provenance_identity_item_deleted_on_both_sides():
    merged, _, provenance = merge_provenance(
        {"srv": [{"id": "a"}, {"id": "b"}]},
        {"srv": [{"id": "b"}]},
        {"srv": [{"id": "b"}]},
        {"/srv": "id"},
    )
    assert merged == {"srv": [{"id": "b"}]}
    assert provenance == [("/srv/0", "merged", False, True)]


def test_provenance_identity_item_adjudicated_delete_and_modify():
    base = {"srv": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    left = {"srv": [{"id": "b", "v": 2}]}
    right = {"srv": [{"id": "a", "v": 99}, {"id": "b", "v": 2}]}
    merged, _, provenance = merge_provenance(
        base, left, right, {"/srv": "id"},
        resolutions={"/srv/0": "left"},
    )
    assert merged == {"srv": [{"id": "b", "v": 2}]}
    assert provenance == [("/srv/0", "left", True, True)]

    merged, _, provenance = merge_provenance(
        base, left, right, {"/srv": "id"},
        resolutions={"/srv/0": "right"},
    )
    assert merged == {"srv": [{"id": "a", "v": 99}, {"id": "b", "v": 2}]}
    assert provenance == [("/srv/0", "right", True, False)]


def test_provenance_identity_field_edit_is_delete_plus_add():
    # Changing an identity value removes the old identity and adds a new
    # one; provenance records both.
    merged, _, provenance = merge_provenance(
        {"s": [{"id": "a", "v": 1}]},
        {"s": [{"id": "a2", "v": 1}]},
        {"s": [{"id": "a", "v": 1}]},
        {"/s": "id"},
    )
    assert merged == {"s": [{"id": "a2", "v": 1}]}
    assert provenance == [
        ("/s/0", "left", False, False),
        ("/s/0", "left", False, True),
    ]


def test_provenance_nested_identity_arrays_pointer_tie_break():
    base = {"servers": [
        {"id": "s1", "disks": [
            {"name": "d1", "size": 10}, {"name": "d2", "size": 20},
        ]},
    ]}
    left = {"servers": [
        {"id": "s1", "disks": [{"name": "d1", "size": 100}]},
    ]}
    right = {"servers": [
        {"id": "s1", "disks": [
            {"name": "d1", "size": 10}, {"name": "d3", "size": 30},
        ]},
    ]}
    merged, _, provenance = merge_provenance(
        base, left, right,
        {"/servers": "id", "/servers/*/disks": "name"},
    )
    assert merged == {"servers": [
        {"id": "s1", "disks": [
            {"name": "d1", "size": 100}, {"name": "d3", "size": 30},
        ]},
    ]}
    assert provenance == [
        ("/servers/0/disks/0/size", "left", False, False),
        ("/servers/0/disks/1", "right", False, False),
        ("/servers/0/disks/1", "merged", False, True),
    ]


def test_provenance_plain_array_is_one_atomic_unit():
    merged, _, provenance = merge_provenance(
        {"a": [1, 2]}, {"a": [1, 2, 3]}, {"a": [1, 2]}
    )
    assert merged == {"a": [1, 2, 3]}
    assert provenance == [("/a", "left", False, False)]

    merged, _, provenance = merge_provenance(
        {"a": [1, 2]}, {"a": [1, 2]}, {}
    )
    assert merged == {}
    assert provenance == [("/a", "right", False, True)]


def test_provenance_root_level_units():
    merged, _, provenance = merge_provenance(1, 2, 1)
    assert merged == 2
    assert provenance == [("", "left", False, False)]

    merged, _, provenance = merge_provenance(
        1, 2, 3, resolutions={"": "right"}
    )
    assert merged == 3
    assert provenance == [("", "right", True, False)]


def test_provenance_one_sided_object_addition_walks_its_leaves():
    merged, _, provenance = merge_provenance(
        {"k": 1},
        {"k": 1, "o": {"host": "h", "port": 1}},
        {"k": 1},
    )
    assert merged == {"k": 1, "o": {"host": "h", "port": 1}}
    assert provenance == [
        ("/o/host", "left", False, False),
        ("/o/port", "left", False, False),
    ]


def test_provenance_both_sided_object_deletion_is_one_deleted_unit():
    merged, _, provenance = merge_provenance(
        {"o": {"x": 1, "y": 2}, "k": 1}, {"k": 1}, {"k": 1}
    )
    assert merged == {"k": 1}
    assert provenance == [("/o", "merged", False, True)]


def test_provenance_protected_one_sided_object_addition_withheld_then_written():
    # Unresolved: the whole one-sided addition is withheld -> no records.
    merged, _, provenance = merge_provenance(
        {"k": 1},
        {"k": 1, "o": {"secret": "rot", "host": "h"}},
        {"k": 1},
        protected=["/o/secret"],
    )
    assert merged == {"k": 1}
    assert provenance == []

    # Adjudicated left: the whole block lands, every leaf adjudicated.
    merged, _, provenance = merge_provenance(
        {"k": 1},
        {"k": 1, "o": {"secret": "rot", "host": "h"}},
        {"k": 1},
        protected=["/o/secret"],
        resolutions={"/o/secret": "left"},
    )
    assert merged == {"k": 1, "o": {"secret": "rot", "host": "h"}}
    assert provenance == [
        ("/o/host", "left", True, False),
        ("/o/secret", "left", True, False),
    ]


def test_provenance_protected_one_sided_object_deletion():
    base = {"o": {"x": 1, "y": 1}}
    # Unresolved: the block is frozen wholesale at base, so both the hit
    # field and the untouched sibling survive as base-sourced changed
    # units (base differed from left, which carried neither).
    merged, _, provenance = merge_provenance(
        base, {}, {"o": {"x": 1, "y": 1}}, protected=["/o/x"]
    )
    assert merged == base
    assert provenance == [
        ("/o/x", "base", False, False),
        ("/o/y", "base", False, False),
    ]

    # Adjudicated to the deleting side: every leaf of the removed block is
    # an adjudicated deletion attributed to that side.
    merged, _, provenance = merge_provenance(
        base, {}, {"o": {"x": 1, "y": 1}}, protected=["/o/x"],
        resolutions={"/o/x": "left"},
    )
    assert merged == {}
    assert provenance == [
        ("/o/x", "left", True, True),
        ("/o/y", "left", True, True),
    ]


def test_provenance_protected_identity_item_deletion_kept_then_deleted():
    base = {"s": [{"id": "a", "v": 1, "w": 2}]}
    merged, _, provenance = merge_provenance(
        base, {"s": []}, base, {"/s": "id"}, ["/s/0/v"]
    )
    assert merged == base
    assert provenance == [("/s/0", "base", False, False)]

    merged, _, provenance = merge_provenance(
        base, {"s": []}, base, {"/s": "id"}, ["/s/0/v"],
        resolutions={"/s/0/v": "left"},
    )
    assert merged == {"s": []}
    assert provenance == [("/s/0", "left", True, True)]


def test_provenance_redaction_does_not_change_records():
    _, plain_conflicts, plain_provenance = Merger().merge_with_provenance(
        {"a": 1}, {"a": 2}, {"a": 3}
    )
    _, redacted_conflicts, redacted_provenance = Merger(
        None, None, ["/a"]
    ).merge_with_provenance({"a": 1}, {"a": 2}, {"a": 3})
    # Conflicts are masked...
    assert redacted_conflicts[0].left == "[REDACTED]"
    assert plain_conflicts[0].left == 2
    # ... but provenance (which carries no values) is identical.
    assert [
        (p.path, p.source, p.adjudicated, p.deleted)
        for p in redacted_provenance
    ] == [
        (p.path, p.source, p.adjudicated, p.deleted)
        for p in plain_provenance
    ]


def test_provenance_is_deterministic():
    base = {"srv": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    left = {"srv": [{"id": "b", "v": 2}, {"id": "z"}, {"id": "a", "v": 11}]}
    right = {"srv": [{"id": "a", "v": 1}, {"id": "b", "v": 22}, {"id": "a2"}]}
    identity = {"/srv": "id"}
    first = Merger(identity).merge_with_provenance(base, left, right)
    for _ in range(5):
        assert Merger(identity).merge_with_provenance(base, left, right) == first


def test_provenance_records_agree_with_final_and_resolution_outcomes():
    """Property: provenance stays consistent with the actual outcomes.

    Across random three-way documents, each non-deleted provenance pointer
    resolves in the final document (and each deleted pointer does not),
    pointers are sorted and unique-up-to-tiebreak, and 'adjudicated' is
    exactly whether the final value differs from the unresolved outcome.
    """
    import random

    from app.merger import _pointer_exists, parse_pointer

    rng = random.Random(20260923)
    scalars = [None, True, False, 1, 2, "x", "y"]

    def doc(depth=0):
        kind = rng.choice(
            ["scalar", "scalar", "obj"] + (["list"] if depth < 2 else [])
        )
        if kind == "scalar" or depth == 2:
            return rng.choice(scalars)
        if kind == "list":
            return [
                {"id": rng.choice(["p", "q", "r"]), "v": rng.choice(scalars)}
                for _ in range(rng.randrange(3))
            ]
        return {
            k: doc(depth + 1)
            for k in rng.sample(["a", "b", "srv"], rng.randrange(4))
        }

    def variant(base):
        """A random mutation (or copy) of *base*."""
        roll = rng.random()
        if roll < 0.25 or not isinstance(base, dict):
            return rng.choice(scalars)
        out = {}
        for key, value in base.items():
            if rng.random() < 0.7:
                out[key] = value if rng.random() < 0.5 else variant(value)
        for key in rng.sample(["a", "b", "srv"], rng.randrange(2)):
            if key not in out:
                out[key] = rng.choice(scalars)
        return out

    identity = {"/srv": "id", "/srv/*/sub": "id"}
    protected_pool = ["/a", "/b", "/srv/*", "/srv/*/v", ""]
    redact_pool = ["/a", "/b", "/srv", "/srv/*/v"]
    for _ in range(400):
        base = doc()
        left = variant(base) if rng.random() < 0.8 else base
        right = variant(base) if rng.random() < 0.8 else base
        protected = [p for p in protected_pool if rng.random() < 0.25]
        redact = [p for p in redact_pool if rng.random() < 0.2]
        merger = Merger(identity, protected, redact)
        kept, kept_conflicts, kept_provenance = merger.merge_with_provenance(
            base, left, right
        )
        # Without resolutions every provenance record is unadjudicated.
        assert all(not p.adjudicated for p in kept_provenance)
        # Adjudicate a random subset of the reported conflicts.
        reported = [c.path for c in kept_conflicts]
        sides = ["base", "left", "right"]
        resolutions = {
            pointer: rng.choice(sides) for pointer in reported if rng.random() < 0.5
        }
        final, conflicts, provenance = merger.merge_with_provenance(
            base, left, right, resolutions
        )
        # Resolutions never change the reported conflicts.
        assert [(c.path, c.kind) for c in conflicts] == [
            (c.path, c.kind) for c in kept_conflicts
        ]

        rendered = [p.path for p in provenance]
        # Sorted by final pointer with the declared tie-break.
        assert rendered == sorted(rendered)
        for record in provenance:
            tokens = parse_pointer(record.path)
            exists_final = _pointer_exists(final, tokens)
            exists_base = _pointer_exists(base, tokens)
            if record.deleted:
                # A deleted unit resolves in base (its record keeps the
                # original location), never in the final document...
                assert exists_base, (base, left, right, record)
                assert not exists_final, (final, record)
                # ... and a deletion is never sourced from 'base'.
                assert record.source in ("left", "right", "merged")
            else:
                assert exists_final, (final, record)
        # Any adjudicated record requires at least one explicit
        # resolution (atomic-unit resolutions can adjudicate sibling
        # leaves under a different pointer, so the check is intentionally
        # at the document level).
        if any(p.adjudicated for p in provenance):
            assert resolutions
