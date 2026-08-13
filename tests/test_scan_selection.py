import pytest

from scan_selection import (
    MAX_SELECTED_CATEGORIES,
    bulk_group_selection,
    toggle_selection,
    validate_scan_category_keys,
)


def test_sixth_category_is_refused_without_changing_selection():
    selected = {"a", "b", "c", "d", "e"}
    updated, hit_limit = toggle_selection(
        selected, "f", is_group=False, root_key="root", child_keys=set()
    )
    assert hit_limit is True
    assert updated == selected
    assert len(updated) == MAX_SELECTED_CATEGORIES


def test_removing_category_is_allowed_at_limit():
    selected = {"a", "b", "c", "d", "e"}
    updated, hit_limit = toggle_selection(
        selected, "c", is_group=False, root_key="root", child_keys=set()
    )
    assert hit_limit is False
    assert updated == {"a", "b", "d", "e"}


def test_bulk_select_fills_only_free_slots():
    selected = {"outside1", "outside2"}
    updated, hit_limit = bulk_group_selection(
        selected, ["a", "b", "c", "d", "e", "f"], root_key="root"
    )
    assert hit_limit is True
    assert len(updated) == MAX_SELECTED_CATEGORIES
    assert {"outside1", "outside2"} <= updated


def test_job_payload_has_hard_five_category_limit():
    valid = {"a", "b", "c", "d", "e", "f"}
    assert validate_scan_category_keys(["a", "a", "b"], valid) == ["a", "b"]
    with pytest.raises(ValueError):
        validate_scan_category_keys(["a", "b", "c", "d", "e", "f"], valid)
