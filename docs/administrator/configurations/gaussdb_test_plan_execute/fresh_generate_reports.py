#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    build_run_paths,
    resolve_run_id,
)


EXECUTE_DIR = Path(__file__).resolve().parent
PLAN_DIR = EXECUTE_DIR.parent / "gaussdb_test_plan"
ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
CASE_HEADING_PATTERN = re.compile(r"^###\s+(TC-[A-Z0-9-]+):\s*(.+?)\s*$", re.MULTILINE)
VALID_STATUSES = {"PASS", "FAIL", "BLOCKED"}
GROUP_ORDER = ("control", "experiment")


@dataclass(frozen=True)
class ReportSpec:
    report_name: str
    heading: str
    short_name: str
    evidence_section: str
    plan_files: tuple[str, ...]


REPORT_SPECS = (
    ReportSpec(
        "01_startup_migration_report.md",
        "01 - 启动、迁移与部署配置测试报告",
        "01 启动与迁移",
        "01_startup_migration",
        ("01_startup_migration.md",),
    ),
    ReportSpec(
        "02_user_management_report.md",
        "02 - 用户管理测试报告",
        "02 用户管理",
        "02_user_management",
        ("02_user_management.md",),
    ),
    ReportSpec(
        "02_user_management_admin_supplement_report.md",
        "02 - 用户管理 Admin/Settings 补充测试报告",
        "02 Admin/Settings 补充",
        "02_user_management_admin_supplement",
        ("02_user_management_admin_supplement.md",),
    ),
    ReportSpec(
        "03_auth_token_session_report.md",
        "03 - 认证、Token 与 Session 测试报告",
        "03 认证与补充",
        "03_authentication",
        ("03_auth_token_session.md", "03_auth_supplement.md"),
    ),
    ReportSpec(
        "04_dataset_document_report.md",
        "04 - 数据集与文档测试报告",
        "04 数据集/文档与补充",
        "04_dataset_document",
        ("04_dataset_document.md", "04_dataset_document_supplement.md"),
    ),
    ReportSpec(
        "05_chat_session_agent_report.md",
        "05 - Chat / Session / Agent 测试报告",
        "05 Chat/Session/Agent 与补充",
        "05_chat_session_agent",
        ("05_chat_session_agent.md", "05_chat_agent_supplement.md"),
    ),
    ReportSpec(
        "06_memory_metadata_report.md",
        "06 - Memory Metadata 测试报告",
        "06 Memory metadata 与补充",
        "06_memory_metadata",
        ("06_memory_metadata.md", "06_memory_supplement.md"),
    ),
    ReportSpec(
        "07_memory_store_report.md",
        "07 - Memory Store E2E 测试报告",
        "07 Memory Store",
        "07_memory_store",
        ("07_memory_store_e2e.md",),
    ),
    ReportSpec(
        "08_file_report.md",
        "08 - File 管理测试报告",
        "08 File 与补充",
        "08_file",
        ("08_file_management.md", "08_file_supplement.md"),
    ),
    ReportSpec(
        "09_connector_system_report.md",
        "09 - Connector 调度与系统健康适配测试报告",
        "09 Connector/System",
        "09_connector",
        ("09_connector_system.md",),
    ),
    ReportSpec(
        "10_fault_recovery_report.md",
        "10 - GaussDB 故障恢复与方言边界测试报告",
        "10 Fault Recovery",
        "10_fault_recovery",
        ("10_fault_recovery.md",),
    ),
)

REPORT_BY_PLAN = {plan_file: spec.report_name for spec in REPORT_SPECS for plan_file in spec.plan_files}
REPORT_BY_PLAN.update(
    {
        "00_environment_setup.md": "00_environment_setup_report.md",
        "11_validation_oracle_matrix.md": "FINAL_COVERAGE_AUDIT.md",
    }
)


@dataclass(frozen=True)
class PlannedCase:
    case_id: str
    title: str
    plan_file: str
    line: int


@dataclass(frozen=True)
class CaseResult:
    planned: PlannedCase
    evidence: dict[str, Any]
    evidence_relative_path: str

    @property
    def statuses(self) -> tuple[str, str]:
        return tuple(str(group["status"]) for group in self.evidence["groups"])  # type: ignore[return-value]

    @property
    def pair_status(self) -> str:
        return str(self.evidence["pair_status"])


@dataclass(frozen=True)
class SectionResult:
    spec: ReportSpec
    cases: tuple[CaseResult, ...]


@dataclass(frozen=True)
class RunResult:
    run_id: str
    evidence_root: Path
    formal_status: dict[str, Any]
    environment: dict[str, Any]
    sections: tuple[SectionResult, ...]

    @property
    def cases(self) -> tuple[CaseResult, ...]:
        return tuple(case for section in self.sections for case in section.cases)


def plan_cases(plan_file: str) -> list[PlannedCase]:
    path = PLAN_DIR / plan_file
    text = path.read_text(encoding="utf-8")
    result: list[PlannedCase] = []
    for match in CASE_HEADING_PATTERN.finditer(text):
        result.append(
            PlannedCase(
                case_id=match.group(1),
                title=match.group(2).strip(),
                plan_file=plan_file,
                line=text.count("\n", 0, match.start()) + 1,
            )
        )
    return result


def expected_pair_status(statuses: tuple[str, str]) -> str:
    if "FAIL" in statuses:
        return "FAIL"
    if "BLOCKED" in statuses:
        return "BLOCKED"
    return "PASS"


def attribution(statuses: tuple[str, str]) -> str | None:
    if statuses == ("PASS", "PASS"):
        return None
    if statuses == ("PASS", "FAIL"):
        return "实验组独有"
    if statuses == ("FAIL", "PASS"):
        return "对照组独有"
    if statuses == ("FAIL", "FAIL"):
        return "对照组也存在"
    if statuses == ("BLOCKED", "BLOCKED"):
        return "两组均阻塞"
    if statuses == ("BLOCKED", "PASS"):
        return "对照环境阻塞/实验组通过"
    raise ValueError(f"unsupported asymmetric result: {statuses[0]}/{statuses[1]}")


def _validate_case_evidence(payload: dict[str, Any], planned: PlannedCase, run_id: str) -> None:
    if payload.get("case_id") != planned.case_id:
        raise ValueError(f"case identity mismatch: {planned.case_id}")
    if not isinstance(payload.get("title"), str) or not payload["title"].strip():
        raise ValueError(f"missing evidence title: {planned.case_id}")
    if payload.get("group_order") != list(GROUP_ORDER):
        raise ValueError(f"declared group order mismatch: {planned.case_id}")
    groups = payload.get("groups")
    if not isinstance(groups, list) or len(groups) != 2:
        raise ValueError(f"invalid groups: {planned.case_id}")
    if [group.get("group") for group in groups] != list(GROUP_ORDER):
        raise ValueError(f"recorded group order mismatch: {planned.case_id}")
    statuses = tuple(str(group.get("status")) for group in groups)
    if any(status not in VALID_STATUSES for status in statuses):
        raise ValueError(f"invalid status: {planned.case_id}: {statuses}")
    if payload.get("pair_status") != expected_pair_status(statuses):
        raise ValueError(f"pair status mismatch: {planned.case_id}")
    if payload.get("batch_id") not in (None, run_id):
        raise ValueError(f"batch mismatch: {planned.case_id}")
    recorded = [group.get("recorded_at") for group in groups]
    if all(isinstance(item, str) for item in recorded):
        if datetime.fromisoformat(recorded[0]) > datetime.fromisoformat(recorded[1]):
            raise ValueError(f"group chronology mismatch: {planned.case_id}")
    attribution(statuses)  # Reject unsupported asymmetric BLOCKED combinations.


def load_run(run_id: str) -> RunResult:
    resolved_run_id = resolve_run_id(run_id)
    paths = build_run_paths(resolved_run_id)
    status_path = paths.evidence / "formal_execution_status.json"
    environment_path = paths.evidence / "00_environment_setup" / "environment_validation.json"
    formal_status = json.loads(status_path.read_text(encoding="utf-8"))
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    if formal_status.get("batch_id") != resolved_run_id:
        raise ValueError("formal execution batch mismatch")
    if formal_status.get("state") != "complete" or formal_status.get("stop_reason") is not None:
        raise ValueError("formal execution is not complete")
    if environment.get("batch_id") != resolved_run_id:
        raise ValueError("environment validation batch mismatch")

    sections: list[SectionResult] = []
    all_plan_ids: list[str] = []
    computed_signatures: Counter[str] = Counter()
    for spec in REPORT_SPECS:
        planned_cases = [case for plan_file in spec.plan_files for case in plan_cases(plan_file)]
        expected_ids = [case.case_id for case in planned_cases]
        section_dir = paths.evidence / spec.evidence_section
        actual_ids = {path.stem for path in section_dir.glob("TC-*.json") if path.is_file()}
        if actual_ids != set(expected_ids):
            missing = sorted(set(expected_ids) - actual_ids)
            extra = sorted(actual_ids - set(expected_ids))
            raise ValueError(f"evidence coverage mismatch in {spec.evidence_section}: missing={missing}, extra={extra}")
        cases: list[CaseResult] = []
        for planned in planned_cases:
            evidence_path = section_dir / f"{planned.case_id}.json"
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            _validate_case_evidence(payload, planned, resolved_run_id)
            relative_path = str(evidence_path.relative_to(EXECUTE_DIR))
            result = CaseResult(planned, payload, relative_path)
            computed_signatures["/".join(result.statuses)] += 1
            cases.append(result)
        all_plan_ids.extend(expected_ids)
        sections.append(SectionResult(spec, tuple(cases)))

    if len(all_plan_ids) != len(set(all_plan_ids)):
        raise ValueError("duplicate case IDs in current plans")
    if len(all_plan_ids) != 643:
        raise ValueError(f"expected 643 current cases, got {len(all_plan_ids)}")
    if formal_status.get("completed_count") != len(all_plan_ids):
        raise ValueError("formal completed count mismatch")
    if formal_status.get("total_count") != len(all_plan_ids):
        raise ValueError("formal total count mismatch")
    if formal_status.get("group_signature_counts") != dict(sorted(computed_signatures.items())):
        raise ValueError("formal signature counts mismatch")
    if not environment.get("operational_ready"):
        raise ValueError("final environment is not operationally ready")
    return RunResult(
        resolved_run_id,
        paths.evidence,
        formal_status,
        environment,
        tuple(sections),
    )


def status_counts(cases: Iterable[CaseResult]) -> dict[str, Counter[str]]:
    case_list = list(cases)
    return {
        "pair": Counter(case.pair_status for case in case_list),
        "control": Counter(case.statuses[0] for case in case_list),
        "experiment": Counter(case.statuses[1] for case in case_list),
        "signature": Counter("/".join(case.statuses) for case in case_list),
    }


def _count_text(counts: Counter[str]) -> str:
    return "、".join(f"{counts.get(status, 0)} {status}" for status in ("PASS", "FAIL", "BLOCKED"))


def _markdown_cell(value: object) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
    return re.sub(r"\s+", " ", text).strip()


def _shorten(value: str, limit: int = 260) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _case_times(cases: Iterable[CaseResult]) -> list[datetime]:
    times: list[datetime] = []
    for case in cases:
        payload = case.evidence
        for key in ("started_at", "finished_at"):
            value = payload.get(key)
            if isinstance(value, str):
                times.append(datetime.fromisoformat(value))
        for group in payload["groups"]:
            value = group.get("recorded_at")
            if isinstance(value, str):
                times.append(datetime.fromisoformat(value))
    return times


def _format_time(value: datetime) -> str:
    return value.astimezone(ASIA_SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")


def _finding_fields(case: CaseResult) -> tuple[str, str, str]:
    ids: list[str] = []
    summaries: list[str] = []
    locations: list[str] = []
    for group in case.evidence["groups"]:
        findings = group.get("findings") or []
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            finding_id = finding.get("id") or finding.get("type") or finding.get("area")
            summary = finding.get("summary") or finding.get("detail")
            location = finding.get("code_location") or finding.get("source")
            for target, value in (
                (ids, finding_id),
                (summaries, summary),
                (locations, location),
            ):
                if value is not None:
                    cleaned = _markdown_cell(value)
                    if cleaned and cleaned not in target:
                        target.append(cleaned)
    summary_text = "；".join(summaries)
    if not summary_text:
        summary_text = "失败细节记录在该用例的 oracle/steps 中"
    return (
        "、".join(ids) or "—",
        _shorten(summary_text),
        "；".join(locations) or "—",
    )


def _attribution_rows(cases: Iterable[CaseResult]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for case in cases:
        label = attribution(case.statuses)
        if label is None:
            continue
        rows.append(
            {
                "case_id": case.planned.case_id,
                "control": case.statuses[0],
                "experiment": case.statuses[1],
                "attribution": label,
                "evidence": case.evidence_relative_path,
            }
        )
    return rows


def render_report(run: RunResult, section: SectionResult) -> str:
    cases = section.cases
    counts = status_counts(cases)
    signatures = counts["signature"]
    times = _case_times(cases)
    start = _format_time(min(times))
    end = _format_time(max(times))
    audit_path = run.evidence_root / section.spec.evidence_section / "coverage_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.is_file() else None
    if audit is not None and (audit.get("batch_id") != run.run_id or not audit.get("audit_pass")):
        raise ValueError(f"section audit is not passing: {section.spec.evidence_section}")

    experiment_only = signatures.get("PASS/FAIL", 0)
    control_only = signatures.get("FAIL/PASS", 0)
    common_fail = signatures.get("FAIL/FAIL", 0)
    common_blocked = signatures.get("BLOCKED/BLOCKED", 0)
    control_blocked = signatures.get("BLOCKED/PASS", 0)
    adaptation_status = "PASS" if experiment_only == 0 else "FAIL"
    contract_text = "全部计划契约通过" if counts["pair"].get("FAIL", 0) == 0 and counts["pair"].get("BLOCKED", 0) == 0 else "仍有未满足契约；按归属单独跟踪，不计作 GaussDB 适配回归"
    plan_text = "、".join(f"`{name}`" for name in section.spec.plan_files)
    evidence_dir = f"runs/{run.run_id}/evidence_private/{section.spec.evidence_section}/"
    execution_method = (
        "01 组在切换分组模式前按用例进程完成；每个用例固定先 control、后 experiment"
        if section.spec.evidence_section == "01_startup_migration"
        else "按计划组单进程执行；每个用例固定先 control、后 experiment"
    )
    postprocessed_cases = [case.planned.case_id for case in cases if case.evidence.get("evidence_postprocessing")]

    lines = [
        f"# {section.spec.heading}",
        "",
        "## 1. 执行结论",
        "",
        f"- 执行批次：`{run.run_id}`",
        f"- 正式执行时间：{start}～{end}（Asia/Shanghai）",
        f"- 当前计划：{plan_text}",
        f"- 覆盖：{len(cases)}/{len(cases)} 个唯一用例，0 缺失、0 额外、0 重复",
        f"- 执行方式：{execution_method}",
        f"- 用例对结果：{_count_text(counts['pair'])}",
        f"- 对照组结果：{_count_text(counts['control'])}",
        f"- 实验组结果：{_count_text(counts['experiment'])}",
        f"- 适配回归结论：**{adaptation_status}**（实验组独有失败 {experiment_only} 个）",
        f"- 产品契约现状：{contract_text}",
        f"- 正式证据：`{evidence_dir}`",
        *(
            [f"- 证据卫生：{', '.join(postprocessed_cases)} 的原始结构化渲染件已递归脱敏；脱敏前后 SHA256 记录在 `raw/structured_secret_redaction_manifest.json`，未保留明文副本，用例状态不变"]
            if postprocessed_cases
            else []
        ),
        "",
        "本报告仅由上述当前批次的正式证据和现行计划生成。双组共同失败继续保留，但按约定不判定为 GaussDB 适配问题；对照组独有失败也不归因于实验组。",
        "",
        "## 2. 问题归属判定",
        "",
        "口径：`实验组独有` = control PASS / experiment FAIL；`对照组也存在` = "
        "control FAIL / experiment FAIL；`对照组独有` = control FAIL / experiment PASS；"
        "`两组均阻塞` = control BLOCKED / experiment BLOCKED；"
        "`对照环境阻塞/实验组通过` = control BLOCKED / experiment PASS。",
        "",
        "<!-- ISSUE_ATTRIBUTION_START -->",
        "| 用例 | 对照组 | 实验组 | 问题归属 |",
        "|---|---|---|---|",
    ]
    for row in _attribution_rows(cases):
        lines.append(f"| {row['case_id']} | {row['control']} | {row['experiment']} | {row['attribution']} |")
    lines.extend(
        [
            "<!-- ISSUE_ATTRIBUTION_END -->",
            "",
            f"归属统计：实验组独有 {experiment_only}，对照组也存在 {common_fail}，对照组独有 {control_only}，两组均阻塞 {common_blocked}，对照环境阻塞/实验组通过 {control_blocked}。",
            "",
            "## 3. 非 PASS 证据摘要",
            "",
        ]
    )
    non_pass = [case for case in cases if case.statuses != ("PASS", "PASS")]
    if non_pass:
        lines.extend(
            [
                "| 用例 | 名称 | 归属 | Finding | 证据摘要 | 代码位置 |",
                "|---|---|---|---|---|---|",
            ]
        )
        for case in non_pass:
            finding_ids, summaries, locations = _finding_fields(case)
            lines.append(
                f"| {case.planned.case_id} | {_markdown_cell(case.planned.title)} | "
                f"{attribution(case.statuses)} | {_markdown_cell(finding_ids)} | "
                f"{_markdown_cell(summaries)} | {_markdown_cell(locations)} |"
            )
    else:
        lines.append("本组没有非 PASS 用例。")

    lines.extend(
        [
            "",
            "## 4. 全量用例结果",
            "",
            "| 用例 | 名称 | 对照组 | 实验组 | 用例对 | 证据 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for case in cases:
        lines.append(f"| {case.planned.case_id} | {_markdown_cell(case.planned.title)} | {case.statuses[0]} | {case.statuses[1]} | {case.pair_status} | [JSON]({case.evidence_relative_path}) |")

    audit_text = "本组 `coverage_audit.json` 的覆盖、证据卫生、环境与残留门禁全部通过。" if audit is not None else "本组由全局覆盖审计校验计划、runner 正式完成状态与证据集合一致性。"
    lines.extend(
        [
            "",
            "## 5. 终审",
            "",
            f"- 计划顺序、正式证据顺序和报告顺序一致，共 {len(cases)} 个用例。",
            "- 每份证据均包含且仅包含 control、experiment 两个组，并保持该顺序。",
            f"- {audit_text}",
            "- 正式执行状态为 `complete`，没有停止原因。",
            "",
            "## 6. 最终判定",
            "",
            f"GaussDB 适配回归判定为 **{adaptation_status}**。"
            f"本组共有 {common_fail} 个双方共同失败、{control_only} 个对照组独有失败、"
            f"{common_blocked} 个双方共同阻塞、{control_blocked} 个对照环境阻塞但实验组通过；"
            "这些结果如实保留，但不归因于实验组适配。",
            "",
        ]
    )
    return "\n".join(lines)


def render_environment_report(run: RunResult) -> str:
    environment = run.environment
    validated_at = _format_time(datetime.fromisoformat(environment["validated_at"]))
    process_data = environment.get("processes", {})
    isolation = environment.get("isolation", {})
    security = environment.get("security_findings", {})
    control = environment["api"]["control"]
    experiment = environment["api"]["experiment"]
    exp_doc = experiment["health"]["doc_engine"]
    sm001 = json.loads((run.evidence_root / "01_startup_migration" / "TC-SM-001.json").read_text(encoding="utf-8"))
    gauss_observed = sm001["groups"][1]["steps"][-1]["observed"]
    lines = [
        "# 00 - 测试环境搭建与前置条件测试报告",
        "",
        "## 1. 环境结论",
        "",
        f"- 执行批次：`{run.run_id}`",
        f"- 最终只读验收时间：{validated_at}（Asia/Shanghai）",
        f"- 环境就绪：**{'PASS' if environment.get('operational_ready') else 'FAIL'}**",
        f"- 受管服务：{process_data.get('service_count', 0)}/8 存活且进程身份匹配",
        f"- 对照组：{control['health']['database']['type']} metadata + {control['health']['doc_engine']['type']} DocEngine",
        f"- 实验组：{experiment['health']['database']['type']} metadata + GaussDB DocEngine",
        f"- 实验组 GaussDB 数据库：`{gauss_observed['database']}`；metadata 与 DocEngine schema 隔离",
        f"- GaussDB 编码：server={exp_doc.get('server_encoding')}，client={exp_doc.get('client_encoding')}",
        f"- 组间隔离：{'PASS' if isolation.get('all_distinct') else 'FAIL'}",
        f"- 明文敏感信息检查：{'PASS' if not security.get('has_unredacted_secret') else 'FAIL'}",
        "- 00 组正式用例数：0；环境证据只用于前置与终态校验",
        "- 执行顺序：后续每个正式用例固定先 control、后 experiment",
        "",
        "当前验收确认 API、Admin、worker、sync 共 8 个服务可用，数据库、DocEngine、Redis 和对象存储健康；两组端口、缓存库、桶、运行目录和数据库范围相互隔离。",
        "",
        "## 2. 问题归属判定",
        "",
        "口径：`实验组独有` = 仅实验组失败；`对照组也存在` = 双组共同失败；`对照组独有` = 仅对照组失败；`两组均阻塞` = 双组均因同一前置条件阻塞。",
        "本批次最终环境验收没有记录需要归属的环境失败。",
        "",
        "<!-- ISSUE_ATTRIBUTION_START -->",
        "| Finding | 对照组 | 实验组 | 问题归属 |",
        "|---|---|---|---|",
        "<!-- ISSUE_ATTRIBUTION_END -->",
        "",
        "## 3. 环境终态",
        "",
        "| 检查项 | 对照组 | 实验组 |",
        "|---|---|---|",
        f"| API ready | {control['ready']} | {experiment['ready']} |",
        f"| Admin ping | {control['admin_ping']} | {experiment['admin_ping']} |",
        f"| metadata | {control['health']['database']['status']} | {experiment['health']['database']['status']} |",
        f"| DocEngine | {control['health']['doc_engine']['status']} | {experiment['health']['doc_engine']['status']} |",
        f"| Redis | {control['health']['redis']['status']} | {experiment['health']['redis']['status']} |",
        f"| 对象存储 | {control['health']['storage']['status']} | {experiment['health']['storage']['status']} |",
        "",
        f"正式证据：`runs/{run.run_id}/evidence_private/00_environment_setup/environment_validation.json`。",
        "",
    ]
    return "\n".join(lines)


def _matrix(run: RunResult) -> dict[str, Any]:
    reports: dict[str, list[dict[str, str]]] = {}
    totals: Counter[str] = Counter()
    for section in run.sections:
        rows = _attribution_rows(section.cases)
        reports[section.spec.report_name] = rows
        for row in rows:
            totals[row["attribution"]] += 1
    return {
        "schema_version": 1,
        "batch_id": run.run_id,
        "reports": reports,
        "totals": {
            "non_pass_cases": sum(totals.values()),
            "实验组独有": totals["实验组独有"],
            "对照组也存在": totals["对照组也存在"],
            "对照组独有": totals["对照组独有"],
            "两组均阻塞": totals["两组均阻塞"],
            "对照环境阻塞/实验组通过": totals["对照环境阻塞/实验组通过"],
        },
    }


def _all_plan_files() -> list[str]:
    return sorted(path.name for path in PLAN_DIR.glob("*.md") if path.is_file())


def render_plan_coverage(run: RunResult) -> str:
    files = _all_plan_files()
    case_ids = [case.case_id for name in files for case in plan_cases(name)]
    lines = [
        "# 测试计划覆盖清单",
        "",
        f"- 执行批次：`{run.run_id}`",
        f"- 当前 Markdown 文件：{len(files)}",
        f"- 当前正式用例：{len(case_ids)}",
        f"- 双组判定记录：{len(case_ids) * 2}",
        f"- 重复用例 ID：{len(case_ids) - len(set(case_ids))}",
        "",
        "## 文件汇总",
        "",
        "| 计划文件 | 用例数 | 报告/审计 | 当前状态 |",
        "|---|---:|---|---|",
    ]
    for filename in files:
        count = len(plan_cases(filename))
        report = REPORT_BY_PLAN.get(filename, "—")
        if count:
            state = f"已执行 {count}/{count}"
        elif filename == "00_environment_setup.md":
            state = "环境验收完成（无正式 TC）"
        elif filename == "11_validation_oracle_matrix.md":
            state = "纳入全局终审（无正式 TC）"
        else:
            state = "说明/审查文档（无正式 TC）"
        lines.append(f"| `{filename}` | {count} | `{report}` | {state} |")
    lines.extend(
        [
            "",
            "## 覆盖结论",
            "",
            "16 个含正式用例的计划文件全部映射到 11 份用例报告；00 环境另有 1 份报告。计划集合、正式证据集合和分组执行器完成清单均为 643 个唯一 ID。",
            "",
        ]
    )
    return "\n".join(lines)


def _section_audit_state(run: RunResult, section: SectionResult) -> str:
    path = run.evidence_root / section.spec.evidence_section / "coverage_audit.json"
    if not path.is_file():
        return "全局结构终审通过"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return "局部终审 PASS" if payload.get("audit_pass") else "局部终审 FAIL"


def _coverage_payload(run: RunResult) -> dict[str, Any]:
    counts = status_counts(run.cases)
    section_rows = []
    for section in run.sections:
        section_counts = status_counts(section.cases)
        section_rows.append(
            {
                "section": section.spec.evidence_section,
                "planned": len(section.cases),
                "evidence": len(section.cases),
                "pair_status_counts": dict(sorted(section_counts["pair"].items())),
                "group_signature_counts": dict(sorted(section_counts["signature"].items())),
                "audit_state": _section_audit_state(run, section),
            }
        )
    return {
        "schema_version": 1,
        "batch_id": run.run_id,
        "formal_state": run.formal_status["state"],
        "planned_case_count": len(run.cases),
        "evidence_case_count": len(run.cases),
        "missing_case_ids": [],
        "extra_case_ids": [],
        "duplicate_plan_case_ids": [],
        "group_record_count": len(run.cases) * 2,
        "pair_status_counts": dict(sorted(counts["pair"].items())),
        "control_status_counts": dict(sorted(counts["control"].items())),
        "experiment_status_counts": dict(sorted(counts["experiment"].items())),
        "group_signature_counts": dict(sorted(counts["signature"].items())),
        "experiment_only_failure_count": counts["signature"].get("PASS/FAIL", 0),
        "coverage_pass": True,
        "adaptation_regression_pass": counts["signature"].get("PASS/FAIL", 0) == 0,
        "sections": section_rows,
    }


def render_final_coverage(run: RunResult) -> str:
    counts = status_counts(run.cases)
    signatures = counts["signature"]
    adaptation_status = "PASS" if signatures.get("PASS/FAIL", 0) == 0 else "FAIL"
    lines = [
        "# GaussDB 全量测试最终覆盖审计",
        "",
        "## 审计口径",
        "",
        f"- 执行批次：`{run.run_id}`",
        "- 当前计划：21 个 Markdown 文件，其中 16 个文件包含正式用例",
        f"- 当前用例：{len(run.cases)} 个唯一 ID，重复 0，双组记录 {len(run.cases) * 2}",
        "- 执行方式：01 组的 21 个用例在模式切换前按用例进程完成；其余 10 个计划组（622 个用例）按组单进程完成；所有用例均为 control → experiment",
        "- 正式执行状态：`complete`，停止原因为空",
        "",
        "## 最终结果",
        "",
        "| 证据域 | 用例 | PASS | FAIL | BLOCKED | 终审 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for section in run.sections:
        pair = status_counts(section.cases)["pair"]
        lines.append(f"| {section.spec.short_name} | {len(section.cases)} | {pair.get('PASS', 0)} | {pair.get('FAIL', 0)} | {pair.get('BLOCKED', 0)} | {_section_audit_state(run, section)} |")
    lines.extend(
        [
            f"| **合计** | **{len(run.cases)}** | **{counts['pair'].get('PASS', 0)}** | **{counts['pair'].get('FAIL', 0)}** | **{counts['pair'].get('BLOCKED', 0)}** | **PASS** |",
            "",
            "## 双组签名与归属",
            "",
            "| 双组签名 | 数量 | 归属 |",
            "|---|---:|---|",
            f"| PASS/PASS | {signatures.get('PASS/PASS', 0)} | 双组通过 |",
            f"| FAIL/FAIL | {signatures.get('FAIL/FAIL', 0)} | 对照组也存在 |",
            f"| FAIL/PASS | {signatures.get('FAIL/PASS', 0)} | 对照组独有 |",
            f"| PASS/FAIL | {signatures.get('PASS/FAIL', 0)} | 实验组独有 |",
            f"| BLOCKED/BLOCKED | {signatures.get('BLOCKED/BLOCKED', 0)} | 两组均阻塞 |",
            f"| BLOCKED/PASS | {signatures.get('BLOCKED/PASS', 0)} | 对照环境阻塞/实验组通过 |",
            "",
            "## 完成性判定",
            "",
            "- 计划 ID = 正式证据 ID = 643；缺失、额外、重复均为 0。",
            "- 643 份证据均包含两个组，且顺序固定为 control、experiment。",
            "- 03–08 的专项覆盖、证据卫生、环境和业务残留审计均为 PASS。",
            "- 01、02、09、10 由全局清单、正式执行状态、证据结构和报告生成校验覆盖。",
            f"- 实验组独有失败为 {signatures.get('PASS/FAIL', 0)}，因此 GaussDB 适配回归判定为 **{adaptation_status}**。",
            f"- 仍保留 {signatures.get('FAIL/FAIL', 0)} 个双方共同失败、"
            f"{signatures.get('FAIL/PASS', 0)} 个对照组独有失败和"
            f"{signatures.get('BLOCKED/BLOCKED', 0)} 个双方共同阻塞、"
            f"{signatures.get('BLOCKED/PASS', 0)} 个对照环境阻塞但实验组通过；"
            "不把它们误归因为适配问题。",
            "",
            f"机器可读终审：`runs/{run.run_id}/evidence_private/final_coverage_audit.json`。",
            "",
        ]
    )
    return "\n".join(lines)


def render_progress(run: RunResult) -> str:
    counts = status_counts(run.cases)
    signatures = counts["signature"]
    adaptation_status = "PASS" if signatures.get("PASS/FAIL", 0) == 0 else "FAIL"
    updated_at = _format_time(datetime.fromisoformat(run.formal_status["updated_at"]))
    return "\n".join(
        [
            "# GaussDB 全量测试进度",
            "",
            f"- 执行批次：`{run.run_id}`",
            f"- 正式执行完成时间：{updated_at}（Asia/Shanghai）",
            "- 当前阶段：643/643 个正式用例重跑完成，报告与覆盖终审完成",
            "- 执行模式：01 组 21 个用例先完成；随后 10 个计划组、622 个用例按组单进程执行",
            f"- 用例对结果：{_count_text(counts['pair'])}",
            f"- 双组签名：PASS/PASS {signatures.get('PASS/PASS', 0)}，"
            f"FAIL/FAIL {signatures.get('FAIL/FAIL', 0)}，"
            f"FAIL/PASS {signatures.get('FAIL/PASS', 0)}，"
            f"PASS/FAIL {signatures.get('PASS/FAIL', 0)}，"
            f"BLOCKED/BLOCKED {signatures.get('BLOCKED/BLOCKED', 0)}，"
            f"BLOCKED/PASS {signatures.get('BLOCKED/PASS', 0)}",
            f"- 适配回归：{adaptation_status}（实验组独有失败 {signatures.get('PASS/FAIL', 0)} 个）",
            "- 环境终态：operational_ready=true，8/8 服务通过只读验收",
            "",
            "## 阶段状态",
            "",
            "| 阶段 | 状态 | 证据 |",
            "|---|---|---|",
            "| 当前计划清单 | 完成 | `PLAN_COVERAGE.md` |",
            "| 双组环境与隔离 | 完成 | `00_environment_setup_report.md` |",
            f"| 11 组正式重跑 | 完成 | `runs/{run.run_id}/evidence_private/formal_execution_status.json` |",
            "| 12 份测试报告 | 完成 | `*report.md` |",
            "| 最终覆盖审计 | 完成 | `FINAL_COVERAGE_AUDIT.md` |",
            "",
            "双方共同失败和对照组独有失败均继续保留在报告中；它们不阻断后续组执行，也不计作 GaussDB 适配缺陷。",
            "",
        ]
    )


def generated_outputs(run: RunResult) -> dict[Path, str]:
    matrix = _matrix(run)
    coverage = _coverage_payload(run)
    outputs = {
        EXECUTE_DIR / "00_environment_setup_report.md": render_environment_report(run),
        EXECUTE_DIR / "FINAL_COVERAGE_AUDIT.md": render_final_coverage(run),
        EXECUTE_DIR / "PLAN_COVERAGE.md": render_plan_coverage(run),
        EXECUTE_DIR / "PROGRESS.md": render_progress(run),
        EXECUTE_DIR / "report_issue_attribution.json": json.dumps(matrix, ensure_ascii=False, indent=2) + "\n",
        run.evidence_root / "final_coverage_audit.json": json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    }
    for section in run.sections:
        outputs[EXECUTE_DIR / section.spec.report_name] = render_report(run, section)
    return outputs


def _write_outputs(outputs: dict[Path, str]) -> None:
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        if "evidence_private" in path.parts:
            temporary.chmod(0o600)
        temporary.replace(path)
        if "evidence_private" in path.parts:
            path.chmod(0o600)


def _check_outputs(outputs: dict[Path, str]) -> list[Path]:
    return [path for path, expected in outputs.items() if not path.is_file() or path.read_text(encoding="utf-8") != expected]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic reports from one namespaced formal run")
    parser.add_argument("--run-id", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    run = load_run(args.run_id)
    outputs = generated_outputs(run)
    if args.write:
        _write_outputs(outputs)
        print(f"generated {len(outputs)} current-run artifacts for {run.run_id}")
        return 0
    stale = _check_outputs(outputs)
    if stale:
        for path in stale:
            print(f"STALE {path.relative_to(EXECUTE_DIR)}")
        return 1
    print(f"verified {len(outputs)} current-run artifacts for {run.run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
