import json
import stat
from pathlib import Path

import pytest

from docs.administrator.configurations.gaussdb_test_plan_execute import fresh_run_context


def test_resolve_run_id_accepts_expected_shape():
    assert fresh_run_context.resolve_run_id("20260716_fixverify_001") == "20260716_fixverify_001"


@pytest.mark.parametrize(
    "value",
    ["", "../escape", "20260716-FIX-001", "20260716_fixverify", "20260716_fixverify_0001"],
)
def test_resolve_run_id_rejects_unsafe_values(value):
    with pytest.raises(ValueError, match="run id"):
        fresh_run_context.resolve_run_id(value)


def test_explicit_run_uses_new_runtime_and_evidence_roots(tmp_path: Path):
    paths = fresh_run_context.build_run_paths(
        "20260716_fixverify_001",
        execute_dir=tmp_path,
        explicit=True,
    )

    assert paths.runtime == tmp_path / "runtime" / "20260716_fixverify_001"
    assert paths.run_root == tmp_path / "runs" / "20260716_fixverify_001"
    assert paths.evidence == paths.run_root / "evidence_private"
    assert "bak" not in str(paths.evidence).lower()


def test_initialize_fresh_run_refuses_existing_run_or_runtime(tmp_path: Path):
    paths = fresh_run_context.build_run_paths(
        "20260716_fixverify_001",
        execute_dir=tmp_path,
        explicit=True,
    )
    paths.runtime.mkdir(parents=True)

    with pytest.raises(FileExistsError):
        fresh_run_context.initialize_fresh_run(paths, confirm="20260716_fixverify_001")


def test_initialize_fresh_run_writes_private_minimal_marker(tmp_path: Path):
    paths = fresh_run_context.build_run_paths(
        "20260716_fixverify_001",
        execute_dir=tmp_path,
        explicit=True,
    )

    payload = fresh_run_context.initialize_fresh_run(paths, confirm=paths.run_id)

    marker_payload = json.loads(paths.marker.read_text(encoding="utf-8"))
    assert marker_payload == payload
    assert set(marker_payload) == {"run_id", "started_at"}
    assert marker_payload["run_id"] == paths.run_id
    assert marker_payload["started_at"].endswith("+00:00")
    assert stat.S_IMODE(paths.run_root.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.run_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.marker.stat().st_mode) == 0o600
    assert not paths.runtime.exists()
