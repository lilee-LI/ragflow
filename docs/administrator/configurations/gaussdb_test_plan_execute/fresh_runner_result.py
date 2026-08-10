from __future__ import annotations

from collections.abc import Mapping


def pair_exit_code(result: Mapping[str, object]) -> int:
    """Return whether orchestration may continue after a persisted pair result."""

    if result.get("pair_status") == "PASS":
        return 0

    groups = result.get("groups")
    if not isinstance(groups, (list, tuple)) or len(groups) != 2:
        return 1
    statuses = {}
    for item in groups:
        if not isinstance(item, Mapping):
            return 1
        group = item.get("group")
        status = item.get("status")
        if group not in {"control", "experiment"} or group in statuses:
            return 1
        statuses[group] = status
    return 0 if statuses == {"control": "FAIL", "experiment": "FAIL"} else 1
