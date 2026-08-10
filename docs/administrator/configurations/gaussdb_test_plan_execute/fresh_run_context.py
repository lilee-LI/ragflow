#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


RUN_ID_ENV = "RAGFLOW_GAUSSDB_RUN_ID"
LEGACY_RUN_ID = "20260710_fresh_001"
RUN_ID_PATTERN = re.compile(r"^20[0-9]{6}_[a-z][a-z0-9]*_[0-9]{3}$")
EXECUTE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class RunPaths:
    run_id: str
    execute_dir: Path
    runtime: Path
    run_root: Path
    evidence: Path
    marker: Path


def resolve_run_id(value: str) -> str:
    run_id = str(value).strip()
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(f"invalid GaussDB fresh run id: {run_id!r}")
    return run_id


def build_run_paths(run_id: str, *, execute_dir: Path = EXECUTE_DIR, explicit: bool = True) -> RunPaths:
    resolved = resolve_run_id(run_id)
    runtime = execute_dir / "runtime" / resolved
    run_root = execute_dir / "runs" / resolved
    evidence = run_root / "evidence_private"
    if not explicit and resolved == LEGACY_RUN_ID:
        run_root = execute_dir
        evidence = execute_dir / "evidence_private"
    return RunPaths(resolved, execute_dir, runtime, run_root, evidence, run_root / "run_started.json")


EXPLICIT_RUN = RUN_ID_ENV in os.environ
BATCH_ID = resolve_run_id(os.environ.get(RUN_ID_ENV, LEGACY_RUN_ID))
PATHS = build_run_paths(BATCH_ID, explicit=EXPLICIT_RUN)
RUNTIME_DIR = PATHS.runtime
EVIDENCE_ROOT = PATHS.evidence


def evidence_dir(section: str) -> Path:
    if not re.fullmatch(r"[0-9]{2}_[a-z0-9_]+", section):
        raise ValueError(f"invalid evidence section: {section!r}")
    return EVIDENCE_ROOT / section


def initialize_fresh_run(paths: RunPaths, *, confirm: str) -> dict[str, str]:
    if confirm != paths.run_id:
        raise ValueError("confirmation does not match run id")
    if paths.run_root.exists() or paths.runtime.exists():
        raise FileExistsError("fresh run or runtime already exists")

    paths.run_root.parent.mkdir(parents=True, exist_ok=True)
    paths.run_root.parent.chmod(0o700)
    paths.run_root.mkdir(mode=0o700)
    paths.run_root.chmod(0o700)
    payload = {
        "run_id": paths.run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    paths.marker.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    paths.marker.chmod(0o600)
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize a namespaced GaussDB verification run")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="create the one-shot run marker")
    init_parser.add_argument("--confirm", required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "init":
        if not EXPLICIT_RUN:
            raise ValueError(f"{RUN_ID_ENV} must be explicitly set")
        if args.confirm != BATCH_ID:
            raise ValueError("confirmation does not match explicit run id")
        payload = initialize_fresh_run(PATHS, confirm=args.confirm)
        print(json.dumps(payload, sort_keys=True))
        return 0
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
