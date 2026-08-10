#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    evidence_dir,
)

EXECUTE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = evidence_dir("02_user_management")
TARGET_EMAIL = re.compile(r"^um[0-9][A-Za-z0-9-]*@fresh\.invalid$")
BASELINE_EMAIL = "um001@fresh.invalid"


def _load_module(filename: str, name: str):
    path = EXECUTE_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def target_users_in_cleanup_order(users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets = [item for item in users if isinstance(item, dict) and TARGET_EMAIL.fullmatch(str(item.get("email", "")))]
    return sorted(
        targets,
        key=lambda item: (
            str(item.get("email")) == BASELINE_EMAIL,
            str(item.get("email")),
        ),
    )


def _remaining_user_graphs(runner, group: str) -> list[dict[str, Any]]:
    connection, user_table, _namespace = runner._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id,email,status,is_active,is_superuser FROM {user_table} WHERE email LIKE %s ORDER BY email",
                ("um%@fresh.invalid",),
            )
            users = [row for row in cursor.fetchall() if TARGET_EMAIL.fullmatch(str(row[1]))]
            residuals = []
            for user_id, email, status, is_active, is_superuser in users:
                cursor.execute("SELECT COUNT(*) FROM tenant WHERE id=%s", (user_id,))
                tenant_count = int(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT COUNT(*) FROM user_tenant WHERE user_id=%s OR tenant_id=%s",
                    (user_id, user_id),
                )
                relation_count = int(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT COUNT(*) FROM file WHERE tenant_id=%s OR created_by=%s",
                    (user_id, user_id),
                )
                file_count = int(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT COUNT(*) FROM knowledgebase WHERE tenant_id=%s",
                    (user_id,),
                )
                dataset_count = int(cursor.fetchone()[0])
                residuals.append(
                    {
                        "email": email,
                        "user_id_fingerprint": runner._fingerprint(user_id),
                        "status": status,
                        "is_active": is_active,
                        "is_superuser": bool(is_superuser),
                        "tenant_count": tenant_count,
                        "relation_count": relation_count,
                        "file_count": file_count,
                        "dataset_count": dataset_count,
                    }
                )
    finally:
        connection.close()
    return residuals


def _session_action(
    runner,
    session: requests.Session,
    base: str,
    group: str,
    label: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = session.request(method, f"{base}{path}", json=payload, timeout=90)
    raw_sha = runner._record_http(
        "TC-UM-CLEANUP",
        group,
        label,
        {"method": method, "path": path, "json": payload},
        response,
    )
    body = response.json()
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "data": body.get("data"),
        "raw_sha256": raw_sha,
    }


def run_cleanup() -> dict[str, Any]:
    runner = _load_module("fresh_02_user_management.py", "fresh_um_cleanup")
    evidence = _load_module("fresh_case_evidence.py", "fresh_evidence_cleanup")
    result: dict[str, Any] = {
        "batch_id": runner.BATCH_ID,
        "group_order": list(runner.GROUP_ORDER),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "groups": [],
    }
    for group in runner.GROUP_ORDER:
        session, base = runner._open_admin_session(group)
        before = _session_action(runner, session, base, group, "list_before_cleanup", "GET", "/users")
        users = before["data"] if isinstance(before["data"], list) else []
        targets = target_users_in_cleanup_order(users)
        actions = []
        for index, item in enumerate(targets, start=1):
            email = str(item["email"])
            encoded = requests.utils.quote(email, safe="")
            action: dict[str, Any] = {"email": email}
            try:
                if bool(item.get("is_superuser")):
                    revoked = _session_action(
                        runner,
                        session,
                        base,
                        group,
                        f"{index:02d}_revoke_admin",
                        "DELETE",
                        f"/users/{encoded}/admin",
                    )
                    action["revoke_admin"] = {
                        "http_status": revoked["http_status"],
                        "code": revoked["code"],
                        "raw_sha256": revoked["raw_sha256"],
                    }
                disabled = _session_action(
                    runner,
                    session,
                    base,
                    group,
                    f"{index:02d}_disable",
                    "PUT",
                    f"/users/{encoded}/activate",
                    {"activate_status": "off"},
                )
                deleted = _session_action(
                    runner,
                    session,
                    base,
                    group,
                    f"{index:02d}_delete",
                    "DELETE",
                    f"/users/{encoded}",
                )
                action.update(
                    {
                        "disable_http_status": disabled["http_status"],
                        "disable_code": disabled["code"],
                        "delete_http_status": deleted["http_status"],
                        "delete_code": deleted["code"],
                        "raw_sha256": [
                            disabled["raw_sha256"],
                            deleted["raw_sha256"],
                        ],
                        "remaining_user_count": runner._email_count(group, [email]),
                    }
                )
            except Exception as exc:
                action.update(
                    {
                        "error_type": type(exc).__name__,
                        "remaining_user_count": runner._email_count(group, [email]),
                    }
                )
            actions.append(action)

        after = _session_action(runner, session, base, group, "list_after_cleanup", "GET", "/users")
        after_users = after["data"] if isinstance(after["data"], list) else []
        remaining_api_targets = [str(item["email"]) for item in target_users_in_cleanup_order(after_users)]
        remaining_graphs = _remaining_user_graphs(runner, group)
        orphans = runner._orphan_snapshot(group)
        api_actions_ok = all(item.get("delete_code") == 0 and item.get("remaining_user_count") == 0 for item in actions)
        clean = before["code"] == 0 and after["code"] == 0 and api_actions_ok and not remaining_api_targets and not remaining_graphs and all(value == 0 for value in orphans.values())
        result["groups"].append(
            {
                "group": group,
                "status": "PASS" if clean else "FAIL",
                "target_count": len(targets),
                "actions": actions,
                "remaining_api_targets": remaining_api_targets,
                "remaining_graphs": remaining_graphs,
                "global_orphan_counts": orphans,
                "list_raw_sha256": [before["raw_sha256"], after["raw_sha256"]],
            }
        )
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    result["status"] = "PASS" if all(item["status"] == "PASS" for item in result["groups"]) else "FAIL"
    evidence.write_evidence(EVIDENCE_DIR / "cleanup.json", result)
    return result


def main() -> None:
    result = run_cleanup()
    print(
        json.dumps(
            {
                "status": result["status"],
                "groups": {
                    item["group"]: {
                        "status": item["status"],
                        "target_count": item["target_count"],
                        "remaining_users": len(item["remaining_graphs"]),
                        "orphan_counts": item["global_orphan_counts"],
                    }
                    for item in result["groups"]
                },
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
