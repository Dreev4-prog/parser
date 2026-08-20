from __future__ import annotations

MAX_SELECTED_CATEGORIES = 2


def toggle_selection(
    selected: set[str],
    key: str,
    *,
    is_group: bool,
    root_key: str,
    child_keys: set[str],
) -> tuple[set[str], bool]:
    """Return a bounded category selection and whether the add was refused.

    Removing a selected key is always allowed. A group root replaces its child
    selections; an individual child replaces the root, so those operations do
    not consume an extra slot unnecessarily.
    """
    updated = set(selected)
    if key in updated:
        updated.remove(key)
        return updated, False

    if is_group:
        updated.difference_update(child_keys)
    else:
        updated.discard(root_key)
    updated.add(key)

    if len(updated) > MAX_SELECTED_CATEGORIES:
        return set(selected), True
    return updated, False


def bulk_group_selection(
    selected: set[str],
    child_keys: list[str],
    *,
    root_key: str,
) -> tuple[set[str], bool]:
    """Clear selected children or fill only the remaining slots up to two."""
    selected_children = [key for key in child_keys if key in selected]
    if selected_children:
        return set(selected) - set(selected_children), False

    updated = set(selected)
    updated.discard(root_key)
    free_slots = max(0, MAX_SELECTED_CATEGORIES - len(updated))
    missing = [key for key in child_keys if key not in updated]
    to_add = missing[:free_slots]
    updated.update(to_add)
    return updated, len(missing) > len(to_add)


def validate_scan_category_keys(category_keys: list[str], valid_keys: set[str]) -> list[str]:
    """Deduplicate/validate an actual job payload and enforce the hard limit."""
    keys = [key for key in category_keys if key in valid_keys]
    keys = list(dict.fromkeys(keys))
    if not keys:
        raise ValueError("Нужно выбрать хотя бы одну категорию")
    if len(keys) > MAX_SELECTED_CATEGORIES:
        raise ValueError(f"Можно выбрать максимум {MAX_SELECTED_CATEGORIES} категорий за один запуск")
    return keys
