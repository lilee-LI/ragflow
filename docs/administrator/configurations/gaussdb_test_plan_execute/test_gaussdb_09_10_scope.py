import re
from pathlib import Path


PLAN_DIR = Path(__file__).resolve().parent.parent / "gaussdb_test_plan"
CASE_PATTERN = re.compile(r"^### (TC-(?:CONN|FR)-\d{3}):", re.MULTILINE)

EXPECTED_09 = [
    "TC-CONN-015",
    "TC-CONN-016",
    "TC-CONN-031",
    "TC-CONN-032",
    "TC-CONN-093",
    "TC-CONN-094",
]
EXPECTED_10 = [
    *[f"TC-FR-{number:03d}" for number in range(1, 13)],
    *[f"TC-FR-{number:03d}" for number in range(18, 25)],
    "TC-FR-027",
    "TC-FR-037",
    "TC-FR-038",
    "TC-FR-044",
    "TC-FR-045",
    "TC-FR-051",
    "TC-FR-052",
    "TC-FR-053",
]


def plan_ids(filename: str) -> list[str]:
    return CASE_PATTERN.findall((PLAN_DIR / filename).read_text(encoding="utf-8"))


def test_connector_plan_contains_only_adaptation_cases():
    assert plan_ids("09_connector_system.md") == EXPECTED_09
    assert not (PLAN_DIR / "09_connector_supplement.md").exists()


def test_fault_recovery_plan_contains_only_adaptation_cases():
    assert plan_ids("10_fault_recovery.md") == EXPECTED_10
