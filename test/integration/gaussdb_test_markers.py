import pytest


_EXCLUSIVE_ENVIRONMENT_MARKERS = {
    "gaussdb_both",
    "gaussdb_centralized",
    "gaussdb_distributed",
    "gaussdb_variant_specific",
}


def mark_gaussdb_both_by_default(namespace):
    for name, value in tuple(namespace.items()):
        if not name.startswith("test_") or not callable(value):
            continue
        marker_names = {marker.name for marker in getattr(value, "pytestmark", ())}
        if marker_names.isdisjoint(_EXCLUSIVE_ENVIRONMENT_MARKERS):
            namespace[name] = pytest.mark.gaussdb_both(value)
