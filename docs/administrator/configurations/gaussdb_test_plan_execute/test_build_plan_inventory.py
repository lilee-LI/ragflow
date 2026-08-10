import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("build_plan_inventory.py")


def load_module():
    spec = importlib.util.spec_from_file_location("build_plan_inventory", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_collects_only_tc_level_three_headings_and_keeps_zero_case_files(tmp_path):
    (tmp_path / "01_alpha.md").write_text(
        "# Alpha\n### TC-A-001: first\ntext TC-A-999\n### TC-A-002: second\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Index\nNo executable case.\n", encoding="utf-8")

    inventory = load_module().collect_inventory(tmp_path)

    assert [group["file"] for group in inventory] == ["01_alpha.md", "README.md"]
    assert inventory[0]["cases"] == [
        {"id": "TC-A-001", "title": "first", "line": 2},
        {"id": "TC-A-002", "title": "second", "line": 4},
    ]
    assert inventory[1]["cases"] == []


def test_duplicate_case_ids_are_reported_with_both_source_locations(tmp_path):
    (tmp_path / "01_alpha.md").write_text("### TC-DUP-001: first\n", encoding="utf-8")
    (tmp_path / "02_beta.md").write_text("### TC-DUP-001: second\n", encoding="utf-8")

    inventory = load_module().collect_inventory(tmp_path)
    duplicates = load_module().find_duplicate_ids(inventory)

    assert duplicates == {
        "TC-DUP-001": ["01_alpha.md:1", "02_beta.md:1"],
    }


def test_write_outputs_creates_traceable_json_and_markdown(tmp_path):
    inventory = [
        {
            "file": "01_alpha.md",
            "cases": [{"id": "TC-A-001", "title": "first", "line": 2}],
        },
        {"file": "README.md", "cases": []},
    ]
    json_path = tmp_path / "plan_inventory.json"
    markdown_path = tmp_path / "PLAN_COVERAGE.md"

    load_module().write_outputs(inventory, json_path, markdown_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload[0]["cases"][0]["id"] == "TC-A-001"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "总测试文件：2" in markdown
    assert "总用例：1" in markdown
    assert "`TC-A-001`" in markdown
    assert "README.md" in markdown


def test_validate_plan_set_reports_unbalanced_code_fence(tmp_path):
    (tmp_path / "01_alpha.md").write_text(
        "# Alpha\n### TC-A-001: first\n```python\nprint('open')\n",
        encoding="utf-8",
    )

    module = load_module()
    inventory = module.collect_inventory(tmp_path)

    assert module.validate_plan_set(tmp_path, inventory) == [
        "01_alpha.md: Markdown code fence count is odd (1)",
    ]


def test_validate_plan_set_reports_unknown_tc_reference_in_review_findings(tmp_path):
    (tmp_path / "01_alpha.md").write_text(
        "# Alpha\n### TC-A-001: first\n",
        encoding="utf-8",
    )
    (tmp_path / "review_findings.md").write_text(
        "# Review\nKnown TC-A-001; stale TC-MISSING-999.\n",
        encoding="utf-8",
    )

    module = load_module()
    inventory = module.collect_inventory(tmp_path)

    assert module.validate_plan_set(tmp_path, inventory) == [
        "review_findings.md: unknown case reference TC-MISSING-999",
    ]
