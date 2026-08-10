#!/usr/bin/env python3
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


CASE_HEADING = re.compile(r"^###\s+(TC-[A-Z0-9-]+):\s*(.*?)\s*$")
CASE_REFERENCE = re.compile(r"\bTC-[A-Z0-9]+(?:-[A-Z0-9]+)+\b")


def collect_inventory(plan_dir: Path) -> list[dict]:
    inventory = []
    for path in sorted(plan_dir.glob("*.md")):
        cases = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = CASE_HEADING.match(line)
            if match:
                cases.append(
                    {
                        "id": match.group(1),
                        "title": match.group(2),
                        "line": line_number,
                    }
                )
        inventory.append({"file": path.name, "cases": cases})
    return inventory


def find_duplicate_ids(inventory: list[dict]) -> dict[str, list[str]]:
    locations = defaultdict(list)
    for group in inventory:
        for case in group["cases"]:
            locations[case["id"]].append(f"{group['file']}:{case['line']}")
    return {case_id: source_locations for case_id, source_locations in sorted(locations.items()) if len(source_locations) > 1}


def validate_plan_set(plan_dir: Path, inventory: list[dict]) -> list[str]:
    errors = []
    for path in sorted(plan_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        fence_count = text.count("```")
        if fence_count % 2:
            errors.append(f"{path.name}: Markdown code fence count is odd ({fence_count})")

    review_path = plan_dir / "review_findings.md"
    if review_path.exists():
        known_case_ids = {case["id"] for group in inventory for case in group["cases"]}
        review_references = set(CASE_REFERENCE.findall(review_path.read_text(encoding="utf-8")))
        for case_id in sorted(review_references - known_case_ids):
            errors.append(f"review_findings.md: unknown case reference {case_id}")
    return errors


def write_outputs(inventory: list[dict], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    case_count = sum(len(group["cases"]) for group in inventory)
    duplicates = find_duplicate_ids(inventory)
    lines = [
        "# 测试计划覆盖清单",
        "",
        f"- 总测试文件：{len(inventory)}",
        f"- 总用例：{case_count}",
        f"- 双组最少判定数：{case_count * 2}",
        f"- 重复用例 ID：{len(duplicates)}",
        "",
        "## 文件汇总",
        "",
        "| 计划文件 | 用例数 | 独立报告 | 当前状态 |",
        "|---|---:|---|---|",
    ]
    for group in inventory:
        report_name = f"{Path(group['file']).stem}_report.md"
        lines.append(f"| `{group['file']}` | {len(group['cases'])} | `{report_name}` | 未测试 |")

    lines.extend(["", "## 用例明细", ""])
    for group in inventory:
        lines.extend([f"### {group['file']}", ""])
        if not group["cases"]:
            lines.extend(["该文件没有 `### TC-*` 标题；按文档约束审计组执行并单独出报告。", ""])
            continue
        for case in group["cases"]:
            lines.append(f"- [ ] `{case['id']}`（源文件第 {case['line']} 行）：{case['title']}")
        lines.append("")

    lines.extend(["## 重复 ID 审计", ""])
    if duplicates:
        for case_id, source_locations in duplicates.items():
            lines.append(f"- `{case_id}`：{', '.join(source_locations)}")
    else:
        lines.append("未发现跨文件重复用例 ID。")
    lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从 GaussDB Markdown 测试计划生成覆盖清单")
    parser.add_argument("plan_dir", type=Path)
    parser.add_argument("json_path", type=Path)
    parser.add_argument("markdown_path", type=Path)
    args = parser.parse_args()
    inventory_data = collect_inventory(args.plan_dir)
    validation_errors = validate_plan_set(args.plan_dir, inventory_data)
    if validation_errors:
        parser.error("plan validation failed:\n" + "\n".join(validation_errors))
    write_outputs(inventory_data, args.json_path, args.markdown_path)
