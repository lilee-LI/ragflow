#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import importlib.util
import json
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from itsdangerous import TimestampSigner
from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer
from ruamel.yaml import YAML

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    RUNTIME_DIR,
    evidence_dir,
)

GROUP_ORDER = ("control", "experiment")
EXECUTE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTE_DIR.parents[3]
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
EVIDENCE_DIR = evidence_dir("03_authentication")
RAW_DIR = EVIDENCE_DIR / "raw"
PLAN_FILES = (
    EXECUTE_DIR.parent / "gaussdb_test_plan" / "03_auth_token_session.md",
    EXECUTE_DIR.parent / "gaussdb_test_plan" / "03_auth_supplement.md",
)
BASE_EMAIL = "at001@fresh.invalid"
BASE_PASSWORD = "AT001-Test@123"
DISABLED_EMAIL = "at005@fresh.invalid"
DISABLED_PASSWORD = "AT005-Test@123"
TIKTOKEN_ENCODING_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
TIKTOKEN_CACHE_KEY = hashlib.sha1(TIKTOKEN_ENCODING_URL.encode()).hexdigest()
TIKTOKEN_ENCODING_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"


def _case_titles() -> dict[str, str]:
    result: dict[str, str] = {}
    pattern = re.compile(r"^### (TC-[A-Z0-9-]+-\d{3}):\s*(.+)$", re.MULTILINE)
    for path in PLAN_FILES:
        for case_id, title in pattern.findall(path.read_text(encoding="utf-8")):
            if case_id in result:
                raise ValueError(f"duplicate case id: {case_id}")
            result[case_id] = title.strip()
    if len(result) != 67:
        raise ValueError(f"expected 67 auth cases, found {len(result)}")
    return result


CASE_TITLES = _case_titles()


def successful_login_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("authorization_present") is True
        and observed.get("decoded_token_matches_database") is True
        and observed.get("access_state_rotated") is True
        and observed.get("last_login_changed") is True
        and observed.get("update_time_changed") is True
        and observed.get("safe_response") is True
        and observed.get("session_cookie_present") is True
    )


def rejected_login_contract_ok(observed: dict[str, Any], expected_code: int) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == expected_code
        and observed.get("authorization_absent") is True
        and observed.get("auth_state_unchanged") is True
        and observed.get("last_login_unchanged") is True
    )


def logout_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("logout_http_status") == 200
        and observed.get("logout_code") == 0
        and observed.get("logout_data") is True
        and observed.get("invalid_prefix") is True
        and observed.get("old_token_http_status") == 401
        and observed.get("old_token_code") == 401
    )


def oauth_new_user_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("callback_http_status") == 302
        and observed.get("auth_query_present") is True
        and observed.get("decoded_token_matches_database") is True
        and observed.get("user_count") == 1
        and observed.get("tenant_count") == 1
        and observed.get("owner_relation_count") == 1
        and observed.get("root_file_count") == 1
        and observed.get("login_channel") == "testoauth"
    )


def oauth_existing_user_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("callback_http_status") == 302
        and observed.get("auth_query_present") is True
        and observed.get("decoded_token_matches_database") is True
        and observed.get("user_count") == 1
        and observed.get("access_state_rotated") is True
        and observed.get("last_login_unchanged") is True
    )


def captcha_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("http_status") == 200 and observed.get("jpeg_content_type") is True and observed.get("captcha_length") == 4 and observed.get("captcha_ttl_in_range") is True


def otp_send_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("data") is True
        and observed.get("stored_hash_format") is True
        and observed.get("otp_ttl_in_range") is True
        and observed.get("last_sent_present") is True
        and observed.get("mail_count") == 1
        and observed.get("mail_otp_length") == 4
    )


def password_reset_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("authorization_present") is True
        and observed.get("authorization_usable") is True
        and observed.get("password_hash_changed") is True
        and observed.get("verified_key_absent") is True
        and observed.get("new_password_login_code") == 0
        and observed.get("old_password_login_code") == 109
    )


def api_token_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("token_prefix") is True
        and observed.get("beta_length") == 32
        and observed.get("tenant_matches") is True
        and observed.get("database_count") == 1
        and observed.get("database_values_match") is True
    )


def session_auth_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("login_code") == 0
        and observed.get("session_cookie_present") is True
        and observed.get("profile_http_status") == 200
        and observed.get("profile_code") == 0
        and observed.get("profile_email_matches") is True
    )


def encryption_chain_contract_ok(observed: dict[str, Any]) -> bool:
    return all(
        observed.get(key) is True
        for key in (
            "rsa_roundtrip_to_base64",
            "decrypted_is_not_plaintext",
            "hash_accepts_base64",
            "hash_rejects_plaintext",
            "signed_access_roundtrip",
            "tampered_signature_rejected",
        )
    )


def user_query_filter_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        all(
            observed.get(key) == 0
            for key in (
                "empty_count",
                "none_count",
                "space_count",
                "short_count",
                "invalid_prefix_count",
            )
        )
        and observed.get("normal_query_executed") is True
        and observed.get("invalid_row_exists") is True
    )


def registration_disabled_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("config_http_status") == 200
        and observed.get("config_code") == 0
        and observed.get("register_enabled") == 0
        and observed.get("password_http_status") == 200
        and observed.get("password_code") == 103
        and observed.get("password_graph_count") == 0
        and observed.get("oauth_http_status") == 302
        and observed.get("oauth_error_present") is True
        and observed.get("oauth_auth_absent") is True
        and observed.get("oauth_graph_count") == 0
        and observed.get("isolated_process_stopped") is True
        and observed.get("main_api_healthy") is True
    )


def active_required_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("login_code") == 0
        and observed.get("disable_code") == 0
        and observed.get("route_http_status") == 200
        and observed.get("route_code") == 403
        and observed.get("reactivate_code") == 0
        and observed.get("isolated_process_stopped") is True
        and observed.get("main_api_healthy") is True
    )


def metadata_fault_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("proxy_down_confirmed") is True
        and observed.get("login_http_status") == 500
        and observed.get("login_failed_finitely") is True
        and observed.get("profile_http_status") == 500
        and observed.get("profile_failed_finitely") is True
        and observed.get("proxy_restored") is True
        and observed.get("recovery_login_code") == 0
        and observed.get("main_api_healthy") is True
    )


def empty_string_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = observed.get("user_service_empty_count") == 0 and observed.get("orm_nickname") == "" and observed.get("login_restored") is True and observed.get("nickname_restored") is True
    if group == "control":
        return common and observed.get("physical_access_is_empty") is True and observed.get("physical_nickname_is_empty") is True
    if group == "experiment":
        return common and observed.get("physical_access_is_null") is True and observed.get("physical_nickname_is_null") is True
    raise ValueError("unknown group")


def registration_compensation_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("normal_register_code") == 0
        and observed.get("normal_graph_count") == 4
        and observed.get("normal_cleanup_succeeded") is True
        and observed.get("fault_request_failed") is True
        and observed.get("table_restored") is True
        and observed.get("failed_graph_count") == 0
        and observed.get("recovery_login_code") == 0
    )


def composite_token_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("create_codes") == [0, 0, 0]
        and observed.get("created_distinct_count") == 3
        and observed.get("duplicate_groups") == 0
        and observed.get("duplicate_insert_rejected") is True
        and observed.get("count_unchanged_after_duplicate") is True
        and observed.get("delete_codes") == [0, 0, 0]
        and observed.get("remaining_fixture_count") == 0
    )


def soft_deleted_registration_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("status_before") == "0"
        and observed.get("http_status") == 200
        and observed.get("code") == 103
        and observed.get("duplicate_message") is True
        and observed.get("user_count") == 1
        and observed.get("status_after") == "0"
    )


def soft_deleted_reset_security_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("status") == "0" and observed.get("security_rejection_http_400") is True and observed.get("reset_succeeded") is False and observed.get("password_hash_unchanged") is True


def soft_deleted_oauth_security_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("status") == "0"
        and observed.get("callback_http_status") == 302
        and observed.get("error_present") is True
        and observed.get("auth_absent") is True
        and observed.get("access_state_unchanged") is True
        and observed.get("last_login_unchanged") is True
    )


def keypair_match_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("public_fingerprints_match") is True
        and observed.get("key_bits") == 2048
        and observed.get("login_http_status") == 200
        and observed.get("login_code") == 0
        and observed.get("signed_header_present") is True
        and observed.get("issued_value_matches_database") is True
    )


def keypair_mismatch_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("isolated_public_differs") is True
        and observed.get("http_status") == 200
        and observed.get("code") == 500
        and observed.get("message") == "Fail to crypt password"
        and int(observed.get("decryption_error_marker_count", 0)) >= 1
        and observed.get("main_private_unchanged") is True
        and observed.get("isolated_process_stopped") is True
        and observed.get("main_api_healthy") is True
    )


def token_401_cleanup_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("frontend_probe_passed") is True
        and observed.get("authorization_absent") is True
        and observed.get("token_key_absent") is True
        and observed.get("user_info_absent") is True
        and observed.get("redirect_calls") == 1
    )


def token_401_single_redirect_contract_ok(observed: dict[str, Any]) -> bool:
    return token_401_cleanup_contract_ok(observed)


def oauth_callback_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("frontend_probe_passed") is True
        and observed.get("authorization_equals_callback") is True
        and observed.get("bearer_prefix_absent") is True
        and observed.get("user_info_absent") is True
        and observed.get("query_auth_removed") is True
        and observed.get("navigate_root_calls") == 1
    )


def oauth_callback_noop_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("frontend_probe_passed") is True and observed.get("authorization_absent") is True and observed.get("set_search_calls") == 0 and observed.get("navigate_calls") == 0


def short_password_registration_security_contract_ok(
    observed: dict[str, Any],
) -> bool:
    return observed.get("http_status") == 400 and observed.get("account_count") == 0 and observed.get("signed_header_absent") is True and observed.get("cleanup_succeeded") is True


def short_password_login_security_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("registration_rejected") is True
        and observed.get("account_count_before_login") == 0
        and observed.get("login_succeeded") is False
        and observed.get("signed_header_absent") is True
        and observed.get("cleanup_succeeded") is True
    )


def invalid_json_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("http_status") == 400


def missing_password_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("http_status") == 200 and observed.get("code") == 500 and observed.get("message") == "Fail to crypt password"


def oversize_request_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 413
        and observed.get("configured_limit") == 1048576
        and observed.get("request_length_exceeds_limit") is True
        and observed.get("isolated_process_stopped") is True
        and observed.get("main_api_healthy") is True
    )


def _load_module(filename: str, name: str):
    path = EXECUTE_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BASE = _load_module("fresh_02_user_management.py", "fresh_03_base")
BASE.EVIDENCE_DIR = EVIDENCE_DIR
BASE.RAW_DIR = RAW_DIR


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    yaml = YAML()
    yaml.default_flow_style = False
    with path.open("w", encoding="utf-8") as stream:
        yaml.dump(payload, stream)
    path.chmod(0o600)


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _seed_tiktoken_cache(root: Path, *, candidates: tuple[Path, ...] | None = None) -> Path:
    target = root / TIKTOKEN_CACHE_KEY
    sources = candidates or (
        PROJECT_ROOT / "ragflow_deps" / "cl100k_base.tiktoken",
        Path(tempfile.gettempdir()) / "data-gym-cache" / TIKTOKEN_CACHE_KEY,
    )
    for source in sources:
        if not source.is_file():
            continue
        if hashlib.sha256(source.read_bytes()).hexdigest() != TIKTOKEN_ENCODING_SHA256:
            continue
        shutil.copyfile(source, target)
        target.chmod(0o600)
        if hashlib.sha256(target.read_bytes()).hexdigest() == TIKTOKEN_ENCODING_SHA256:
            return target
        target.unlink(missing_ok=True)
    raise RuntimeError("validated tiktoken dependency is unavailable")


def _prepare_isolated_runtime(
    case_id: str,
    group: str,
    port: int,
    *,
    environment_overrides: dict[str, str] | None = None,
) -> tuple[Path, dict[str, str]]:
    if group not in GROUP_ORDER:
        raise ValueError("unknown group")
    if not 1024 <= port <= 65535:
        raise ValueError("invalid isolated API port")
    case_parent = RUNTIME_DIR / "cases" / case_id
    root = case_parent / group
    if root.exists():
        if root.parent != case_parent:
            raise ValueError("unsafe isolated runtime path")
        shutil.rmtree(root)
    root.mkdir(parents=True, mode=0o700)
    shutil.copytree(RUNTIME_DIR / group / "conf", root / "conf")
    config_path = root / "conf" / "service_conf.yaml"
    config = BASE._load_yaml(config_path)
    config["ragflow"]["host"] = "127.0.0.1"
    config["ragflow"]["http_port"] = port
    _write_yaml(config_path, config)
    for asset in ("agent", "rag", "ragflow_deps"):
        (root / asset).symlink_to(PROJECT_ROOT / asset, target_is_directory=True)
    (root / "logs").mkdir(mode=0o700)
    _seed_tiktoken_cache(root)
    for path in root.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o700)
        elif path.is_file() and not path.is_symlink():
            path.chmod(0o600)

    manager = _load_module("fresh_service_manager.py", "fresh_03_runtime_manager")
    environment = manager.load_group_environment(group)
    environment.update(
        {
            "RAG_PROJECT_BASE": str(root),
            "PYTHONPATH": str(PROJECT_ROOT),
            "PYTHONUNBUFFERED": "1",
            "NLTK_DATA": str(PROJECT_ROOT / "nltk_data"),
        }
    )
    if environment_overrides:
        environment.update({str(key): str(value) for key, value in environment_overrides.items()})
    return root, environment


def _stop_isolated_api(process: subprocess.Popen[Any] | None) -> bool:
    if process is None:
        return True
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
    return process.poll() is not None


def _launch_isolated_api(
    case_id: str,
    group: str,
    environment: dict[str, str],
    port: int,
    *,
    active_required_server: bool = False,
) -> tuple[subprocess.Popen[Any], Path, float]:
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    suffix = "active_required_api" if active_required_server else "isolated_api"
    log_path = RAW_DIR / f"{case_id}_{group}_{suffix}.log"
    command = [str(PYTHON), str(EXECUTE_DIR / "fresh_active_required_server.py")] if active_required_server else [str(PYTHON), "api/ragflow_server.py", "--init-superuser"]
    with log_path.open("wb", buffering=0) as stream:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    log_path.chmod(0o600)
    started = time.monotonic()
    deadline = started + 150
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("isolated API exited before readiness")
            try:
                response = requests.get(f"http://127.0.0.1:{port}/api/v1/system/ping", timeout=2)
                if response.status_code == 200 and response.text == "pong":
                    return process, log_path, time.monotonic() - started
            except requests.RequestException:
                pass
            time.sleep(0.5)
        raise TimeoutError("isolated API readiness timeout")
    except Exception:
        _stop_isolated_api(process)
        raise


def _private_log_summary(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    return {
        "size": len(raw),
        "sha256": __import__("hashlib").sha256(raw).hexdigest(),
        "traceback_count": text.count("Traceback (most recent call last)"),
        "ready_marker_count": text.count("RAGFlow server is ready"),
    }


def _frontend_public_key_info() -> dict[str, Any]:
    from Cryptodome.PublicKey import RSA

    source_path = PROJECT_ROOT / "web" / "src" / "utils" / "index.ts"
    source = source_path.read_text(encoding="utf-8")
    match = re.search(r"const pub\s*=\s*'([^']+)'", source)
    if match is None:
        raise RuntimeError("frontend rsaPsw public key was not found")
    raw_pem = match.group(1).replace("\\n", "\n").strip()
    header = "-----BEGIN PUBLIC KEY-----"
    footer = "-----END PUBLIC KEY-----"
    if not raw_pem.startswith(header) or not raw_pem.endswith(footer):
        raise RuntimeError("frontend rsaPsw public key has invalid boundaries")
    body = re.sub(r"\s+", "", raw_pem[len(header) : -len(footer)])
    if not body or re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", body) is None:
        raise RuntimeError("frontend rsaPsw public key has invalid base64 content")
    wrapped_body = "\n".join(body[index : index + 64] for index in range(0, len(body), 64))
    pem = f"{header}\n{wrapped_body}\n{footer}"
    key = RSA.import_key(pem)
    public_der = key.public_key().export_key(format="DER")
    return {
        "_pem": pem,
        "fingerprint": hashlib.sha256(public_der).hexdigest(),
        "bits": key.size_in_bits(),
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
    }


def _parse_frontend_probe_output(output: str, case_id: str, group: str) -> dict[str, Any]:
    matches = re.findall(r"^FRESH_OBSERVED=(\{.*\})$", output, re.MULTILINE)
    if len(matches) != 1:
        raise RuntimeError(f"expected one frontend observation, found {len(matches)}")
    observed = json.loads(matches[0])
    if not isinstance(observed, dict):
        raise RuntimeError("frontend observation is not an object")
    if observed.get("case_id") != case_id or observed.get("group") != group:
        raise RuntimeError("frontend observation identity mismatch")
    return observed


def _run_frontend_probe(case_id: str, group: str) -> dict[str, Any]:
    test_path = EXECUTE_DIR / "fresh_03_auth_frontend.test.tsx"
    config_path = EXECUTE_DIR / "fresh_03_auth_frontend.jest.config.cjs"
    jest_path = PROJECT_ROOT / "web" / "node_modules" / ".bin" / "jest"
    environment = os.environ.copy()
    environment.update(
        {
            "FRESH_CASE": case_id,
            "FRESH_GROUP": group,
            "NODE_PATH": str(PROJECT_ROOT / "web" / "node_modules"),
        }
    )
    completed = subprocess.run(
        [
            str(jest_path),
            "--config",
            str(config_path),
            "--runTestsByPath",
            str(test_path),
            "--no-cache",
            "--runInBand",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
        check=False,
    )
    output = completed.stdout
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = RAW_DIR / f"{case_id}_{group}_frontend_probe.log"
    log_path.write_text(output, encoding="utf-8")
    log_path.chmod(0o600)
    observed = _parse_frontend_probe_output(output, case_id, group)
    observed["frontend_probe_passed"] = completed.returncode == 0
    observed["frontend_probe_exit_code"] = completed.returncode
    observed["frontend_probe_log_sha256"] = hashlib.sha256(output.encode("utf-8")).hexdigest()
    source_paths = (
        PROJECT_ROOT / "web" / "src" / "utils" / "request.ts",
        PROJECT_ROOT / "web" / "src" / "utils" / "authorization-util.ts",
        PROJECT_ROOT / "web" / "src" / "hooks" / "auth-hooks.ts",
        PROJECT_ROOT / "web" / "src" / "constants" / "authorization.ts",
    )
    observed["frontend_source_sha256"] = hashlib.sha256(b"".join(path.read_bytes() for path in source_paths)).hexdigest()
    return observed


def _private_key_public_info(path: Path) -> dict[str, Any]:
    from Cryptodome.PublicKey import RSA

    key = RSA.import_key(path.read_bytes(), passphrase="Welcome")
    public_der = key.public_key().export_key(format="DER")
    return {
        "fingerprint": hashlib.sha256(public_der).hexdigest(),
        "bits": key.size_in_bits(),
    }


def _encrypt_with_public_pem(password: str, pem: str) -> str:
    import base64
    from Cryptodome.Cipher import PKCS1_v1_5
    from Cryptodome.PublicKey import RSA

    key = RSA.import_key(pem)
    encoded = base64.b64encode(password.encode("utf-8"))
    return base64.b64encode(PKCS1_v1_5.new(key).encrypt(encoded)).decode("ascii")


def _login_with_frontend_public_key(
    case_id: str,
    group: str,
    label: str,
    api_base: str,
    email: str,
    password: str,
    frontend: dict[str, Any],
) -> dict[str, Any]:
    return _isolated_request(
        case_id,
        group,
        label,
        api_base,
        "POST",
        "/auth/login",
        payload={
            "email": email,
            "password": _encrypt_with_public_pem(password, frontend["_pem"]),
        },
    )


def _replace_isolated_private_key_with_mismatch(root: Path) -> dict[str, Any]:
    from Cryptodome.PublicKey import RSA

    path = root / "conf" / "private.pem"
    generated = RSA.generate(2048)
    path.write_bytes(
        generated.export_key(
            format="PEM",
            passphrase="Welcome",
            pkcs=8,
            protection="PBKDF2WithHMAC-SHA1AndAES128-CBC",
        )
    )
    path.chmod(0o600)
    return _private_key_public_info(path)


def _main_api_healthy(group: str) -> bool:
    try:
        response = requests.get(f"{BASE._api_base(group)}/system/ping", timeout=5)
        return response.status_code == 200 and response.text == "pong"
    except requests.RequestException:
        return False


def _isolated_request(
    case_id: str,
    group: str,
    label: str,
    base: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    auth: str | None = None,
    timeout: float = 45,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {auth}"} if auth else {}
    response = requests.request(
        method,
        f"{base}{path}",
        headers=headers,
        json=payload,
        timeout=timeout,
        allow_redirects=False,
    )
    raw_sha = BASE._record_http(
        case_id,
        group,
        label,
        {"method": method, "path": path, "Authorization": auth, "json": payload},
        response,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    data = body.get("data")
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "message": body.get("message"),
        "data": data,
        "location": response.headers.get("Location"),
        "authorization_present": bool(response.headers.get("Authorization")),
        "raw_sha256": raw_sha,
        "_auth": response.headers.get("Authorization"),
    }


def _registration_graph_snapshot(group: str, email: str) -> dict[str, Any]:
    connection, user_table, _namespace = BASE._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT id FROM {user_table} WHERE email=%s", (email,))
            rows = cursor.fetchall()
            if len(rows) != 1:
                return {
                    "user_count": len(rows),
                    "tenant_count": 0,
                    "owner_relation_count": 0,
                    "root_file_count": 0,
                    "graph_count": len(rows),
                }
            user_id = str(rows[0][0])
            cursor.execute("SELECT COUNT(*) FROM tenant WHERE id=%s", (user_id,))
            tenant_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM user_tenant WHERE user_id=%s AND tenant_id=%s AND role='owner'",
                (user_id, user_id),
            )
            relation_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM file WHERE tenant_id=%s AND created_by=%s AND name='/' AND type='folder'",
                (user_id, user_id),
            )
            file_count = int(cursor.fetchone()[0])
    finally:
        connection.close()
    return {
        "user_count": 1,
        "tenant_count": tenant_count,
        "owner_relation_count": relation_count,
        "root_file_count": file_count,
        "graph_count": 1 + tenant_count + relation_count + file_count,
        "user_id_fingerprint": BASE._fingerprint(user_id),
    }


def _core_registration_counts(group: str) -> dict[str, int]:
    connection, user_table, _namespace = BASE._open_database(group)
    try:
        with connection.cursor() as cursor:
            result = {}
            for key, table in (
                ("user", user_table),
                ("tenant", "tenant"),
                ("user_tenant", "user_tenant"),
                ("file", "file"),
            ):
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                result[key] = int(cursor.fetchone()[0])
            return result
    finally:
        connection.close()


def _open_writable_database(group: str, *, autocommit: bool = True):
    if group == "control":
        import pymysql

        config = BASE._load_yaml(RUNTIME_DIR / group / "conf" / "service_conf.yaml")["mysql"]
        return pymysql.connect(
            host=config["host"],
            port=int(config["port"]),
            user=config["user"],
            password=config["password"],
            database=config["name"],
            connect_timeout=5,
            read_timeout=90,
            write_timeout=30,
            autocommit=autocommit,
        ), "`user`"

    import psycopg2

    environment = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))[group]
    schema = str(environment["GAUSSDB_METADATA_SCHEMA"])
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("unsafe metadata schema")
    connection = psycopg2.connect(
        host=environment["GAUSSDB_METADATA_HOST"],
        port=int(environment["GAUSSDB_METADATA_PORT"]),
        dbname=environment["GAUSSDB_METADATA_DBNAME"],
        user=environment["GAUSSDB_METADATA_USER"],
        password=environment["GAUSSDB_METADATA_PASSWORD"],
        connect_timeout=5,
        options=(f"-c client_encoding=UTF8 -c default_transaction_read_only=off -c search_path={schema}"),
    )
    _initialize_gauss_writable_connection(connection, schema, autocommit)
    return connection, '"user"'


def _initialize_gauss_writable_connection(connection, schema: str, autocommit: bool) -> None:
    # Persist the explicit SET outside the fixture transaction. TC-AT-053
    # intentionally rolls back a rejected duplicate insert; if SET belongs to
    # that transaction, rollback restores the startup-only search_path and the
    # next unqualified statement is sent to a DN without the metadata schema.
    connection.autocommit = True
    BASE.set_gauss_search_path(connection, schema)
    connection.autocommit = autocommit


def _set_empty_auth_fields(group: str, email: str) -> dict[str, Any]:
    connection, user_table = _open_writable_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {user_table} SET access_token=%s,nickname=%s WHERE email=%s",
                ("", "", email),
            )
            changed = int(cursor.rowcount)
            cursor.execute(
                f"SELECT access_token,nickname FROM {user_table} WHERE email=%s",
                (email,),
            )
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("empty-string fixture disappeared")
    return {
        "changed": changed,
        "physical_access_is_null": row[0] is None,
        "physical_access_is_empty": row[0] == "",
        "physical_nickname_is_null": row[1] is None,
        "physical_nickname_is_empty": row[1] == "",
    }


def _read_user_auth_fields(group: str, email: str) -> dict[str, Any]:
    connection, user_table, _namespace = BASE._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT access_token,nickname FROM {user_table} WHERE email=%s",
                (email,),
            )
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        return {"user_count": 0}
    access = str(row[0] or "")
    return {
        "user_count": 1,
        "access_length": len(access),
        "access_fingerprint": BASE._fingerprint(access),
        "nickname": "" if row[1] is None else str(row[1]),
        "nickname_physical_null": row[1] is None,
    }


def _token_set_summary(group: str, tenant_id: str, tokens: list[str]) -> dict[str, int]:
    if not tokens:
        return {"row_count": 0, "distinct_count": 0, "duplicate_groups": 0}
    connection, _user_table, _namespace = BASE._open_database(group)
    placeholders = ",".join(["%s"] * len(tokens))
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*),COUNT(DISTINCT token) FROM api_token WHERE tenant_id=%s AND token IN ({placeholders})",
                (tenant_id, *tokens),
            )
            row_count, distinct_count = cursor.fetchone()
            cursor.execute(
                f"SELECT COUNT(*) FROM (SELECT tenant_id,token FROM api_token WHERE tenant_id=%s AND token IN ({placeholders}) GROUP BY tenant_id,token HAVING COUNT(*)>1) duplicate_tokens",
                (tenant_id, *tokens),
            )
            duplicate_groups = int(cursor.fetchone()[0])
    finally:
        connection.close()
    return {
        "row_count": int(row_count),
        "distinct_count": int(distinct_count),
        "duplicate_groups": duplicate_groups,
    }


def _empty_string_service_probe(group: str, email: str) -> dict[str, Any]:
    manager = _load_module("fresh_service_manager.py", "fresh_03_empty_probe_manager")
    script = r"""
import json, sys
from api.db.services.user_service import UserService
users = list(UserService.query(email=sys.argv[1]))
values = {
    "email_count": len(users),
    "user_service_empty_count": len(list(UserService.query(access_token=""))),
    "orm_nickname": users[0].nickname if len(users) == 1 else None,
}
print("__FRESH_RESULT__" + json.dumps(values, sort_keys=True))
"""
    completed = subprocess.run(
        [str(manager.PYTHON), "-c", script, email],
        cwd=manager.PROJECT_ROOT,
        env=manager.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    marker = next(line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__"))
    return json.loads(marker)


def _proxy_configure(group: str, mode: str, *, reset: bool = True) -> dict[str, Any]:
    if mode not in {"normal", "down", "blackhole"}:
        raise ValueError("invalid proxy mode")
    config = json.loads((RUNTIME_DIR / "private_proxy_config.json").read_text(encoding="utf-8"))
    proxy_name = f"{group}_metadata"
    response = requests.post(
        f"http://{config['control_host']}:{config['control_port']}/proxies/{proxy_name}",
        headers={"Authorization": f"Bearer {config['control_token']}"},
        json={"mode": mode, "latency_ms": 0, "reset": reset},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    proxy = payload.get("proxy") if isinstance(payload.get("proxy"), dict) else {}
    return {
        "http_status": response.status_code,
        "name": proxy.get("name"),
        "mode": proxy.get("mode"),
        "active": proxy.get("active"),
        "closed": payload.get("closed"),
        "fault_hits": proxy.get("fault_hits"),
        "resets": proxy.get("resets"),
    }


def _metadata_fault_request(
    case_id: str,
    group: str,
    label: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    auth: str | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {auth}"} if auth else {}
    started = time.monotonic()
    try:
        response = requests.request(
            method,
            f"{BASE._api_base(group)}{path}",
            headers=headers,
            json=payload,
            timeout=90,
        )
        elapsed = time.monotonic() - started
        raw_sha = BASE._record_http(
            case_id,
            group,
            label,
            {"method": method, "path": path, "Authorization": auth, "json": payload},
            response,
        )
        try:
            body = response.json()
        except ValueError:
            body = {}
        return {
            "http_status": response.status_code,
            "code": body.get("code"),
            "message": body.get("message"),
            "elapsed_seconds": round(elapsed, 3),
            "failed_finitely": elapsed < 90 and not (response.status_code == 200 and body.get("code") == 0),
            "exception_type": None,
            "raw_sha256": raw_sha,
        }
    except requests.RequestException as exc:
        elapsed = time.monotonic() - started
        return {
            "http_status": None,
            "code": None,
            "message": None,
            "elapsed_seconds": round(elapsed, 3),
            "failed_finitely": elapsed < 90,
            "exception_type": type(exc).__name__,
            "raw_sha256": None,
        }


def _tenant_table_state(group: str) -> dict[str, bool]:
    connection, _user_table = _open_writable_database(group)
    try:
        with connection.cursor() as cursor:
            if group == "control":
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name IN (%s,%s)",
                    ("tenant", "tenant_at052_fault"),
                )
            else:
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_name IN (%s,%s)",
                    ("tenant", "tenant_at052_fault"),
                )
            names = {str(row[0]) for row in cursor.fetchall()}
    finally:
        connection.close()
    return {"tenant": "tenant" in names, "backup": "tenant_at052_fault" in names}


def _rename_tenant_for_fault(group: str, *, restore: bool) -> dict[str, bool]:
    before = _tenant_table_state(group)
    if restore:
        if before == {"tenant": True, "backup": False}:
            return before
        if before != {"tenant": False, "backup": True}:
            raise RuntimeError("unexpected tenant table state before restore")
        statement = "RENAME TABLE tenant_at052_fault TO tenant" if group == "control" else "ALTER TABLE tenant_at052_fault RENAME TO tenant"
    else:
        if before != {"tenant": True, "backup": False}:
            raise RuntimeError("unexpected tenant table state before fault")
        statement = "RENAME TABLE tenant TO tenant_at052_fault" if group == "control" else "ALTER TABLE tenant RENAME TO tenant_at052_fault"
    connection, _user_table = _open_writable_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement)
    finally:
        connection.close()
    return _tenant_table_state(group)


def _duplicate_api_token_row(group: str, tenant_id: str, token: str) -> dict[str, Any]:
    connection, _user_table = _open_writable_database(group, autocommit=False)
    columns = "tenant_id,token,dialog_id,source,beta,create_time,create_date,update_time,update_date"
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {columns} FROM api_token WHERE tenant_id=%s AND token=%s",
                (tenant_id, token),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("API token fixture is missing")
            cursor.execute(
                "SELECT COUNT(*) FROM api_token WHERE tenant_id=%s AND token=%s",
                (tenant_id, token),
            )
            before = int(cursor.fetchone()[0])
            rejected = False
            error_type = None
            error_code = None
            try:
                placeholders = ",".join(["%s"] * 9)
                cursor.execute(f"INSERT INTO api_token ({columns}) VALUES ({placeholders})", row)
                connection.commit()
            except Exception as exc:
                rejected = True
                error_type = type(exc).__name__
                error_code = getattr(exc, "pgcode", None)
                if error_code is None and getattr(exc, "args", None):
                    error_code = exc.args[0] if isinstance(exc.args[0], int) else None
                connection.rollback()
            cursor.execute(
                "SELECT COUNT(*) FROM api_token WHERE tenant_id=%s AND token=%s",
                (tenant_id, token),
            )
            after = int(cursor.fetchone()[0])
    finally:
        connection.close()
    return {
        "before_count": before,
        "after_count": after,
        "duplicate_insert_rejected": rejected,
        "error_type": error_type,
        "error_code": error_code,
    }


def _evidence_module():
    return _load_module("fresh_case_evidence.py", "fresh_03_evidence")


def _finalize(recorder, case_id: str) -> dict[str, Any]:
    evidence = _evidence_module()
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / f"{case_id}.json", result)
    return result


def _auth_snapshot(group: str, email: str) -> dict[str, Any]:
    connection, user_table, _namespace = BASE._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id,access_token,last_login_time,update_time,update_date,status,is_active,password FROM {user_table} WHERE email=%s",
                (email,),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    if len(rows) != 1:
        return {"user_count": len(rows)}
    user_id, access_state, last_login, update_time, update_date, status, is_active, password_hash = rows[0]
    access_text = str(access_state or "")
    password_text = str(password_hash or "")
    return {
        "user_count": 1,
        "user_id_fingerprint": BASE._fingerprint(user_id),
        "access_state_fingerprint": BASE._fingerprint(access_text),
        "access_state_length": len(access_text),
        "invalid_prefix": access_text.startswith("INVALID_"),
        "last_login_time": str(last_login) if last_login is not None else None,
        "update_time": int(update_time) if update_time is not None else None,
        "update_date": str(update_date) if update_date is not None else None,
        "status": str(status) if status is not None else None,
        "is_active": str(is_active) if is_active is not None else None,
        "password_hash_fingerprint": BASE._fingerprint(password_text),
        "password_hash_length": len(password_text),
        "password_hash_scheme": password_text.split(":", 1)[0].split("$", 1)[0],
    }


def _raw_access_state(group: str, email: str) -> str:
    connection, user_table, _namespace = BASE._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT access_token FROM {user_table} WHERE email=%s", (email,))
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("auth fixture is missing")
    return str(row[0] or "")


def _signing_secret(group: str) -> str:
    environments = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))
    explicit = environments[group].get("RAGFLOW_SECRET_KEY")
    if explicit:
        return str(explicit)
    raise RuntimeError("this fresh batch requires an explicit signing secret")


def _decoded_token_matches(group: str, token: str, email: str) -> bool:
    try:
        decoded = str(Serializer(secret_key=_signing_secret(group)).loads(token))
    except Exception:
        return False
    return decoded == _raw_access_state(group, email)


def _login(
    case_id: str,
    group: str,
    label: str,
    email: str,
    password: str,
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    client = session or requests
    wire = {"email": email, "password": BASE._encrypt_password(group, password)}
    response = client.post(f"{BASE._api_base(group)}/auth/login", json=wire, timeout=45)
    raw_sha = BASE._record_http(
        case_id,
        group,
        label,
        {"email": email, "password": password},
        response,
    )
    body = response.json()
    auth = response.headers.get("Authorization")
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "message": body.get("message"),
        "data": body.get("data"),
        "authorization_present": bool(auth),
        "authorization_fingerprint": BASE._fingerprint(auth) if auth else None,
        "session_cookie_present": bool(response.cookies),
        "raw_sha256": raw_sha,
        "_auth": auth,
    }


def _request(
    case_id: str,
    group: str,
    label: str,
    method: str,
    path: str,
    *,
    session: requests.Session | None = None,
    auth: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {auth}"} if auth is not None else {}
    client = session or requests
    response = client.request(
        method,
        f"{BASE._api_base(group)}{path}",
        headers=headers,
        json=payload,
        timeout=45,
    )
    raw_sha = BASE._record_http(
        case_id,
        group,
        label,
        {"method": method, "path": path, "Authorization": auth, "json": payload},
        response,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "message": body.get("message"),
        "data": body.get("data"),
        "content_type": response.headers.get("Content-Type"),
        "location": response.headers.get("Location"),
        "raw_sha256": raw_sha,
    }


def _raw_body_request(
    case_id: str,
    group: str,
    label: str,
    base: str,
    path: str,
    body: bytes,
    *,
    timeout: float = 45,
) -> dict[str, Any]:
    started = time.monotonic()
    response = requests.post(
        f"{base}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    elapsed = time.monotonic() - started
    raw_sha = BASE._record_http(
        case_id,
        group,
        label,
        {
            "method": "POST",
            "path": path,
            "content_type": "application/json",
            "body_length": len(body),
            "body_sha256": hashlib.sha256(body).hexdigest(),
        },
        response,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return {
        "http_status": response.status_code,
        "code": payload.get("code"),
        "message": payload.get("message"),
        "response_content_type": response.headers.get("Content-Type"),
        "response_length": len(response.content),
        "request_length": len(body),
        "request_sha256": hashlib.sha256(body).hexdigest(),
        "elapsed_seconds": elapsed,
        "raw_sha256": raw_sha,
    }


def _ensure_user(case_id: str, group: str, email: str, nickname: str, password: str) -> dict[str, Any]:
    count = BASE._email_count(group, [email])
    if count == 1:
        return {"created": False}
    if count != 0:
        raise RuntimeError("fixture email is not unique")
    result = BASE._register(
        case_id,
        group,
        f"create_{nickname.lower()}",
        {"email": email, "nickname": nickname, "password": password},
    )
    if result["code"] != 0:
        raise RuntimeError("failed to create auth fixture")
    return {"created": True, "raw_sha256": result["raw_sha256"]}


def run_at001() -> dict[str, Any]:
    case_id = "TC-AT-001"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        if BASE._email_count(group, [BASE_EMAIL]):
            BASE._delete_user_via_admin(group, BASE_EMAIL)
        created = _ensure_user(case_id, group, BASE_EMAIL, "AT001User", BASE_PASSWORD)
        before = _auth_snapshot(group, BASE_EMAIL)
        time.sleep(1.05)
        session = requests.Session()
        login = _login(case_id, group, "password_login", BASE_EMAIL, BASE_PASSWORD, session=session)
        after = _auth_snapshot(group, BASE_EMAIL)
        data = login["data"] if isinstance(login["data"], dict) else {}
        observed = {
            "http_status": login["http_status"],
            "code": login["code"],
            "authorization_present": login["authorization_present"],
            "decoded_token_matches_database": bool(login["_auth"]) and _decoded_token_matches(group, login["_auth"], BASE_EMAIL),
            "access_state_rotated": before.get("access_state_fingerprint") != after.get("access_state_fingerprint"),
            "last_login_changed": before.get("last_login_time") != after.get("last_login_time"),
            "update_time_changed": before.get("update_time") != after.get("update_time"),
            "safe_response": data.get("email") == BASE_EMAIL and not {"password", "access_token"}.intersection(data),
            "session_cookie_present": login["session_cookie_present"],
        }
        recorder.add_group(
            group,
            "PASS" if successful_login_contract_ok(observed) else "FAIL",
            [
                {"name": "create_fixture_via_api", **created},
                {
                    "name": "password_login_with_session",
                    "contract": observed,
                    "authorization_fingerprint": login["authorization_fingerprint"],
                    "raw_sha256": login["raw_sha256"],
                },
                {"name": "read_only_auth_state_before_after", "before": before, "after": after},
            ],
            oracle={"code": 0, "token_decodes_to_current_access_state": True},
        )
    return _finalize(recorder, case_id)


def _run_rejected_login(
    case_id: str,
    email: str,
    password: str,
    expected_code: int,
    expected_message: str,
    *,
    tracked_email: str | None = None,
) -> dict[str, Any]:
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        before = _auth_snapshot(group, tracked_email) if tracked_email else None
        login = _login(case_id, group, "rejected_login", email, password)
        after = _auth_snapshot(group, tracked_email) if tracked_email else None
        observed = {
            "http_status": login["http_status"],
            "code": login["code"],
            "authorization_absent": not login["authorization_present"],
            "auth_state_unchanged": before is None or before.get("access_state_fingerprint") == after.get("access_state_fingerprint"),
            "last_login_unchanged": before is None or before.get("last_login_time") == after.get("last_login_time"),
        }
        ok = rejected_login_contract_ok(observed, expected_code) and login["message"] == expected_message
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "submit_rejected_login",
                    "contract": observed,
                    "message": login["message"],
                    "raw_sha256": login["raw_sha256"],
                },
                {"name": "read_only_auth_state_unchanged", "before": before, "after": after},
            ],
            oracle={"code": expected_code, "message": expected_message},
        )
    return _finalize(recorder, case_id)


def run_at002() -> dict[str, Any]:
    return _run_rejected_login(
        "TC-AT-002",
        "at002-unregistered@fresh.invalid",
        "Anything@123",
        109,
        "Email: at002-unregistered@fresh.invalid is not registered!",
    )


def run_at003() -> dict[str, Any]:
    return _run_rejected_login(
        "TC-AT-003",
        BASE_EMAIL,
        "WrongPassword",
        109,
        "Email and password do not match!",
        tracked_email=BASE_EMAIL,
    )


def run_at004() -> dict[str, Any]:
    case_id = "TC-AT-004"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        response = _request(case_id, group, "empty_login_body", "POST", "/auth/login", payload={})
        ok = response["http_status"] == 200 and response["code"] == 109 and response["message"] == "Unauthorized!"
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [{"name": "submit_empty_json", **response}],
            oracle={"http_status": 200, "code": 109, "message": "Unauthorized!"},
        )
    return _finalize(recorder, case_id)


def run_at005() -> dict[str, Any]:
    case_id = "TC-AT-005"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        if BASE._email_count(group, [DISABLED_EMAIL]):
            BASE._delete_user_via_admin(group, DISABLED_EMAIL)
        _ensure_user(case_id, group, DISABLED_EMAIL, "AT005Disabled", DISABLED_PASSWORD)
        encoded = requests.utils.quote(DISABLED_EMAIL, safe="")
        disabled = BASE._admin_action(
            case_id,
            group,
            "disable_fixture",
            "PUT",
            f"/users/{encoded}/activate",
            {"activate_status": "off"},
        )
        before = _auth_snapshot(group, DISABLED_EMAIL)
        login = _login(case_id, group, "disabled_login", DISABLED_EMAIL, DISABLED_PASSWORD)
        after = _auth_snapshot(group, DISABLED_EMAIL)
        observed = {
            "http_status": login["http_status"],
            "code": login["code"],
            "authorization_absent": not login["authorization_present"],
            "auth_state_unchanged": before.get("access_state_fingerprint") == after.get("access_state_fingerprint"),
            "last_login_unchanged": before.get("last_login_time") == after.get("last_login_time"),
        }
        ok = (
            disabled["code"] == 0
            and after.get("is_active") == "0"
            and rejected_login_contract_ok(observed, 403)
            and login["message"] == "This account has been disabled, please contact the administrator!"
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "disable_via_admin", "code": disabled["code"], "raw_sha256": disabled["raw_sha256"]},
                {"name": "login_disabled_user", "contract": observed, "message": login["message"], "raw_sha256": login["raw_sha256"]},
                {"name": "read_only_state", "before": before, "after": after},
            ],
            oracle={"is_active": "0", "code": 403},
        )
    return _finalize(recorder, case_id)


def run_at006() -> dict[str, Any]:
    case_id = "TC-AT-006"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        before = _auth_snapshot(group, BASE_EMAIL)
        response = requests.post(
            f"{BASE._api_base(group)}/auth/login",
            json={"email": BASE_EMAIL, "password": "NOT_VALID_RSA_CIPHERTEXT!!!"},
            timeout=45,
        )
        raw_sha = BASE._record_http(
            case_id,
            group,
            "invalid_rsa_login",
            {"email": BASE_EMAIL, "password": "invalid-ciphertext"},
            response,
        )
        body = response.json()
        after = _auth_snapshot(group, BASE_EMAIL)
        ok = (
            response.status_code == 200
            and body.get("code") == 500
            and body.get("message") == "Fail to crypt password"
            and before.get("access_state_fingerprint") == after.get("access_state_fingerprint")
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "submit_invalid_rsa_ciphertext", "http_status": response.status_code, "code": body.get("code"), "message": body.get("message"), "raw_sha256": raw_sha},
                {"name": "read_only_auth_state_unchanged", "before": before, "after": after},
            ],
            oracle={"http_status": 200, "code": 500},
        )
    return _finalize(recorder, case_id)


def run_at007() -> dict[str, Any]:
    case_id = "TC-AT-007"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(case_id, group, "login", BASE_EMAIL, BASE_PASSWORD)
        profile = _request(case_id, group, "profile_with_token", "GET", "/users/me", auth=login["_auth"])
        data = profile["data"] if isinstance(profile["data"], dict) else {}
        ok = login["code"] == 0 and profile["http_status"] == 200 and profile["code"] == 0 and data.get("email") == BASE_EMAIL and _decoded_token_matches(group, login["_auth"], BASE_EMAIL)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "login_and_decode_token", "login_code": login["code"], "decoded_matches": _decoded_token_matches(group, login["_auth"], BASE_EMAIL), "raw_sha256": login["raw_sha256"]},
                {
                    "name": "access_profile_with_bearer",
                    "http_status": profile["http_status"],
                    "code": profile["code"],
                    "email_matches": data.get("email") == BASE_EMAIL,
                    "raw_sha256": profile["raw_sha256"],
                },
            ],
            oracle={"profile": [200, 0], "decoded_token_matches": True},
        )
    return _finalize(recorder, case_id)


def run_at008() -> dict[str, Any]:
    case_id = "TC-AT-008"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(case_id, group, "login", BASE_EMAIL, BASE_PASSWORD)
        logout = _request(case_id, group, "logout", "POST", "/auth/logout", auth=login["_auth"])
        state = _auth_snapshot(group, BASE_EMAIL)
        old = _request(case_id, group, "old_token_profile", "GET", "/users/me", auth=login["_auth"])
        observed = {
            "logout_http_status": logout["http_status"],
            "logout_code": logout["code"],
            "logout_data": logout["data"],
            "invalid_prefix": state.get("invalid_prefix"),
            "old_token_http_status": old["http_status"],
            "old_token_code": old["code"],
        }
        recorder.add_group(
            group,
            "PASS" if logout_contract_ok(observed) else "FAIL",
            [
                {"name": "login_then_logout", "contract": observed, "raw_sha256": [login["raw_sha256"], logout["raw_sha256"], old["raw_sha256"]]},
                {"name": "read_only_invalid_auth_state", "state": state},
            ],
            oracle={"logout": [200, 0], "old_token": [401, 401]},
        )
    return _finalize(recorder, case_id)


def run_at009() -> dict[str, Any]:
    case_id = "TC-AT-009"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        response = _request(case_id, group, "unauthenticated_logout", "POST", "/auth/logout")
        ok = response["http_status"] == 401 and response["code"] == 401
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [{"name": "logout_without_auth", **response}],
            oracle={"http_status": 401, "code": 401},
        )
    return _finalize(recorder, case_id)


def run_at010() -> dict[str, Any]:
    case_id = "TC-AT-010"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        session = requests.Session()
        login = _login(case_id, group, "session_login", BASE_EMAIL, BASE_PASSWORD, session=session)
        logout = _request(case_id, group, "session_logout", "POST", "/auth/logout", session=session)
        profile = _request(case_id, group, "profile_after_session_logout", "GET", "/users/me", session=session)
        state = _auth_snapshot(group, BASE_EMAIL)
        ok = (
            login["code"] == 0
            and login["session_cookie_present"]
            and logout["http_status"] == 200
            and logout["code"] == 0
            and profile["http_status"] == 401
            and profile["code"] == 401
            and state.get("invalid_prefix") is True
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "login_and_logout_with_session_cookie",
                    "login_code": login["code"],
                    "cookie_present": login["session_cookie_present"],
                    "logout_code": logout["code"],
                    "raw_sha256": [login["raw_sha256"], logout["raw_sha256"]],
                },
                {
                    "name": "reuse_logged_out_session",
                    "http_status": profile["http_status"],
                    "code": profile["code"],
                    "invalid_prefix": state.get("invalid_prefix"),
                    "raw_sha256": profile["raw_sha256"],
                },
            ],
            oracle={"session_profile_after_logout": [401, 401]},
        )
    return _finalize(recorder, case_id)


def _protocol_config() -> dict[str, Any]:
    return json.loads((RUNTIME_DIR / "auth_protocol" / "private_config.json").read_text(encoding="utf-8"))


def _issue_oauth_code(profile: dict[str, Any]) -> str:
    config = _protocol_config()
    response = requests.post(
        f"http://{config['http_host']}:{config['http_port']}/control/oauth-codes",
        headers={"Authorization": f"Bearer {config['control_token']}"},
        json=profile,
        timeout=5,
    )
    response.raise_for_status()
    return str(response.json()["code"])


def _oauth_start(
    case_id: str,
    group: str,
    label: str,
    session: requests.Session,
    *,
    api_base: str | None = None,
) -> dict[str, Any]:
    base = api_base or BASE._api_base(group)
    response = session.get(
        f"{base}/auth/login/testoauth",
        allow_redirects=False,
        timeout=30,
    )
    raw_sha = BASE._record_http(
        case_id,
        group,
        label,
        {"method": "GET", "path": "/auth/login/testoauth"},
        response,
    )
    location = response.headers.get("Location", "")
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    state = (query.get("state") or [""])[0]
    return {
        "http_status": response.status_code,
        "location_host": parsed.hostname,
        "location_port": parsed.port,
        "location_path": parsed.path,
        "client_id": (query.get("client_id") or [None])[0],
        "response_type": (query.get("response_type") or [None])[0],
        "redirect_uri_present": bool((query.get("redirect_uri") or [""])[0]),
        "state_present": bool(state),
        "session_cookie_present": bool(response.cookies),
        "raw_sha256": raw_sha,
        "_state": state,
    }


def _oauth_callback(
    case_id: str,
    group: str,
    label: str,
    session: requests.Session,
    code: str,
    state: str,
    *,
    api_base: str | None = None,
) -> dict[str, Any]:
    base = api_base or BASE._api_base(group)
    response = session.get(
        f"{base}/auth/oauth/testoauth/callback",
        params={"code": code, "state": state},
        allow_redirects=False,
        timeout=45,
    )
    raw_sha = BASE._record_http(
        case_id,
        group,
        label,
        {
            "method": "GET",
            "path": "/auth/oauth/testoauth/callback",
            "oauth_code_fingerprint": BASE._fingerprint(code),
            "state_fingerprint": BASE._fingerprint(state),
        },
        response,
    )
    location = response.headers.get("Location", "")
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    auth = (query.get("auth") or [None])[0]
    error = (query.get("error") or [None])[0]
    return {
        "http_status": response.status_code,
        "redirect_path": parsed.path,
        "auth_query_present": bool(auth),
        "auth_fingerprint": BASE._fingerprint(auth) if auth else None,
        "error_present": bool(error),
        "error_value": error,
        "raw_sha256": raw_sha,
        "_auth": auth,
    }


def _oauth_graph(group: str, email: str) -> dict[str, Any]:
    return BASE._registration_snapshot(group, email)


def run_at011() -> dict[str, Any]:
    case_id = "TC-AT-011"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        response = _request(case_id, group, "login_channels", "GET", "/auth/login/channels")
        channels = response["data"] if isinstance(response["data"], list) else []
        matches = [item for item in channels if isinstance(item, dict) and item.get("channel") == "testoauth"]
        ok = response["http_status"] == 200 and response["code"] == 0 and len(matches) == 1 and matches[0].get("display_name") == "Test OAuth" and matches[0].get("icon") == "sso"
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "get_public_login_channels",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "channel_count": len(channels),
                    "testoauth_exact_match": len(matches) == 1,
                    "raw_sha256": response["raw_sha256"],
                }
            ],
            oracle={"channel": "testoauth", "display_name": "Test OAuth", "icon": "sso"},
        )
    return _finalize(recorder, case_id)


def run_at012() -> dict[str, Any]:
    case_id = "TC-AT-012"
    config = _protocol_config()
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        session = requests.Session()
        start = _oauth_start(case_id, group, "oauth_redirect", session)
        ok = (
            start["http_status"] == 302
            and start["location_host"] == config["http_host"]
            and start["location_port"] == int(config["http_port"])
            and start["location_path"] == "/oauth/authorize"
            and start["client_id"] == config["oauth_client_id"]
            and start["response_type"] == "code"
            and start["redirect_uri_present"]
            and start["state_present"]
            and start["session_cookie_present"]
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "start_oauth_and_inspect_redirect",
                    **{k: v for k, v in start.items() if not k.startswith("_")},
                    "state_fingerprint": BASE._fingerprint(start["_state"]),
                }
            ],
            oracle={"http_status": 302, "provider_path": "/oauth/authorize", "client_id": "ragflow_test_client"},
        )
    return _finalize(recorder, case_id)


def run_at013() -> dict[str, Any]:
    case_id = "TC-AT-013"
    email = "at013-oauth@fresh.invalid"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        BASE._delete_user_via_admin(group, email)
        session = requests.Session()
        start = _oauth_start(case_id, group, "oauth_start", session)
        code = _issue_oauth_code({"email": email, "username": "at013oauth", "nickname": "AT013OAuth", "avatar_url": ""})
        callback = _oauth_callback(case_id, group, "oauth_new_user_callback", session, code, start["_state"])
        graph = _oauth_graph(group, email)
        observed = {
            "callback_http_status": callback["http_status"],
            "auth_query_present": callback["auth_query_present"],
            "decoded_token_matches_database": bool(callback["_auth"]) and _decoded_token_matches(group, callback["_auth"], email),
            "user_count": graph.get("user_count"),
            "tenant_count": graph.get("tenant_count"),
            "owner_relation_count": graph.get("owner_relation_count"),
            "root_file_count": graph.get("root_file_count"),
            "login_channel": graph.get("login_channel"),
        }
        recorder.add_group(
            group,
            "PASS" if oauth_new_user_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "start_and_complete_stub_oauth",
                    "start_http_status": start["http_status"],
                    "callback": {k: v for k, v in callback.items() if not k.startswith("_")},
                    "raw_sha256": [start["raw_sha256"], callback["raw_sha256"]],
                },
                {"name": "read_only_oauth_user_graph", "contract": observed, "graph": graph},
            ],
            oracle={"redirect_auth": True, "login_channel": "testoauth", "complete_user_graph": True},
        )
    return _finalize(recorder, case_id)


def run_at014() -> dict[str, Any]:
    case_id = "TC-AT-014"
    email = "at014-must-not-exist@fresh.invalid"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        BASE._delete_user_via_admin(group, email)
        session = requests.Session()
        start = _oauth_start(case_id, group, "oauth_start", session)
        code = _issue_oauth_code({"email": email, "username": "at014", "nickname": "AT014OAuth", "avatar_url": ""})
        callback = _oauth_callback(case_id, group, "oauth_invalid_state", session, code, "invalid-state")
        count = BASE._email_count(group, [email])
        ok = start["state_present"] and callback["http_status"] == 302 and callback["error_value"] == "invalid_state" and not callback["auth_query_present"] and count == 0
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "callback_with_mismatched_state",
                    "http_status": callback["http_status"],
                    "error": callback["error_value"],
                    "auth_query_present": callback["auth_query_present"],
                    "raw_sha256": [start["raw_sha256"], callback["raw_sha256"]],
                },
                {"name": "read_only_no_user_created", "user_count": count},
            ],
            oracle={"redirect_error": "invalid_state", "user_count": 0},
        )
    return _finalize(recorder, case_id)


def run_at015() -> dict[str, Any]:
    case_id = "TC-AT-015"
    email = "at015-existing@fresh.invalid"
    password = "AT015-Test@123"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        BASE._delete_user_via_admin(group, email)
        _ensure_user(case_id, group, email, "AT015Existing", password)
        before = _auth_snapshot(group, email)
        session = requests.Session()
        start = _oauth_start(case_id, group, "oauth_start", session)
        code = _issue_oauth_code({"email": email, "username": "at015", "nickname": "IgnoredNickname", "avatar_url": ""})
        callback = _oauth_callback(case_id, group, "oauth_existing_callback", session, code, start["_state"])
        after = _auth_snapshot(group, email)
        observed = {
            "callback_http_status": callback["http_status"],
            "auth_query_present": callback["auth_query_present"],
            "decoded_token_matches_database": bool(callback["_auth"]) and _decoded_token_matches(group, callback["_auth"], email),
            "user_count": after.get("user_count"),
            "access_state_rotated": before.get("access_state_fingerprint") != after.get("access_state_fingerprint"),
            "last_login_unchanged": before.get("last_login_time") == after.get("last_login_time"),
        }
        recorder.add_group(
            group,
            "PASS" if oauth_existing_user_contract_ok(observed) else "FAIL",
            [
                {"name": "oauth_callback_for_existing_user", "contract": observed, "auth_fingerprint": callback["auth_fingerprint"], "raw_sha256": [start["raw_sha256"], callback["raw_sha256"]]},
                {"name": "read_only_existing_user_before_after", "before": before, "after": after},
            ],
            oracle={"single_existing_user": True, "access_rotated": True, "last_login_unchanged": True},
        )
    return _finalize(recorder, case_id)


def _redis_client(group: str):
    import redis

    config = BASE._load_yaml(RUNTIME_DIR / group / "conf" / "service_conf.yaml")["redis"]
    host, port = str(config["host"]).rsplit(":", 1)
    return redis.Redis(
        host=host,
        port=int(port),
        db=int(config["db"]),
        username=config.get("username") or None,
        password=config.get("password") or None,
        decode_responses=True,
        socket_connect_timeout=5,
    )


def _otp_keys(email: str) -> dict[str, str]:
    return {
        "captcha": f"captcha:{email}",
        "otp": f"otp:{email}",
        "attempts": f"otp_attempts:{email}",
        "last_sent": f"otp_last_sent:{email}",
        "lock": f"otp_lock:{email}",
        "verified": f"otp:verified:{email}",
    }


def _otp_state(group: str, email: str) -> dict[str, Any]:
    client = _redis_client(group)
    keys = _otp_keys(email)
    otp_value = client.get(keys["otp"])
    parts = str(otp_value or "").split(":", 1)
    return {
        "captcha_present": bool(client.get(keys["captcha"])),
        "captcha_ttl": int(client.ttl(keys["captcha"])),
        "otp_present": bool(otp_value),
        "otp_value_fingerprint": BASE._fingerprint(otp_value) if otp_value else None,
        "stored_hash_format": len(parts) == 2 and len(parts[0]) == 64 and len(parts[1]) == 32 and all(char in "0123456789abcdef" for part in parts for char in part.lower()),
        "otp_ttl": int(client.ttl(keys["otp"])),
        "attempts": int(client.get(keys["attempts"]) or 0),
        "attempts_ttl": int(client.ttl(keys["attempts"])),
        "last_sent_present": bool(client.get(keys["last_sent"])),
        "lock_present": bool(client.get(keys["lock"])),
        "lock_ttl": int(client.ttl(keys["lock"])),
        "verified": client.get(keys["verified"]),
        "verified_ttl": int(client.ttl(keys["verified"])),
    }


def _captcha_request(case_id: str, group: str, label: str, email: str) -> dict[str, Any]:
    response = requests.post(
        f"{BASE._api_base(group)}/auth/password/forgot/captcha",
        params={"email": email},
        timeout=30,
    )
    raw_sha = BASE._record_http(
        case_id,
        group,
        label,
        {"method": "POST", "path": "/auth/password/forgot/captcha", "email": email},
        response,
    )
    client = _redis_client(group)
    value = client.get(_otp_keys(email)["captcha"])
    ttl = int(client.ttl(_otp_keys(email)["captcha"]))
    return {
        "http_status": response.status_code,
        "content_type": response.headers.get("Content-Type", ""),
        "image_length": len(response.content),
        "captcha_length": len(str(value or "")),
        "captcha_fingerprint": BASE._fingerprint(value) if value else None,
        "captcha_ttl": ttl,
        "raw_sha256": raw_sha,
        "_captcha": str(value or ""),
    }


def _send_otp(case_id: str, group: str, label: str, email: str, captcha: str) -> dict[str, Any]:
    response = requests.post(
        f"{BASE._api_base(group)}/auth/password/forgot/otp",
        json={"email": email, "captcha": captcha},
        timeout=30,
    )
    raw_sha = BASE._record_http(
        case_id,
        group,
        label,
        {
            "method": "POST",
            "path": "/auth/password/forgot/otp",
            "email": email,
            "captcha_fingerprint": BASE._fingerprint(captcha),
        },
        response,
    )
    body = response.json()
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "message": body.get("message"),
        "data": body.get("data"),
        "raw_sha256": raw_sha,
    }


def _mail_messages(email: str) -> list[dict[str, Any]]:
    config = _protocol_config()
    response = requests.get(
        f"http://{config['http_host']}:{config['http_port']}/control/messages",
        params={"email": email},
        headers={"Authorization": f"Bearer {config['control_token']}"},
        timeout=5,
    )
    response.raise_for_status()
    messages = response.json().get("messages")
    return messages if isinstance(messages, list) else []


def _latest_mail_otp(email: str) -> str:
    messages = _mail_messages(email)
    if not messages:
        raise RuntimeError("OTP email was not captured")
    body = str(messages[-1].get("body", ""))
    match = re.search(r"password reset code is:\s*([A-Z]{4})", body, re.I)
    if not match:
        raise RuntimeError("OTP was not found in captured email")
    return match.group(1).upper()


def _verify_otp(case_id: str, group: str, label: str, email: str, otp: str) -> dict[str, Any]:
    response = requests.post(
        f"{BASE._api_base(group)}/auth/password/forgot/otp/verify",
        json={"email": email, "otp": otp},
        timeout=30,
    )
    raw_sha = BASE._record_http(
        case_id,
        group,
        label,
        {
            "method": "POST",
            "path": "/auth/password/forgot/otp/verify",
            "email": email,
            "otp_fingerprint": BASE._fingerprint(otp),
        },
        response,
    )
    body = response.json()
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "message": body.get("message"),
        "data": body.get("data"),
        "raw_sha256": raw_sha,
    }


def _reset_password(
    case_id: str,
    group: str,
    label: str,
    email: str,
    new_password: str,
    confirmation: str,
) -> dict[str, Any]:
    response = requests.post(
        f"{BASE._api_base(group)}/auth/password/reset",
        json={
            "email": email,
            "new_password": BASE._encrypt_password(group, new_password),
            "confirm_new_password": BASE._encrypt_password(group, confirmation),
        },
        timeout=45,
    )
    raw_sha = BASE._record_http(
        case_id,
        group,
        label,
        {
            "method": "POST",
            "path": "/auth/password/reset",
            "email": email,
            "new_password": new_password,
            "confirm_new_password": confirmation,
        },
        response,
    )
    body = response.json()
    auth = response.headers.get("Authorization")
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "message": body.get("message"),
        "data": body.get("data"),
        "authorization_present": bool(auth),
        "authorization_fingerprint": BASE._fingerprint(auth) if auth else None,
        "raw_sha256": raw_sha,
        "_auth": auth,
    }


def _group_email(stem: str, group: str) -> str:
    return f"{stem}-{group}@fresh.invalid"


def _prepare_otp_user(case_id: str, group: str, stem: str, password: str) -> str:
    email = _group_email(stem, group)
    if BASE._email_count(group, [email]) == 0:
        _ensure_user(case_id, group, email, f"{stem.replace('-', '')}{group.title()}", password)
    return email


def _send_fresh_otp(case_id: str, group: str, email: str, label_prefix: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    before_mail_count = len(_mail_messages(email))
    captcha = _captcha_request(case_id, group, f"{label_prefix}_captcha", email)
    sent = _send_otp(case_id, group, f"{label_prefix}_send", email, captcha["_captcha"])
    otp = _latest_mail_otp(email)
    sent["mail_count_delta"] = len(_mail_messages(email)) - before_mail_count
    return captcha, sent, otp


def run_at016() -> dict[str, Any]:
    case_id = "TC-AT-016"
    password = "ATReset-Test@123"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        email = _prepare_otp_user(case_id, group, "atreset", password)
        captcha = _captcha_request(case_id, group, "captcha", email)
        observed = {
            "http_status": captcha["http_status"],
            "jpeg_content_type": captcha["content_type"].lower().startswith("image/jpeg"),
            "captcha_length": captcha["captcha_length"],
            "captcha_ttl_in_range": 1 <= captcha["captcha_ttl"] <= 60,
        }
        recorder.add_group(
            group,
            "PASS" if captcha_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "request_real_captcha_and_read_cache",
                    "contract": observed,
                    "image_length": captcha["image_length"],
                    "captcha_ttl": captcha["captcha_ttl"],
                    "captcha_fingerprint": captcha["captcha_fingerprint"],
                    "raw_sha256": captcha["raw_sha256"],
                }
            ],
            oracle={"content_type": "image/jpeg", "captcha_length": 4, "ttl_max": 60},
        )
    return _finalize(recorder, case_id)


def run_at017() -> dict[str, Any]:
    case_id = "TC-AT-017"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        email = _group_email("atreset", group)
        client = _redis_client(group)
        captcha_value = client.get(_otp_keys(email)["captcha"])
        refreshed = None
        if not captcha_value:
            refreshed = _captcha_request(case_id, group, "refresh_captcha", email)
            captcha_value = refreshed["_captcha"]
        before_count = len(_mail_messages(email))
        sent = _send_otp(case_id, group, "send_otp", email, str(captcha_value))
        otp = _latest_mail_otp(email)
        mail_delta = len(_mail_messages(email)) - before_count
        state = _otp_state(group, email)
        observed = {
            "http_status": sent["http_status"],
            "code": sent["code"],
            "data": sent["data"],
            "stored_hash_format": state["stored_hash_format"],
            "otp_ttl_in_range": 1 <= state["otp_ttl"] <= 300,
            "last_sent_present": state["last_sent_present"],
            "mail_count": mail_delta,
            "mail_otp_length": len(otp),
        }
        recorder.add_group(
            group,
            "PASS" if otp_send_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "send_otp_using_cached_captcha",
                    "contract": observed,
                    "captcha_refreshed": refreshed is not None,
                    "mail_otp_fingerprint": BASE._fingerprint(otp),
                    "raw_sha256": sent["raw_sha256"],
                },
                {"name": "read_only_redis_otp_state", "state": state},
            ],
            oracle={"otp_hashed": True, "ttl_max": 300, "real_mail_delta": 1},
        )
    return _finalize(recorder, case_id)


def run_at018() -> dict[str, Any]:
    case_id = "TC-AT-018"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        email = _group_email("atreset", group)
        before = _otp_state(group, email)
        before_mail = len(_mail_messages(email))
        captcha = _captcha_request(case_id, group, "second_captcha", email)
        sent = _send_otp(case_id, group, "resend_within_cooldown", email, captcha["_captcha"])
        after = _otp_state(group, email)
        mail_delta = len(_mail_messages(email)) - before_mail
        ok = (
            sent["http_status"] == 200
            and sent["code"] == 10
            and sent["data"] is False
            and "wait" in str(sent["message"]).lower()
            and before["otp_value_fingerprint"] == after["otp_value_fingerprint"]
            and mail_delta == 0
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "request_new_captcha_then_resend_during_cooldown",
                    "http_status": sent["http_status"],
                    "code": sent["code"],
                    "message": sent["message"],
                    "mail_delta": mail_delta,
                    "raw_sha256": [captcha["raw_sha256"], sent["raw_sha256"]],
                },
                {"name": "read_only_original_otp_unchanged", "before": before, "after": after},
            ],
            oracle={"code": 10, "mail_delta": 0, "original_otp_unchanged": True},
        )
    return _finalize(recorder, case_id)


def run_at019() -> dict[str, Any]:
    case_id = "TC-AT-019"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        email = _group_email("atreset", group)
        otp = _latest_mail_otp(email)
        verified = _verify_otp(case_id, group, "verify_real_mail_otp", email, otp)
        state = _otp_state(group, email)
        ok = (
            verified["http_status"] == 200
            and verified["code"] == 0
            and verified["data"] is True
            and state["verified"] == "1"
            and 1 <= state["verified_ttl"] <= 300
            and not state["otp_present"]
            and state["attempts"] == 0
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "verify_otp_parsed_from_smtps_capture",
                    "http_status": verified["http_status"],
                    "code": verified["code"],
                    "otp_fingerprint": BASE._fingerprint(otp),
                    "raw_sha256": verified["raw_sha256"],
                },
                {"name": "read_only_verified_flag_and_consumption", "state": state},
            ],
            oracle={"verified": "1", "otp_consumed": True, "ttl_max": 300},
        )
    return _finalize(recorder, case_id)


def run_at020() -> dict[str, Any]:
    case_id = "TC-AT-020"
    password = "AT020-Test@123"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        email = _prepare_otp_user(case_id, group, "at020-lock", password)
        _captcha, sent, otp = _send_fresh_otp(case_id, group, email, "lock_fixture")
        wrong = "BBBB" if otp == "AAAA" else "AAAA"
        failures = [_verify_otp(case_id, group, f"wrong_attempt_{index}", email, wrong) for index in range(1, 6)]
        locked_state = _otp_state(group, email)
        sixth = _verify_otp(case_id, group, "correct_while_locked", email, otp)
        ok = (
            sent["code"] == 0
            and all(item["code"] == 109 for item in failures)
            and locked_state["attempts"] == 5
            and locked_state["lock_present"]
            and 1 <= locked_state["lock_ttl"] <= 1800
            and sixth["code"] == 10
            and "too many attempts" in str(sixth["message"]).lower()
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "send_fresh_otp_then_submit_five_wrong_values",
                    "send_code": sent["code"],
                    "failure_codes": [item["code"] for item in failures],
                    "raw_sha256": [item["raw_sha256"] for item in failures],
                },
                {"name": "read_only_lock_state", "state": locked_state},
                {"name": "reject_correct_otp_while_locked", "code": sixth["code"], "message": sixth["message"], "raw_sha256": sixth["raw_sha256"]},
            ],
            oracle={"attempts": 5, "lock_ttl_max": 1800, "locked_code": 10},
        )
    return _finalize(recorder, case_id)


def run_at021() -> dict[str, Any]:
    case_id = "TC-AT-021"
    old_password = "ATReset-Test@123"
    new_password = "ATReset-New@456"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        email = _group_email("atreset", group)
        before = _auth_snapshot(group, email)
        reset = _reset_password(case_id, group, "reset_password", email, new_password, new_password)
        after = _auth_snapshot(group, email)
        profile = _request(case_id, group, "profile_with_reset_token", "GET", "/users/me", auth=reset["_auth"])
        new_login = _login(case_id, group, "login_new_password", email, new_password)
        old_login = _login(case_id, group, "login_old_password", email, old_password)
        state = _otp_state(group, email)
        observed = {
            "http_status": reset["http_status"],
            "code": reset["code"],
            "authorization_present": reset["authorization_present"],
            "authorization_usable": profile["http_status"] == 200 and profile["code"] == 0,
            "password_hash_changed": before.get("password_hash_fingerprint") != after.get("password_hash_fingerprint"),
            "verified_key_absent": state["verified"] is None,
            "new_password_login_code": new_login["code"],
            "old_password_login_code": old_login["code"],
        }
        recorder.add_group(
            group,
            "PASS" if password_reset_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "reset_after_real_otp_verification",
                    "contract": observed,
                    "authorization_fingerprint": reset["authorization_fingerprint"],
                    "raw_sha256": [reset["raw_sha256"], profile["raw_sha256"]],
                },
                {"name": "read_only_hash_and_verified_state", "before": before, "after": after, "verified_state": state["verified"]},
                {"name": "login_new_and_old_passwords", "new_code": new_login["code"], "old_code": old_login["code"], "raw_sha256": [new_login["raw_sha256"], old_login["raw_sha256"]]},
            ],
            oracle={"reset_code": 0, "new_login": 0, "old_login": 109},
        )
    return _finalize(recorder, case_id)


def run_at022() -> dict[str, Any]:
    case_id = "TC-AT-022"
    password = "AT022-Test@123"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        email = _prepare_otp_user(case_id, group, "at022-mismatch", password)
        _captcha, sent, otp = _send_fresh_otp(case_id, group, email, "mismatch_fixture")
        verified = _verify_otp(case_id, group, "verify_fixture_otp", email, otp)
        before = _auth_snapshot(group, email)
        reset = _reset_password(case_id, group, "mismatched_passwords", email, "AT022-NewA@456", "AT022-NewB@789")
        after = _auth_snapshot(group, email)
        state = _otp_state(group, email)
        ok = (
            sent["code"] == 0
            and verified["code"] == 0
            and reset["http_status"] == 200
            and reset["code"] == 101
            and reset["message"] == "passwords do not match"
            and before.get("password_hash_fingerprint") == after.get("password_hash_fingerprint")
            and state["verified"] == "1"
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "establish_verified_state_via_real_otp",
                    "send_code": sent["code"],
                    "verify_code": verified["code"],
                    "otp_fingerprint": BASE._fingerprint(otp),
                    "raw_sha256": verified["raw_sha256"],
                },
                {"name": "reject_mismatched_new_passwords", "http_status": reset["http_status"], "code": reset["code"], "message": reset["message"], "raw_sha256": reset["raw_sha256"]},
                {"name": "read_only_hash_unchanged", "before": before, "after": after, "verified_retained": state["verified"] == "1"},
            ],
            oracle={"code": 101, "hash_unchanged": True},
        )
    return _finalize(recorder, case_id)


def run_at023() -> dict[str, Any]:
    case_id = "TC-AT-023"
    password = "AT023-Test@123"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        email = _prepare_otp_user(case_id, group, "at023-unverified", password)
        before = _auth_snapshot(group, email)
        state_before = _otp_state(group, email)
        reset = _reset_password(case_id, group, "reset_without_verification", email, "AT023-New@456", "AT023-New@456")
        after = _auth_snapshot(group, email)
        ok = (
            state_before["verified"] is None
            and reset["http_status"] == 200
            and reset["code"] == 109
            and reset["message"] == "email not verified"
            and before.get("password_hash_fingerprint") == after.get("password_hash_fingerprint")
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "confirm_no_verified_flag", "verified": state_before["verified"]},
                {"name": "reject_reset_without_otp", "http_status": reset["http_status"], "code": reset["code"], "message": reset["message"], "raw_sha256": reset["raw_sha256"]},
                {"name": "read_only_hash_unchanged", "before": before, "after": after},
            ],
            oracle={"code": 109, "message": "email not verified", "hash_unchanged": True},
        )
    return _finalize(recorder, case_id)


def _owner_tenant_id(group: str, email: str) -> str:
    connection, user_table, _namespace = BASE._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT id FROM {user_table} WHERE email=%s", (email,))
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("owner user is missing")
    return str(row[0])


def _api_token_rows(group: str, email: str) -> list[dict[str, Any]]:
    tenant_id = _owner_tenant_id(group, email)
    connection, _user_table, _namespace = BASE._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tenant_id,token,beta,create_time FROM api_token WHERE tenant_id=%s ORDER BY create_time,token",
                (tenant_id,),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "tenant_id": str(row[0]),
            "token": str(row[1]),
            "beta": str(row[2] or ""),
            "create_time": int(row[3]) if row[3] is not None else None,
        }
        for row in rows
    ]


def _create_api_token(case_id: str, group: str, label: str, jwt: str) -> dict[str, Any]:
    response = requests.post(
        f"{BASE._api_base(group)}/system/tokens",
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    raw_sha = BASE._record_http(
        case_id,
        group,
        label,
        {"method": "POST", "path": "/system/tokens", "Authorization": jwt},
        response,
    )
    body = response.json()
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "raw_sha256": raw_sha,
        "_token": str(data.get("token") or ""),
        "_beta": str(data.get("beta") or ""),
        "_tenant_id": str(data.get("tenant_id") or ""),
    }


def _delete_api_token(case_id: str, group: str, label: str, jwt: str, token: str) -> dict[str, Any]:
    encoded = requests.utils.quote(token, safe="")
    response = requests.delete(
        f"{BASE._api_base(group)}/system/tokens/{encoded}",
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    raw_sha = BASE._record_http(
        case_id,
        group,
        label,
        {
            "method": "DELETE",
            "path": "/system/tokens/<redacted>",
            "credential_fingerprint": BASE._fingerprint(token),
            "Authorization": jwt,
        },
        response,
    )
    body = response.json()
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "data": body.get("data"),
        "raw_sha256": raw_sha,
    }


def _credential_profile(case_id: str, group: str, label: str, credential: str) -> dict[str, Any]:
    return _request(case_id, group, label, "GET", "/users/me", auth=credential)


def _create_chat(case_id: str, group: str, jwt: str) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        "create_chat_fixture",
        "POST",
        "/chats",
        auth=jwt,
        payload={"name": f"AT028 Chat {group}"},
    )


def run_at024() -> dict[str, Any]:
    case_id = "TC-AT-024"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(case_id, group, "login", BASE_EMAIL, BASE_PASSWORD)
        created = _create_api_token(case_id, group, "create_api_token", login["_auth"])
        rows = _api_token_rows(group, BASE_EMAIL)
        matches = [row for row in rows if row["token"] == created["_token"]]
        observed = {
            "http_status": created["http_status"],
            "code": created["code"],
            "token_prefix": created["_token"].startswith("ragflow-"),
            "beta_length": len(created["_beta"]),
            "tenant_matches": created["_tenant_id"] == _owner_tenant_id(group, BASE_EMAIL),
            "database_count": len(matches),
            "database_values_match": len(matches) == 1 and matches[0]["beta"] == created["_beta"] and matches[0]["tenant_id"] == created["_tenant_id"],
        }
        recorder.add_group(
            group,
            "PASS" if api_token_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "create_token_via_api",
                    "contract": observed,
                    "credential_fingerprint": BASE._fingerprint(created["_token"]),
                    "beta_fingerprint": BASE._fingerprint(created["_beta"]),
                    "raw_sha256": [login["raw_sha256"], created["raw_sha256"]],
                },
                {"name": "read_only_composite_row_match", "tenant_id_fingerprint": BASE._fingerprint(created["_tenant_id"]), "matching_rows": len(matches)},
            ],
            oracle={"token_prefix": "ragflow-", "beta_length": 32, "database_count": 1},
        )
    return _finalize(recorder, case_id)


def run_at025() -> dict[str, Any]:
    case_id = "TC-AT-025"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(case_id, group, "login", BASE_EMAIL, BASE_PASSWORD)
        first = _create_api_token(case_id, group, "create_second_token", login["_auth"])
        second = _create_api_token(case_id, group, "create_third_token", login["_auth"])
        listed = _request(case_id, group, "list_tokens", "GET", "/system/tokens", auth=login["_auth"])
        api_rows = listed["data"] if isinstance(listed["data"], list) else []
        database = _api_token_rows(group, BASE_EMAIL)
        api_tokens = {str(item.get("token")) for item in api_rows if isinstance(item, dict)}
        db_tokens = {item["token"] for item in database}
        ok = (
            first["code"] == second["code"] == 0
            and listed["http_status"] == 200
            and listed["code"] == 0
            and len(api_rows) >= 3
            and api_tokens == db_tokens
            and all(len(str(item.get("beta") or "")) == 32 for item in api_rows if isinstance(item, dict))
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "create_two_additional_tokens",
                    "codes": [first["code"], second["code"]],
                    "credential_fingerprints": [BASE._fingerprint(first["_token"]), BASE._fingerprint(second["_token"])],
                    "raw_sha256": [first["raw_sha256"], second["raw_sha256"]],
                },
                {
                    "name": "list_and_compare_database_set",
                    "http_status": listed["http_status"],
                    "code": listed["code"],
                    "api_count": len(api_rows),
                    "database_count": len(database),
                    "sets_match": api_tokens == db_tokens,
                    "raw_sha256": listed["raw_sha256"],
                },
            ],
            oracle={"minimum_count": 3, "api_database_sets_match": True},
        )
    return _finalize(recorder, case_id)


def run_at026() -> dict[str, Any]:
    case_id = "TC-AT-026"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(case_id, group, "login", BASE_EMAIL, BASE_PASSWORD)
        before = _api_token_rows(group, BASE_EMAIL)
        target = before[-1]["token"]
        deleted = _delete_api_token(case_id, group, "delete_token", login["_auth"], target)
        after = _api_token_rows(group, BASE_EMAIL)
        remaining = sum(row["token"] == target for row in after)
        ok = deleted["http_status"] == 200 and deleted["code"] == 0 and deleted["data"] is True and remaining == 0 and len(after) == len(before) - 1
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "delete_selected_api_token",
                    "http_status": deleted["http_status"],
                    "code": deleted["code"],
                    "credential_fingerprint": BASE._fingerprint(target),
                    "raw_sha256": deleted["raw_sha256"],
                },
                {"name": "read_only_token_absence", "before_count": len(before), "after_count": len(after), "target_count": remaining},
            ],
            oracle={"target_count": 0, "count_delta": -1},
        )
    return _finalize(recorder, case_id)


def run_at027() -> dict[str, Any]:
    case_id = "TC-AT-027"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        rows = _api_token_rows(group, BASE_EMAIL)
        token = rows[0]["token"]
        profile = _credential_profile(case_id, group, "profile_with_api_token", token)
        data = profile["data"] if isinstance(profile["data"], dict) else {}
        ok = profile["http_status"] == 200 and profile["code"] == 0 and data.get("email") == BASE_EMAIL
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "authenticate_with_auth_api",
                    "http_status": profile["http_status"],
                    "code": profile["code"],
                    "email_matches": data.get("email") == BASE_EMAIL,
                    "credential_fingerprint": BASE._fingerprint(token),
                    "raw_sha256": profile["raw_sha256"],
                }
            ],
            oracle={"profile": [200, 0], "email": BASE_EMAIL},
        )
    return _finalize(recorder, case_id)


def run_at028() -> dict[str, Any]:
    case_id = "TC-AT-028"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(case_id, group, "login", BASE_EMAIL, BASE_PASSWORD)
        rows = _api_token_rows(group, BASE_EMAIL)
        beta = rows[0]["beta"]
        chat = _create_chat(case_id, group, login["_auth"])
        chat_data = chat["data"] if isinstance(chat["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        info = _request(case_id, group, "chatbot_info_with_beta", "GET", f"/chatbots/{chat_id}/info", auth=beta)
        ok = login["code"] == 0 and chat["code"] == 0 and bool(chat_id) and info["http_status"] == 200 and info["code"] == 0
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "create_chat_via_main_api", "code": chat["code"], "chat_id_fingerprint": BASE._fingerprint(chat_id), "raw_sha256": chat["raw_sha256"]},
                {"name": "access_beta_only_chatbot_info", "http_status": info["http_status"], "code": info["code"], "beta_fingerprint": BASE._fingerprint(beta), "raw_sha256": info["raw_sha256"]},
            ],
            oracle={"beta_only_endpoint": [200, 0]},
        )
    return _finalize(recorder, case_id)


def run_at029() -> dict[str, Any]:
    case_id = "TC-AT-029"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        beta = _api_token_rows(group, BASE_EMAIL)[0]["beta"]
        profile = _credential_profile(case_id, group, "profile_with_beta", beta)
        ok = profile["http_status"] == 401 and profile["code"] == 401
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "reject_beta_on_default_auth_endpoint",
                    "http_status": profile["http_status"],
                    "code": profile["code"],
                    "beta_fingerprint": BASE._fingerprint(beta),
                    "raw_sha256": profile["raw_sha256"],
                }
            ],
            oracle={"http_status": 401, "code": 401},
        )
    return _finalize(recorder, case_id)


class _HistoricalTimestampSigner(TimestampSigner):
    def get_timestamp(self) -> int:
        return int(time.time()) - 7200


def run_at030() -> dict[str, Any]:
    case_id = "TC-AT-030"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(case_id, group, "login", BASE_EMAIL, BASE_PASSWORD)
        raw_access = _raw_access_state(group, BASE_EMAIL)
        aged = Serializer(secret_key=_signing_secret(group), signer=_HistoricalTimestampSigner).dumps(raw_access)
        profile = _credential_profile(case_id, group, "profile_with_two_hour_timestamp", aged)
        normal_decode = Serializer(secret_key=_signing_secret(group)).loads(aged)
        ok = login["code"] == 0 and str(normal_decode) == raw_access and profile["http_status"] == 200 and profile["code"] == 0
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "use_valid_signed_token_with_historical_timestamp",
                    "timestamp_age_seconds": 7200,
                    "decoded_matches": str(normal_decode) == raw_access,
                    "http_status": profile["http_status"],
                    "code": profile["code"],
                    "credential_fingerprint": BASE._fingerprint(aged),
                    "raw_sha256": profile["raw_sha256"],
                }
            ],
            oracle={"current_behavior_without_max_age": [200, 0]},
        )
    return _finalize(recorder, case_id)


def run_at031() -> dict[str, Any]:
    case_id = "TC-AT-031"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        first = _login(case_id, group, "login_v1", BASE_EMAIL, BASE_PASSWORD)
        second = _login(case_id, group, "login_v2", BASE_EMAIL, BASE_PASSWORD)
        old = _credential_profile(case_id, group, "profile_v1", first["_auth"])
        new = _credential_profile(case_id, group, "profile_v2", second["_auth"])
        ok = (
            first["code"] == second["code"] == 0
            and first["authorization_fingerprint"] != second["authorization_fingerprint"]
            and old["http_status"] == 401
            and old["code"] == 401
            and new["http_status"] == 200
            and new["code"] == 0
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "login_twice_and_compare_tokens",
                    "distinct": first["authorization_fingerprint"] != second["authorization_fingerprint"],
                    "raw_sha256": [first["raw_sha256"], second["raw_sha256"]],
                },
                {"name": "old_rejected_new_accepted", "old": [old["http_status"], old["code"]], "new": [new["http_status"], new["code"]], "raw_sha256": [old["raw_sha256"], new["raw_sha256"]]},
            ],
            oracle={"old": [401, 401], "new": [200, 0]},
        )
    return _finalize(recorder, case_id)


def run_at032() -> dict[str, Any]:
    case_id = "TC-AT-032"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(case_id, group, "login", BASE_EMAIL, BASE_PASSWORD)
        created = _create_api_token(case_id, group, "create_ephemeral_token", login["_auth"])
        before = _credential_profile(case_id, group, "profile_before_delete", created["_token"])
        deleted = _delete_api_token(case_id, group, "delete_ephemeral_token", login["_auth"], created["_token"])
        after = _credential_profile(case_id, group, "profile_after_delete", created["_token"])
        count = sum(row["token"] == created["_token"] for row in _api_token_rows(group, BASE_EMAIL))
        ok = created["code"] == 0 and before["http_status"] == 200 and before["code"] == 0 and deleted["code"] == 0 and after["http_status"] == 401 and after["code"] == 401 and count == 0
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "create_use_delete_reuse_api_token",
                    "before": [before["http_status"], before["code"]],
                    "delete_code": deleted["code"],
                    "after": [after["http_status"], after["code"]],
                    "credential_fingerprint": BASE._fingerprint(created["_token"]),
                    "raw_sha256": [created["raw_sha256"], before["raw_sha256"], deleted["raw_sha256"], after["raw_sha256"]],
                },
                {"name": "read_only_token_absent", "count": count},
            ],
            oracle={"before": [200, 0], "after": [401, 401], "database_count": 0},
        )
    return _finalize(recorder, case_id)


def run_at033() -> dict[str, Any]:
    case_id = "TC-AT-033"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        session = requests.Session()
        login = _login(case_id, group, "session_login", BASE_EMAIL, BASE_PASSWORD, session=session)
        profile = _request(case_id, group, "cookie_only_profile", "GET", "/users/me", session=session)
        data = profile["data"] if isinstance(profile["data"], dict) else {}
        observed = {
            "login_code": login["code"],
            "session_cookie_present": login["session_cookie_present"],
            "profile_http_status": profile["http_status"],
            "profile_code": profile["code"],
            "profile_email_matches": data.get("email") == BASE_EMAIL,
        }
        recorder.add_group(
            group,
            "PASS" if session_auth_contract_ok(observed) else "FAIL",
            [{"name": "login_then_use_cookie_without_authorization", "contract": observed, "cookie_count": len(session.cookies), "raw_sha256": [login["raw_sha256"], profile["raw_sha256"]]}],
            oracle={"cookie_only_profile": [200, 0]},
        )
    return _finalize(recorder, case_id)


def run_at034() -> dict[str, Any]:
    case_id = "TC-AT-034"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        session = requests.Session()
        login = _login(case_id, group, "session_login", BASE_EMAIL, BASE_PASSWORD, session=session)
        cookies = list(session.cookies)
        fresh_browser = requests.Session()
        no_cookie = _request(case_id, group, "profile_after_browser_close", "GET", "/users/me", session=fresh_browser)
        tampered_session = requests.Session()
        for cookie in cookies:
            value = cookie.value
            tampered = value[:-1] + ("A" if value[-1:] != "A" else "B")
            tampered_session.cookies.set(cookie.name, tampered, domain=cookie.domain, path=cookie.path)
        tampered_response = _request(case_id, group, "profile_with_tampered_cookie", "GET", "/users/me", session=tampered_session)
        ok = login["code"] == 0 and bool(cookies) and no_cookie["http_status"] == 401 and no_cookie["code"] == 401 and tampered_response["http_status"] == 401 and tampered_response["code"] == 401
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "simulate_browser_close_without_cookie",
                    "cookie_count": len(cookies),
                    "cookie_fingerprints": [BASE._fingerprint(c.value) for c in cookies],
                    "response": [no_cookie["http_status"], no_cookie["code"]],
                    "raw_sha256": no_cookie["raw_sha256"],
                },
                {"name": "reject_tampered_signed_cookie", "response": [tampered_response["http_status"], tampered_response["code"]], "raw_sha256": tampered_response["raw_sha256"]},
            ],
            oracle={"no_cookie": [401, 401], "tampered_cookie": [401, 401]},
        )
    return _finalize(recorder, case_id)


def run_at035() -> dict[str, Any]:
    case_id = "TC-AT-035"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        session = requests.Session()
        login = _login(case_id, group, "session_login", BASE_EMAIL, BASE_PASSWORD, session=session)
        logout = _request(case_id, group, "logout_from_separate_client", "POST", "/auth/logout", auth=login["_auth"])
        profile = _request(case_id, group, "stale_session_profile", "GET", "/users/me", session=session)
        state = _auth_snapshot(group, BASE_EMAIL)
        ok = login["code"] == 0 and logout["code"] == 0 and state.get("invalid_prefix") is True and profile["http_status"] == 401 and profile["code"] == 401
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "preserve_original_session_while_other_client_logs_out", "logout_code": logout["code"], "invalid_prefix": state.get("invalid_prefix"), "raw_sha256": logout["raw_sha256"]},
                {"name": "reject_stale_session_by_database_state", "http_status": profile["http_status"], "code": profile["code"], "raw_sha256": profile["raw_sha256"]},
            ],
            oracle={"invalid_prefix": True, "stale_session": [401, 401]},
        )
    return _finalize(recorder, case_id)


def run_at036() -> dict[str, Any]:
    case_id = "TC-AT-036"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        barrier = threading.Barrier(2)

        def do_login(label: str):
            barrier.wait(timeout=10)
            return _login(case_id, group, label, BASE_EMAIL, BASE_PASSWORD)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(do_login, f"concurrent_login_{index}") for index in (1, 2)]
            logins = [future.result(timeout=60) for future in futures]
        profiles = [_credential_profile(case_id, group, f"profile_concurrent_{index}", item["_auth"]) for index, item in enumerate(logins, start=1)]
        statuses = sorted((item["http_status"], item["code"]) for item in profiles)
        decoded_current = [_decoded_token_matches(group, item["_auth"], BASE_EMAIL) for item in logins]
        ok = all(item["code"] == 0 for item in logins) and statuses == [(200, 0), (401, 401)] and sum(decoded_current) == 1
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "release_two_password_logins_concurrently",
                    "login_codes": [item["code"] for item in logins],
                    "credential_fingerprints": [item["authorization_fingerprint"] for item in logins],
                    "raw_sha256": [item["raw_sha256"] for item in logins],
                },
                {"name": "exactly_one_token_matches_last_write", "profile_statuses": statuses, "decoded_current_flags": decoded_current, "raw_sha256": [item["raw_sha256"] for item in profiles]},
            ],
            oracle={"successful_logins": 2, "valid_tokens_after_race": 1},
        )
    return _finalize(recorder, case_id)


def run_at037() -> dict[str, Any]:
    case_id = "TC-AT-037"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(case_id, group, "login", BASE_EMAIL, BASE_PASSWORD)
        created = [_create_api_token(case_id, group, f"create_parallel_token_{index}", login["_auth"]) for index in range(1, 4)]
        tokens = [item["_token"] for item in created]
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(_credential_profile, case_id, group, f"parallel_profile_{index}", token) for index, token in enumerate(tokens, start=1)]
            first_profiles = [future.result(timeout=30) for future in futures]
        deleted = _delete_api_token(case_id, group, "delete_middle_token", login["_auth"], tokens[1])
        after_profiles = [_credential_profile(case_id, group, f"profile_after_delete_{index}", token) for index, token in enumerate(tokens, start=1)]
        first_statuses = [(item["http_status"], item["code"]) for item in first_profiles]
        after_statuses = [(item["http_status"], item["code"]) for item in after_profiles]
        ok = all(item["code"] == 0 for item in created) and first_statuses == [(200, 0)] * 3 and deleted["code"] == 0 and after_statuses == [(200, 0), (401, 401), (200, 0)]
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "use_three_api_tokens_concurrently",
                    "create_codes": [item["code"] for item in created],
                    "profile_statuses": first_statuses,
                    "credential_fingerprints": [BASE._fingerprint(token) for token in tokens],
                    "raw_sha256": [item["raw_sha256"] for item in first_profiles],
                },
                {
                    "name": "delete_one_without_affecting_others",
                    "delete_code": deleted["code"],
                    "profile_statuses": after_statuses,
                    "raw_sha256": [deleted["raw_sha256"]] + [item["raw_sha256"] for item in after_profiles],
                },
            ],
            oracle={"before": [[200, 0]] * 3, "after": [[200, 0], [401, 401], [200, 0]]},
        )
    return _finalize(recorder, case_id)


def _request_with_authorization_header(case_id: str, group: str, label: str, authorization: str) -> dict[str, Any]:
    response = requests.get(
        f"{BASE._api_base(group)}/users/me",
        headers={"Authorization": authorization},
        timeout=30,
    )
    raw_sha = BASE._record_http(
        case_id,
        group,
        label,
        {"method": "GET", "path": "/users/me", "Authorization": authorization},
        response,
    )
    body = response.json()
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "data": body.get("data"),
        "raw_sha256": raw_sha,
    }


def _raw_password_hash(group: str, email: str) -> str:
    connection, user_table, _namespace = BASE._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT password FROM {user_table} WHERE email=%s", (email,))
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("password fixture missing")
    return str(row[0] or "")


def _invalid_access_row_exists(group: str, email: str) -> bool:
    connection, user_table, _namespace = BASE._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) FROM {user_table} WHERE email=%s AND access_token LIKE %s",
                (email, "INVALID_%"),
            )
            return int(cursor.fetchone()[0]) == 1
    finally:
        connection.close()


def _user_service_filter_probe(group: str, email: str) -> dict[str, Any]:
    import subprocess

    manager = _load_module("fresh_service_manager.py", "fresh_03_probe_manager")
    script = r"""
import json, sys
from api.db.services.user_service import UserService
email = sys.argv[1]
user = UserService.query(email=email)[0]
values = {
    "empty_count": len(list(UserService.query(access_token=""))),
    "none_count": len(list(UserService.query(access_token=None))),
    "space_count": len(list(UserService.query(access_token="   "))),
    "short_count": len(list(UserService.query(access_token="short"))),
    "invalid_prefix_count": len(list(UserService.query(access_token=str(user.access_token)))),
    "normal_query_executed": len(list(UserService.query(access_token="a" * 32))) >= 0,
}
print("__FRESH_RESULT__" + json.dumps(values, sort_keys=True))
"""
    completed = subprocess.run(
        [str(manager.PYTHON), "-c", script, email],
        cwd=manager.PROJECT_ROOT,
        env=manager.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    marker = next(line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__"))
    return json.loads(marker)


def run_at038() -> dict[str, Any]:
    case_id = "TC-AT-038"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        response = _credential_profile(case_id, group, "invalid_token", "not_a_valid_token_at_all")
        ok = response["http_status"] == 401 and response["code"] == 401
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "reject_completely_invalid_credential",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "credential_fingerprint": BASE._fingerprint("not_a_valid_token_at_all"),
                    "raw_sha256": response["raw_sha256"],
                }
            ],
            oracle={"http_status": 401, "code": 401},
        )
    return _finalize(recorder, case_id)


def run_at039() -> dict[str, Any]:
    case_id = "TC-AT-039"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(case_id, group, "login", BASE_EMAIL, BASE_PASSWORD)
        values = [f"Bearer {login['_auth']}", str(login["_auth"]), f"bEaReR {login['_auth']}"]
        responses = [_request_with_authorization_header(case_id, group, f"authorization_format_{index}", value) for index, value in enumerate(values, start=1)]
        statuses = [(item["http_status"], item["code"]) for item in responses]
        ok = login["code"] == 0 and statuses == [(200, 0)] * 3
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "bearer_raw_and_mixed_case_formats",
                    "statuses": statuses,
                    "credential_fingerprint": login["authorization_fingerprint"],
                    "raw_sha256": [item["raw_sha256"] for item in responses],
                }
            ],
            oracle={"all_formats": [[200, 0]] * 3},
        )
    return _finalize(recorder, case_id)


def run_at040() -> dict[str, Any]:
    case_id = "TC-AT-040"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        response = _request_with_authorization_header(case_id, group, "empty_authorization", "")
        ok = response["http_status"] == 401 and response["code"] == 401
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [{"name": "reject_empty_authorization_header", "http_status": response["http_status"], "code": response["code"], "raw_sha256": response["raw_sha256"]}],
            oracle={"http_status": 401, "code": 401},
        )
    return _finalize(recorder, case_id)


def run_at041() -> dict[str, Any]:
    case_id = "TC-AT-041"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        first = _login(case_id, group, "login_before_logout", BASE_EMAIL, BASE_PASSWORD)
        logout = _request(case_id, group, "logout", "POST", "/auth/logout", auth=first["_auth"])
        old = _credential_profile(case_id, group, "replay_logged_out_token", first["_auth"])
        second = _login(case_id, group, "password_reauthentication", BASE_EMAIL, BASE_PASSWORD)
        new = _credential_profile(case_id, group, "profile_new_token", second["_auth"])
        ok = (
            first["code"] == 0
            and logout["code"] == 0
            and old["http_status"] == 401
            and old["code"] == 401
            and second["code"] == 0
            and new["http_status"] == 200
            and new["code"] == 0
            and first["authorization_fingerprint"] != second["authorization_fingerprint"]
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "logout_and_replay_old_token",
                    "logout_code": logout["code"],
                    "old_status": [old["http_status"], old["code"]],
                    "raw_sha256": [first["raw_sha256"], logout["raw_sha256"], old["raw_sha256"]],
                },
                {
                    "name": "reauthenticate_with_password_and_use_new_token",
                    "login_code": second["code"],
                    "new_status": [new["http_status"], new["code"]],
                    "tokens_distinct": first["authorization_fingerprint"] != second["authorization_fingerprint"],
                    "raw_sha256": [second["raw_sha256"], new["raw_sha256"]],
                },
            ],
            oracle={"old": [401, 401], "new": [200, 0]},
        )
    return _finalize(recorder, case_id)


def run_at042() -> dict[str, Any]:
    case_id = "TC-AT-042"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(case_id, group, "login", BASE_EMAIL, BASE_PASSWORD)
        token = str(login["_auth"])
        index = max(1, len(token) // 2)
        tampered = token[:index] + ("A" if token[index] != "A" else "B") + token[index + 1 :]
        response = _credential_profile(case_id, group, "tampered_token", tampered)
        ok = login["code"] == 0 and tampered != token and response["http_status"] == 401 and response["code"] == 401
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "tamper_signed_token_and_reject",
                    "changed_index": index,
                    "original_fingerprint": login["authorization_fingerprint"],
                    "tampered_fingerprint": BASE._fingerprint(tampered),
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "raw_sha256": response["raw_sha256"],
                }
            ],
            oracle={"http_status": 401, "code": 401},
        )
    return _finalize(recorder, case_id)


def run_at043() -> dict[str, Any]:
    case_id = "TC-AT-043"
    user_b_email = "at043-userb@fresh.invalid"
    user_b_password = "AT043-UserB@123"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        _ensure_user(case_id, group, user_b_email, "AT043UserB", user_b_password)
        user_b_login = _login(case_id, group, "user_b_login", user_b_email, user_b_password)
        token_a = _api_token_rows(group, BASE_EMAIL)[0]["token"]
        profile = _credential_profile(case_id, group, "user_a_profile_via_shared_api_token", token_a)
        data = profile["data"] if isinstance(profile["data"], dict) else {}
        ok = user_b_login["code"] == 0 and profile["http_status"] == 200 and profile["code"] == 0 and data.get("email") == BASE_EMAIL and data.get("email") != user_b_email
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "establish_distinct_user_b",
                    "login_code": user_b_login["code"],
                    "user_b_id_fingerprint": _auth_snapshot(group, user_b_email).get("user_id_fingerprint"),
                    "raw_sha256": user_b_login["raw_sha256"],
                },
                {
                    "name": "use_user_a_api_token_while_acting_as_holder_b",
                    "http_status": profile["http_status"],
                    "code": profile["code"],
                    "returned_user_a": data.get("email") == BASE_EMAIL,
                    "credential_fingerprint": BASE._fingerprint(token_a),
                    "raw_sha256": profile["raw_sha256"],
                },
            ],
            oracle={"api_token_identity": BASE_EMAIL},
        )
    return _finalize(recorder, case_id)


def run_at044() -> dict[str, Any]:
    import base64
    from Cryptodome.Cipher import PKCS1_v1_5
    from Cryptodome.PublicKey import RSA
    from itsdangerous import BadSignature
    from werkzeug.security import check_password_hash

    case_id = "TC-AT-044"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        encrypted = BASE._encrypt_password(group, BASE_PASSWORD)
        key = RSA.import_key((RUNTIME_DIR / group / "conf" / "private.pem").read_text(encoding="utf-8"), "Welcome")
        decrypted = PKCS1_v1_5.new(key).decrypt(base64.b64decode(encrypted), b"failure").decode("utf-8")
        expected_base64 = base64.b64encode(BASE_PASSWORD.encode("utf-8")).decode("ascii")
        stored_hash = _raw_password_hash(group, BASE_EMAIL)
        access = _raw_access_state(group, BASE_EMAIL)
        serializer = Serializer(secret_key=_signing_secret(group))
        signed = serializer.dumps(access)
        signed_roundtrip = str(serializer.loads(signed)) == access
        tampered = signed[:-1] + ("A" if signed[-1] != "A" else "B")
        try:
            serializer.loads(tampered)
            tampered_rejected = False
        except BadSignature:
            tampered_rejected = True
        observed = {
            "rsa_roundtrip_to_base64": decrypted == expected_base64,
            "decrypted_is_not_plaintext": decrypted != BASE_PASSWORD,
            "hash_accepts_base64": check_password_hash(stored_hash, expected_base64),
            "hash_rejects_plaintext": not check_password_hash(stored_hash, BASE_PASSWORD),
            "signed_access_roundtrip": signed_roundtrip,
            "tampered_signature_rejected": tampered_rejected,
        }
        recorder.add_group(
            group,
            "PASS" if encryption_chain_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "rsa_public_private_roundtrip",
                    "contract": {k: observed[k] for k in ("rsa_roundtrip_to_base64", "decrypted_is_not_plaintext")},
                    "ciphertext_fingerprint": BASE._fingerprint(encrypted),
                    "decrypted_fingerprint": BASE._fingerprint(decrypted),
                },
                {
                    "name": "stored_hash_uses_base64_password",
                    "hash_length": len(stored_hash),
                    "hash_scheme": stored_hash.split(":", 1)[0],
                    "accepts_base64": observed["hash_accepts_base64"],
                    "rejects_plaintext": observed["hash_rejects_plaintext"],
                },
                {
                    "name": "authorization_uses_signed_access_state",
                    "signed_access_roundtrip": signed_roundtrip,
                    "tampered_signature_rejected": tampered_rejected,
                    "credential_fingerprint": BASE._fingerprint(signed),
                },
            ],
            oracle={"all_chain_checks": True},
        )
    return _finalize(recorder, case_id)


def run_at045() -> dict[str, Any]:
    import base64
    from werkzeug.security import check_password_hash

    case_id = "TC-AT-045"
    email = "at045-special@fresh.invalid"
    password = "P@$$w0rd!#%^&*()"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        BASE._delete_user_via_admin(group, email)
        registration = BASE._register(case_id, group, "register_special_password", {"email": email, "nickname": "AT045Special", "password": password})
        login = _login(case_id, group, "login_special_password", email, password)
        stored = _raw_password_hash(group, email)
        encoded = base64.b64encode(password.encode("utf-8")).decode("ascii")
        ok = registration["code"] == 0 and login["code"] == 0 and check_password_hash(stored, encoded) and not check_password_hash(stored, password)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "register_and_login_special_character_password",
                    "registration_code": registration["code"],
                    "login_code": login["code"],
                    "raw_sha256": [registration["raw_sha256"], login["raw_sha256"]],
                },
                {
                    "name": "read_only_hash_validation",
                    "hash_length": len(stored),
                    "hash_scheme": stored.split(":", 1)[0],
                    "base64_matches": check_password_hash(stored, encoded),
                    "plaintext_rejected": not check_password_hash(stored, password),
                },
            ],
            oracle={"registration_login": [0, 0], "base64_hash": True},
        )
    return _finalize(recorder, case_id)


def run_at046() -> dict[str, Any]:
    import base64
    from werkzeug.security import check_password_hash, generate_password_hash

    case_id = "TC-AT-046"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        stored = _raw_password_hash(group, BASE_EMAIL)
        encoded = base64.b64encode(BASE_PASSWORD.encode("utf-8")).decode("ascii")
        generated_a = generate_password_hash(encoded)
        generated_b = generate_password_hash(encoded)
        scheme = generated_a.split(":", 1)[0].split("$", 1)[0]
        ok = (
            scheme == "scrypt"
            and len(stored) <= 255
            and check_password_hash(stored, encoded)
            and check_password_hash(generated_a, encoded)
            and check_password_hash(generated_b, encoded)
            and generated_a != generated_b
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "runtime_hash_probe",
                    "scheme": scheme,
                    "stored_length": len(stored),
                    "generated_lengths": [len(generated_a), len(generated_b)],
                    "salts_distinct": generated_a != generated_b,
                    "all_verify": check_password_hash(stored, encoded) and check_password_hash(generated_a, encoded) and check_password_hash(generated_b, encoded),
                    "hash_fingerprints": [BASE._fingerprint(stored), BASE._fingerprint(generated_a), BASE._fingerprint(generated_b)],
                }
            ],
            oracle={"scheme": "scrypt", "maximum_field_length": 255, "random_salts": True},
        )
    return _finalize(recorder, case_id)


def run_at047() -> dict[str, Any]:
    case_id = "TC-AT-047"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(case_id, group, "login", BASE_EMAIL, BASE_PASSWORD)
        logout = _request(case_id, group, "logout", "POST", "/auth/logout", auth=login["_auth"])
        invalid_state = _raw_access_state(group, BASE_EMAIL)
        signed_invalid = Serializer(secret_key=_signing_secret(group)).dumps(invalid_state)
        api_rejection = _credential_profile(case_id, group, "signed_invalid_profile", signed_invalid)
        probe = _user_service_filter_probe(group, BASE_EMAIL)
        observed = {**probe, "invalid_row_exists": _invalid_access_row_exists(group, BASE_EMAIL)}
        ok = logout["code"] == 0 and invalid_state.startswith("INVALID_") and api_rejection["http_status"] == 401 and api_rejection["code"] == 401 and user_query_filter_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "create_real_invalid_access_state_via_logout", "logout_code": logout["code"], "invalid_prefix": invalid_state.startswith("INVALID_"), "raw_sha256": logout["raw_sha256"]},
                {"name": "isolated_user_service_query_probe", "contract": observed},
                {
                    "name": "reject_correctly_signed_invalid_access_state",
                    "http_status": api_rejection["http_status"],
                    "code": api_rejection["code"],
                    "credential_fingerprint": BASE._fingerprint(signed_invalid),
                    "raw_sha256": api_rejection["raw_sha256"],
                },
            ],
            oracle={"all_invalid_query_counts": 0, "signed_invalid": [401, 401]},
        )
    return _finalize(recorder, case_id)


def run_at048() -> dict[str, Any]:
    case_id = "TC-AT-048"
    password = "AT048-Test@123"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        password_email = f"at048-password-{group}@fresh.invalid"
        oauth_email = f"at048-oauth-{group}@fresh.invalid"
        for email in (password_email, oauth_email):
            BASE._delete_user_via_admin(group, email)
        port = _free_loopback_port()
        root, environment = _prepare_isolated_runtime(
            case_id,
            group,
            port,
            environment_overrides={"REGISTER_ENABLED": "0"},
        )
        base = f"http://127.0.0.1:{port}/api/v1"
        process = None
        log_path = None
        ready_seconds = None
        stopped = False
        try:
            process, log_path, ready_seconds = _launch_isolated_api(case_id, group, environment, port)
            config_response = _isolated_request(case_id, group, "disabled_config", base, "GET", "/system/config")
            password_response = _isolated_request(
                case_id,
                group,
                "disabled_password_registration",
                base,
                "POST",
                "/users",
                payload={
                    "email": password_email,
                    "nickname": "AT048Password",
                    "password": BASE._encrypt_password(group, password),
                },
            )
            password_graph = _registration_graph_snapshot(group, password_email)

            oauth_session = requests.Session()
            oauth_start = _oauth_start(
                case_id,
                group,
                "disabled_oauth_start",
                oauth_session,
                api_base=base,
            )
            oauth_code = _issue_oauth_code(
                {
                    "email": oauth_email,
                    "username": f"at048{group}",
                    "nickname": "AT048OAuth",
                    "avatar_url": "",
                }
            )
            oauth_callback = _oauth_callback(
                case_id,
                group,
                "disabled_oauth_callback",
                oauth_session,
                oauth_code,
                oauth_start["_state"],
                api_base=base,
            )
            oauth_graph = _registration_graph_snapshot(group, oauth_email)
        finally:
            stopped = _stop_isolated_api(process)

        oauth_cleanup = BASE._delete_user_via_admin(group, oauth_email)
        password_cleanup = BASE._delete_user_via_admin(group, password_email)
        config_data = config_response["data"] if isinstance(config_response["data"], dict) else {}
        observed = {
            "config_http_status": config_response["http_status"],
            "config_code": config_response["code"],
            "register_enabled": config_data.get("registerEnabled"),
            "password_http_status": password_response["http_status"],
            "password_code": password_response["code"],
            "password_graph_count": password_graph["graph_count"],
            "oauth_http_status": oauth_callback["http_status"],
            "oauth_error_present": oauth_callback["error_present"],
            "oauth_auth_absent": not oauth_callback["auth_query_present"],
            "oauth_graph_count": oauth_graph["graph_count"],
            "isolated_process_stopped": stopped,
            "main_api_healthy": _main_api_healthy(group),
        }
        recorder.add_group(
            group,
            "PASS" if registration_disabled_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "start_isolated_registration_disabled_api",
                    "port": port,
                    "runtime_root": str(root.relative_to(PROJECT_ROOT)),
                    "ready_seconds": round(float(ready_seconds), 3),
                    "config": {
                        "http_status": config_response["http_status"],
                        "code": config_response["code"],
                        "register_enabled": config_data.get("registerEnabled"),
                    },
                    "private_log": _private_log_summary(log_path),
                    "raw_sha256": config_response["raw_sha256"],
                },
                {
                    "name": "password_registration_is_rejected",
                    "http_status": password_response["http_status"],
                    "code": password_response["code"],
                    "graph": password_graph,
                    "raw_sha256": password_response["raw_sha256"],
                },
                {
                    "name": "oauth_registration_must_obey_same_switch",
                    "start_http_status": oauth_start["http_status"],
                    "callback_http_status": oauth_callback["http_status"],
                    "auth_query_present": oauth_callback["auth_query_present"],
                    "error_present": oauth_callback["error_present"],
                    "graph": oauth_graph,
                    "raw_sha256": [
                        oauth_start["raw_sha256"],
                        oauth_callback["raw_sha256"],
                    ],
                },
                {
                    "name": "stop_and_cleanup",
                    "isolated_process_stopped": stopped,
                    "oauth_admin_cleanup": oauth_cleanup,
                    "password_admin_cleanup": password_cleanup,
                    "main_api_healthy": observed["main_api_healthy"],
                },
            ],
            oracle={
                "register_enabled": 0,
                "password_code": 103,
                "oauth_new_user_rejected": True,
                "no_registration_graph": True,
            },
        )
    return _finalize(recorder, case_id)


def run_at049() -> dict[str, Any]:
    case_id = "TC-AT-049"
    password = "AT049-Test@123"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        email = f"at049-{group}@fresh.invalid"
        BASE._delete_user_via_admin(group, email)
        _ensure_user(case_id, group, email, "AT049Active", password)
        login = _login(case_id, group, "login_before_disable", email, password)
        port = _free_loopback_port()
        root, environment = _prepare_isolated_runtime(case_id, group, port)
        process = None
        log_path = None
        ready_seconds = None
        disabled = {"code": None, "raw_sha256": None}
        reactivated = {"code": None, "raw_sha256": None}
        route = {"http_status": None, "code": None, "raw_sha256": None}
        stopped = False
        try:
            process, log_path, ready_seconds = _launch_isolated_api(
                case_id,
                group,
                environment,
                port,
                active_required_server=True,
            )
            encoded = requests.utils.quote(email, safe="")
            disabled = BASE._admin_action(
                case_id,
                group,
                "disable_after_login",
                "PUT",
                f"/users/{encoded}/activate",
                {"activate_status": "off"},
            )
            route = _isolated_request(
                case_id,
                group,
                "active_required_route",
                f"http://127.0.0.1:{port}",
                "GET",
                "/__test__/active-required",
                auth=login["_auth"],
            )
        finally:
            encoded = requests.utils.quote(email, safe="")
            reactivated = BASE._admin_action(
                case_id,
                group,
                "reactivate_fixture",
                "PUT",
                f"/users/{encoded}/activate",
                {"activate_status": "on"},
            )
            stopped = _stop_isolated_api(process)

        main_healthy = _main_api_healthy(group)
        cleanup = BASE._delete_user_via_admin(group, email)
        observed = {
            "login_code": login["code"],
            "disable_code": disabled["code"],
            "route_http_status": route["http_status"],
            "route_code": route["code"],
            "reactivate_code": reactivated["code"],
            "isolated_process_stopped": stopped,
            "main_api_healthy": main_healthy,
        }
        recorder.add_group(
            group,
            "PASS" if active_required_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "start_test_only_decorator_route",
                    "port": port,
                    "runtime_root": str(root.relative_to(PROJECT_ROOT)),
                    "ready_seconds": round(float(ready_seconds), 3),
                    "private_log": _private_log_summary(log_path),
                },
                {
                    "name": "login_then_disable_via_admin",
                    "login_code": login["code"],
                    "disable_code": disabled["code"],
                    "raw_sha256": [login["raw_sha256"], disabled["raw_sha256"]],
                },
                {
                    "name": "invoke_login_required_plus_active_required",
                    "http_status": route["http_status"],
                    "code": route["code"],
                    "raw_sha256": route["raw_sha256"],
                },
                {
                    "name": "reactivate_stop_and_cleanup",
                    "reactivate_code": reactivated["code"],
                    "isolated_process_stopped": stopped,
                    "main_api_healthy": main_healthy,
                    "admin_cleanup": cleanup,
                    "raw_sha256": reactivated["raw_sha256"],
                },
            ],
            oracle={"http_status": 200, "code": 403, "reactivated": True},
        )
    return _finalize(recorder, case_id)


def run_at050() -> dict[str, Any]:
    case_id = "TC-AT-050"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login_before_fault = _login(case_id, group, "login_before_fault", BASE_EMAIL, BASE_PASSWORD)
        down = None
        restored = None
        try:
            down = _proxy_configure(group, "down", reset=True)
            fault_login = _metadata_fault_request(
                case_id,
                group,
                "login_during_metadata_fault",
                "POST",
                "/auth/login",
                payload={
                    "email": BASE_EMAIL,
                    "password": BASE._encrypt_password(group, BASE_PASSWORD),
                },
            )
            fault_profile = _metadata_fault_request(
                case_id,
                group,
                "profile_during_metadata_fault",
                "GET",
                "/users/me",
                auth=login_before_fault["_auth"],
            )
        finally:
            restored = _proxy_configure(group, "normal", reset=True)

        recovery = None
        recovery_attempts = 0
        for attempt in range(1, 5):
            recovery_attempts = attempt
            try:
                candidate = _login(
                    case_id,
                    group,
                    f"recovery_login_{attempt}",
                    BASE_EMAIL,
                    BASE_PASSWORD,
                )
            except requests.RequestException:
                candidate = None
            if candidate is not None and candidate["code"] == 0:
                recovery = candidate
                break
            time.sleep(1.0)
        observed = {
            "proxy_down_confirmed": down is not None and down["mode"] == "down",
            "login_http_status": fault_login["http_status"],
            "login_failed_finitely": fault_login["failed_finitely"],
            "profile_http_status": fault_profile["http_status"],
            "profile_failed_finitely": fault_profile["failed_finitely"],
            "proxy_restored": restored is not None and restored["mode"] == "normal",
            "recovery_login_code": recovery["code"] if recovery else None,
            "main_api_healthy": _main_api_healthy(group),
        }
        recorder.add_group(
            group,
            "PASS" if metadata_fault_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "establish_token_and_cut_metadata_proxy",
                    "initial_login_code": login_before_fault["code"],
                    "proxy": down,
                    "raw_sha256": login_before_fault["raw_sha256"],
                },
                {
                    "name": "login_fails_finitely_during_fault",
                    **fault_login,
                },
                {
                    "name": "authenticated_profile_fails_finitely_during_fault",
                    **fault_profile,
                },
                {
                    "name": "restore_proxy_and_recover",
                    "proxy": restored,
                    "recovery_attempts": recovery_attempts,
                    "recovery_login_code": recovery["code"] if recovery else None,
                    "main_api_healthy": observed["main_api_healthy"],
                    "raw_sha256": recovery["raw_sha256"] if recovery else None,
                },
            ],
            oracle={
                "fault_login_http_status": 500,
                "fault_profile_http_status": 500,
                "finite_failure": True,
                "recovery_login_code": 0,
            },
        )
    return _finalize(recorder, case_id)


def run_at051() -> dict[str, Any]:
    case_id = "TC-AT-051"
    password = "AT051-Test@123"
    restored_nickname = "AT051Restored"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        email = f"at051-{group}@fresh.invalid"
        BASE._delete_user_via_admin(group, email)
        _ensure_user(case_id, group, email, "AT051Original", password)
        physical = _set_empty_auth_fields(group, email)
        service_probe = _empty_string_service_probe(group, email)
        login = _login(case_id, group, "login_restores_access", email, password)
        patch = _request(
            case_id,
            group,
            "restore_nickname_via_api",
            "PATCH",
            "/users/me",
            auth=login["_auth"],
            payload={"nickname": restored_nickname},
        )
        restored_fields = _read_user_auth_fields(group, email)
        observed = {
            **physical,
            "user_service_empty_count": service_probe["user_service_empty_count"],
            "orm_nickname": service_probe["orm_nickname"],
            "login_restored": login["code"] == 0 and restored_fields.get("access_length") == 32,
            "nickname_restored": patch["code"] == 0 and restored_fields.get("nickname") == restored_nickname,
        }
        cleanup = BASE._delete_user_via_admin(group, email)
        recorder.add_group(
            group,
            "PASS" if empty_string_contract_ok(group, observed) else "FAIL",
            [
                {
                    "name": "plan_authorized_direct_empty_string_update",
                    "physical": physical,
                },
                {
                    "name": "isolated_orm_and_user_service_probe",
                    "probe": service_probe,
                },
                {
                    "name": "restore_fixture_through_public_api",
                    "login_code": login["code"],
                    "patch_code": patch["code"],
                    "restored_fields": restored_fields,
                    "admin_cleanup": cleanup,
                    "raw_sha256": [login["raw_sha256"], patch["raw_sha256"]],
                },
            ],
            oracle={
                "control_physical": "empty_string",
                "experiment_physical": "null",
                "orm_nickname": "",
                "empty_access_query_count": 0,
            },
        )
    return _finalize(recorder, case_id)


def run_at052() -> dict[str, Any]:
    case_id = "TC-AT-052"
    password = "AT052-Test@123"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        normal_email = f"at052-normal-{group}@fresh.invalid"
        fault_email = f"at052-fault-{group}@fresh.invalid"
        for email in (normal_email, fault_email):
            BASE._delete_user_via_admin(group, email)
        _rename_tenant_for_fault(group, restore=True)
        normal_registration = BASE._register(
            case_id,
            group,
            "normal_registration",
            {
                "email": normal_email,
                "nickname": "AT052Normal",
                "password": password,
            },
        )
        normal_graph = _registration_graph_snapshot(group, normal_email)
        normal_cleanup = BASE._delete_user_via_admin(group, normal_email)
        before_counts = _core_registration_counts(group)

        fault_registration = {
            "http_status": None,
            "code": None,
            "message": None,
            "raw_sha256": None,
            "exception_type": None,
        }
        fault_state = _rename_tenant_for_fault(group, restore=False)
        try:
            try:
                fault_registration = {
                    **BASE._register(
                        case_id,
                        group,
                        "registration_with_tenant_table_missing",
                        {
                            "email": fault_email,
                            "nickname": "AT052Fault",
                            "password": password,
                        },
                    ),
                    "exception_type": None,
                }
            except Exception as exc:
                fault_registration["exception_type"] = type(exc).__name__
        finally:
            restored_state = _rename_tenant_for_fault(group, restore=True)

        after_counts = _core_registration_counts(group)
        count_deltas = {key: after_counts[key] - before_counts[key] for key in before_counts}
        failed_graph_count = sum(max(value, 0) for value in count_deltas.values())
        fault_email_count = BASE._email_count(group, [fault_email])
        fault_graph = _registration_graph_snapshot(group, fault_email)
        recovery = _login(case_id, group, "login_after_table_restore", BASE_EMAIL, BASE_PASSWORD)
        fault_cleanup = BASE._delete_user_via_admin(group, fault_email)
        observed = {
            "normal_register_code": normal_registration["code"],
            "normal_graph_count": normal_graph["graph_count"],
            "normal_cleanup_succeeded": normal_cleanup,
            "fault_request_failed": fault_registration.get("code") != 0,
            "table_restored": restored_state == {"tenant": True, "backup": False},
            "failed_graph_count": failed_graph_count,
            "recovery_login_code": recovery["code"],
        }
        recorder.add_group(
            group,
            "PASS" if registration_compensation_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "normal_registration_has_complete_four_table_graph",
                    "http_status": normal_registration["http_status"],
                    "code": normal_registration["code"],
                    "graph": normal_graph,
                    "admin_cleanup": normal_cleanup,
                    "raw_sha256": normal_registration["raw_sha256"],
                },
                {
                    "name": "plan_authorized_tenant_table_rename_fault",
                    "fault_state": fault_state,
                    "request": {
                        "http_status": fault_registration.get("http_status"),
                        "code": fault_registration.get("code"),
                        "exception_type": fault_registration.get("exception_type"),
                        "raw_sha256": fault_registration.get("raw_sha256"),
                    },
                    "restored_state": restored_state,
                },
                {
                    "name": "verify_compensation_and_service_recovery",
                    "before_counts": before_counts,
                    "after_counts": after_counts,
                    "count_deltas": count_deltas,
                    "failed_graph_count": failed_graph_count,
                    "fault_email_count": fault_email_count,
                    "fault_graph": fault_graph,
                    "recovery_login_code": recovery["code"],
                    "fault_admin_cleanup": fault_cleanup,
                    "raw_sha256": recovery["raw_sha256"],
                },
            ],
            oracle={
                "normal_graph_count": 4,
                "fault_request_failed": True,
                "failed_graph_count": 0,
                "table_restored": True,
            },
        )
    return _finalize(recorder, case_id)


def run_at053() -> dict[str, Any]:
    case_id = "TC-AT-053"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(case_id, group, "login", BASE_EMAIL, BASE_PASSWORD)
        created = [_create_api_token(case_id, group, f"create_token_{index}", login["_auth"]) for index in range(1, 4)]
        tokens = [item["_token"] for item in created if item["_token"]]
        tenant_id = _owner_tenant_id(group, BASE_EMAIL)
        before_duplicate = _token_set_summary(group, tenant_id, tokens)
        duplicate = _duplicate_api_token_row(group, tenant_id, tokens[0])
        after_duplicate = _token_set_summary(group, tenant_id, tokens)
        deleted = [_delete_api_token(case_id, group, f"delete_token_{index}", login["_auth"], token) for index, token in enumerate(tokens, 1)]
        remaining = _token_set_summary(group, tenant_id, tokens)
        observed = {
            "create_codes": [item["code"] for item in created],
            "created_distinct_count": before_duplicate["distinct_count"],
            "duplicate_groups": before_duplicate["duplicate_groups"],
            "duplicate_insert_rejected": duplicate["duplicate_insert_rejected"],
            "count_unchanged_after_duplicate": before_duplicate["row_count"] == after_duplicate["row_count"] and duplicate["before_count"] == duplicate["after_count"],
            "delete_codes": [item["code"] for item in deleted],
            "remaining_fixture_count": remaining["row_count"],
        }
        recorder.add_group(
            group,
            "PASS" if composite_token_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "create_three_tokens_via_api",
                    "login_code": login["code"],
                    "create_codes": observed["create_codes"],
                    "token_fingerprints": [BASE._fingerprint(token) for token in tokens],
                    "tenant_fingerprint": BASE._fingerprint(tenant_id),
                    "database_summary": before_duplicate,
                    "raw_sha256": [item["raw_sha256"] for item in created],
                },
                {
                    "name": "plan_authorized_duplicate_composite_key_insert",
                    "rejected": duplicate["duplicate_insert_rejected"],
                    "error_type": duplicate["error_type"],
                    "error_code": duplicate["error_code"],
                    "before_count": duplicate["before_count"],
                    "after_count": duplicate["after_count"],
                    "set_summary_after": after_duplicate,
                },
                {
                    "name": "delete_all_fixture_tokens_via_api",
                    "delete_codes": observed["delete_codes"],
                    "remaining": remaining,
                    "raw_sha256": [item["raw_sha256"] for item in deleted],
                },
            ],
            oracle={
                "created_distinct_count": 3,
                "duplicate_groups": 0,
                "duplicate_insert_rejected": True,
                "remaining_fixture_count": 0,
            },
        )
    return _finalize(recorder, case_id)


def run_softdel001() -> dict[str, Any]:
    case_id = "TC-AT-SOFTDEL-001"
    password = "ATSoftDel001@123"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        email = f"at-softdel001-{group}@fresh.invalid"
        BASE._delete_user_via_admin(group, email)
        created = _ensure_user(case_id, group, email, "ATSoftDel001", password)
        status_set = BASE._set_user_status_direct(group, email, "0")
        before = _auth_snapshot(group, email)
        duplicate = BASE._register(
            case_id,
            group,
            "duplicate_registration_after_soft_delete",
            {
                "email": email,
                "nickname": "ATSoftDel001New",
                "password": password,
            },
        )
        after = _auth_snapshot(group, email)
        observed = {
            "status_before": before.get("status"),
            "http_status": duplicate["http_status"],
            "code": duplicate["code"],
            "duplicate_message": "has already registered" in str(duplicate["message"]),
            "user_count": after.get("user_count"),
            "status_after": after.get("status"),
        }
        cleanup = BASE._delete_user_via_admin(group, email)
        recorder.add_group(
            group,
            "PASS" if soft_deleted_registration_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "create_via_api_then_plan_authorized_soft_delete",
                    "created": created,
                    "status_update_succeeded": status_set,
                    "state": before,
                },
                {
                    "name": "attempt_duplicate_registration",
                    "http_status": duplicate["http_status"],
                    "code": duplicate["code"],
                    "duplicate_message": observed["duplicate_message"],
                    "raw_sha256": duplicate["raw_sha256"],
                },
                {
                    "name": "read_only_single_soft_deleted_row_and_cleanup",
                    "state": after,
                    "admin_cleanup": cleanup,
                },
            ],
            oracle={
                "http_status": 200,
                "code": 103,
                "single_row": True,
                "status": "0",
            },
        )
    return _finalize(recorder, case_id)


def run_softdel002() -> dict[str, Any]:
    case_id = "TC-AT-SOFTDEL-002"
    old_password = "ATSoftDel002@123"
    new_password = "ATSoftDel002New@456"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        email = f"at-softdel002-r2-{group}@fresh.invalid"
        BASE._delete_user_via_admin(group, email)
        _ensure_user(case_id, group, email, "ATSoftDel002", old_password)
        status_set = BASE._set_user_status_direct(group, email, "0")
        before = _auth_snapshot(group, email)
        mail_before = len(_mail_messages(email))
        captcha = _captcha_request(case_id, group, "soft_deleted_captcha", email)
        sent = _send_otp(
            case_id,
            group,
            "soft_deleted_send_otp",
            email,
            captcha["_captcha"],
        )
        try:
            otp = _latest_mail_otp(email)
        except RuntimeError:
            otp = None
        if otp is not None:
            verified = _verify_otp(case_id, group, "soft_deleted_verify_otp", email, otp)
            reset = _reset_password(
                case_id,
                group,
                "soft_deleted_reset_password",
                email,
                new_password,
                new_password,
            )
        else:
            verified = {
                "http_status": None,
                "code": None,
                "raw_sha256": None,
            }
            reset = {
                "http_status": None,
                "code": None,
                "authorization_present": False,
                "raw_sha256": None,
            }
        after = _auth_snapshot(group, email)
        mail_delta = len(_mail_messages(email)) - mail_before
        statuses = [
            captcha["http_status"],
            sent["http_status"],
            verified["http_status"],
            reset["http_status"],
        ]
        observed = {
            "status": before.get("status"),
            "security_rejection_http_400": 400 in statuses,
            "reset_succeeded": reset["http_status"] == 200 and reset["code"] == 0,
            "password_hash_unchanged": before.get("password_hash_fingerprint") == after.get("password_hash_fingerprint"),
        }
        cleanup = BASE._delete_user_via_admin(group, email)
        recorder.add_group(
            group,
            "PASS" if soft_deleted_reset_security_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "create_via_api_then_plan_authorized_soft_delete",
                    "status_update_succeeded": status_set,
                    "state_before": before,
                },
                {
                    "name": "complete_real_captcha_smtps_otp_chain",
                    "captcha_http_status": captcha["http_status"],
                    "captcha_length": captcha["captcha_length"],
                    "send_http_status": sent["http_status"],
                    "send_code": sent["code"],
                    "verify_http_status": verified["http_status"],
                    "verify_code": verified["code"],
                    "mail_delta": mail_delta,
                    "mail_otp_captured": otp is not None,
                    "mail_otp_fingerprint": BASE._fingerprint(otp) if otp is not None else None,
                    "raw_sha256": [
                        captcha["raw_sha256"],
                        sent["raw_sha256"],
                        verified["raw_sha256"],
                    ],
                },
                {
                    "name": "attempt_reset_and_verify_hash_security_oracle",
                    "reset_http_status": reset["http_status"],
                    "reset_code": reset["code"],
                    "authorization_present": reset["authorization_present"],
                    "signed_header_was_issued": reset["authorization_present"],
                    "credential_digest_changed": not observed["password_hash_unchanged"],
                    "expected_http_400_seen": observed["security_rejection_http_400"],
                    "security_contract": observed,
                    "state_after": after,
                    "admin_cleanup": cleanup,
                    "raw_sha256": reset["raw_sha256"],
                },
            ],
            oracle={
                "soft_deleted_reset_rejected_http_400": True,
                "password_hash_unchanged": True,
            },
        )
    return _finalize(recorder, case_id)


def run_softdel003() -> dict[str, Any]:
    case_id = "TC-AT-SOFTDEL-003"
    password = "ATSoftDel003@123"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        email = f"at-softdel003-{group}@fresh.invalid"
        BASE._delete_user_via_admin(group, email)
        _ensure_user(case_id, group, email, "ATSoftDel003", password)
        status_set = BASE._set_user_status_direct(group, email, "0")
        before = _auth_snapshot(group, email)
        session = requests.Session()
        start = _oauth_start(case_id, group, "soft_deleted_oauth_start", session)
        code = _issue_oauth_code(
            {
                "email": email,
                "username": f"softdel003{group}",
                "nickname": "ATSoftDel003OAuth",
                "avatar_url": "",
            }
        )
        callback = _oauth_callback(
            case_id,
            group,
            "soft_deleted_oauth_callback",
            session,
            code,
            start["_state"],
        )
        after = _auth_snapshot(group, email)
        profile = (
            _credential_profile(
                case_id,
                group,
                "soft_deleted_oauth_token_profile",
                callback["_auth"],
            )
            if callback["_auth"]
            else None
        )
        observed = {
            "status": before.get("status"),
            "callback_http_status": callback["http_status"],
            "error_present": callback["error_present"],
            "auth_absent": not callback["auth_query_present"],
            "access_state_unchanged": before.get("access_state_fingerprint") == after.get("access_state_fingerprint"),
            "last_login_unchanged": before.get("last_login_time") == after.get("last_login_time"),
        }
        issued_auth_matches_current_access = bool(callback["_auth"]) and _decoded_token_matches(group, callback["_auth"], email)
        cleanup = BASE._delete_user_via_admin(group, email)
        recorder.add_group(
            group,
            "PASS" if soft_deleted_oauth_security_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "create_via_api_then_plan_authorized_soft_delete",
                    "status_update_succeeded": status_set,
                    "state_before": before,
                },
                {
                    "name": "complete_stub_oauth_for_soft_deleted_email",
                    "start_http_status": start["http_status"],
                    "callback_http_status": callback["http_status"],
                    "auth_query_present": callback["auth_query_present"],
                    "error_present": callback["error_present"],
                    "issued_auth_matches_current_access": issued_auth_matches_current_access,
                    "raw_sha256": [start["raw_sha256"], callback["raw_sha256"]],
                },
                {
                    "name": "verify_security_state_and_token_usability",
                    "security_contract": observed,
                    "state_after": after,
                    "profile_http_status": profile["http_status"] if profile else None,
                    "profile_code": profile["code"] if profile else None,
                    "admin_cleanup": cleanup,
                    "raw_sha256": profile["raw_sha256"] if profile else None,
                },
            ],
            oracle={
                "redirect_error": True,
                "auth_absent": True,
                "access_and_last_login_unchanged": True,
            },
        )
    return _finalize(recorder, case_id)


def run_keypair001() -> dict[str, Any]:
    case_id = "TC-AT-KEYPAIR-001"
    frontend = _frontend_public_key_info()
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        _ensure_user(case_id, group, BASE_EMAIL, "AT001User", BASE_PASSWORD)
        backend = _private_key_public_info(RUNTIME_DIR / group / "conf" / "private.pem")
        login = _login_with_frontend_public_key(
            case_id,
            group,
            "login_with_frontend_rsa_public_key",
            BASE._api_base(group),
            BASE_EMAIL,
            BASE_PASSWORD,
            frontend,
        )
        issued_matches = bool(login["_auth"]) and _decoded_token_matches(group, login["_auth"], BASE_EMAIL)
        observed = {
            "public_fingerprints_match": frontend["fingerprint"] == backend["fingerprint"],
            "key_bits": backend["bits"],
            "login_http_status": login["http_status"],
            "login_code": login["code"],
            "signed_header_present": login["authorization_present"],
            "issued_value_matches_database": issued_matches,
        }
        recorder.add_group(
            group,
            "PASS" if keypair_match_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "derive_backend_public_key_without_exposing_private_material",
                    "frontend_public_fingerprint": frontend["fingerprint"],
                    "backend_public_fingerprint": backend["fingerprint"],
                    "fingerprints_match": observed["public_fingerprints_match"],
                    "key_bits": backend["bits"],
                    "frontend_source_sha256": frontend["source_sha256"],
                },
                {
                    "name": "encrypt_with_actual_frontend_rsaPsw_key_and_login",
                    "http_status": login["http_status"],
                    "code": login["code"],
                    "signed_header_was_issued": login["authorization_present"],
                    "issued_value_matches_database": issued_matches,
                    "raw_sha256": login["raw_sha256"],
                },
            ],
            oracle={
                "public_fingerprints_match": True,
                "key_bits": 2048,
                "login": [200, 0],
            },
        )
    return _finalize(recorder, case_id)


def run_keypair002() -> dict[str, Any]:
    case_id = "TC-AT-KEYPAIR-002"
    frontend = _frontend_public_key_info()
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    marker = "Login failed: password decryption error"
    for group in GROUP_ORDER:
        main_private = RUNTIME_DIR / group / "conf" / "private.pem"
        main_private_before = hashlib.sha256(main_private.read_bytes()).hexdigest()
        port = _free_loopback_port()
        root, environment = _prepare_isolated_runtime(case_id, group, port)
        mismatch = _replace_isolated_private_key_with_mismatch(root)
        isolated_public_differs = mismatch["fingerprint"] != frontend["fingerprint"]
        process = None
        log_path = None
        ready_seconds = None
        stopped = False
        try:
            process, log_path, ready_seconds = _launch_isolated_api(case_id, group, environment, port)
            login = _login_with_frontend_public_key(
                case_id,
                group,
                "login_with_mismatched_isolated_private_key",
                f"http://127.0.0.1:{port}/api/v1",
                BASE_EMAIL,
                BASE_PASSWORD,
                frontend,
            )
        finally:
            stopped = _stop_isolated_api(process)

        marker_count = 0
        log_summaries = []
        for candidate in (
            log_path,
            root / "logs" / "ragflow_server.log",
        ):
            if candidate is not None and candidate.exists():
                candidate.chmod(0o600)
                text_value = candidate.read_text(encoding="utf-8", errors="replace")
                marker_count += text_value.count(marker)
                log_summaries.append(_private_log_summary(candidate))
        main_private_after = hashlib.sha256(main_private.read_bytes()).hexdigest()
        observed = {
            "isolated_public_differs": isolated_public_differs,
            "http_status": login["http_status"],
            "code": login["code"],
            "message": login["message"],
            "decryption_error_marker_count": marker_count,
            "main_private_unchanged": main_private_before == main_private_after,
            "isolated_process_stopped": stopped,
            "main_api_healthy": _main_api_healthy(group),
        }
        recorder.add_group(
            group,
            "PASS" if keypair_mismatch_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "prepare_isolated_runtime_with_mismatched_private_copy",
                    "runtime_root": str(root.relative_to(PROJECT_ROOT)),
                    "port": port,
                    "ready_seconds": round(float(ready_seconds), 3),
                    "frontend_public_fingerprint": frontend["fingerprint"],
                    "isolated_public_fingerprint": mismatch["fingerprint"],
                    "isolated_public_differs": isolated_public_differs,
                    "private_files_mode_0600": main_private.stat().st_mode & 0o777 == 0o600 and (root / "conf" / "private.pem").stat().st_mode & 0o777 == 0o600,
                },
                {
                    "name": "submit_frontend_ciphertext_to_mismatched_backend",
                    "http_status": login["http_status"],
                    "code": login["code"],
                    "message": login["message"],
                    "raw_sha256": login["raw_sha256"],
                },
                {
                    "name": "verify_private_log_marker_and_isolation_cleanup",
                    "decryption_error_marker_count": marker_count,
                    "private_log_summaries": log_summaries,
                    "main_private_unchanged": observed["main_private_unchanged"],
                    "isolated_process_stopped": stopped,
                    "main_api_healthy": observed["main_api_healthy"],
                },
            ],
            oracle={
                "isolated_public_differs": True,
                "response": [200, 500, "Fail to crypt password"],
                "main_private_unchanged": True,
            },
        )
    return _finalize(recorder, case_id)


def run_token401001() -> dict[str, Any]:
    case_id = "TC-AT-TOKEN-401-001"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        observed = _run_frontend_probe(case_id, group)
        recorder.add_group(
            group,
            "PASS" if token_401_cleanup_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "execute_current_frontend_http_401_interceptor",
                    "primary_state_absent": observed["authorization_absent"],
                    "secondary_state_absent": observed["token_key_absent"],
                    "profile_cache_absent": observed["user_info_absent"],
                    "redirect_calls": observed["redirect_calls"],
                    "frontend_probe_passed": observed["frontend_probe_passed"],
                    "frontend_probe_exit_code": observed["frontend_probe_exit_code"],
                    "frontend_probe_log_sha256": observed["frontend_probe_log_sha256"],
                    "frontend_source_sha256": observed["frontend_source_sha256"],
                }
            ],
            oracle={
                "three_local_storage_entries_absent": True,
                "redirect_calls": 1,
            },
        )
    return _finalize(recorder, case_id)


def run_token401002() -> dict[str, Any]:
    case_id = "TC-AT-TOKEN-401-002"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        observed = _run_frontend_probe(case_id, group)
        recorder.add_group(
            group,
            "PASS" if token_401_single_redirect_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "execute_five_concurrent_frontend_http_401_responses",
                    "concurrent_response_count": observed["concurrent_response_count"],
                    "primary_state_absent": observed["authorization_absent"],
                    "secondary_state_absent": observed["token_key_absent"],
                    "profile_cache_absent": observed["user_info_absent"],
                    "redirect_calls": observed["redirect_calls"],
                    "frontend_probe_passed": observed["frontend_probe_passed"],
                    "frontend_probe_exit_code": observed["frontend_probe_exit_code"],
                    "frontend_probe_log_sha256": observed["frontend_probe_log_sha256"],
                    "frontend_source_sha256": observed["frontend_source_sha256"],
                }
            ],
            oracle={"concurrent_response_count": 5, "redirect_calls": 1},
        )
    return _finalize(recorder, case_id)


def run_oauth_callback001() -> dict[str, Any]:
    case_id = "TC-AT-OAUTH-CALLBACK-001"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        observed = _run_frontend_probe(case_id, group)
        recorder.add_group(
            group,
            "PASS" if oauth_callback_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "execute_current_useOAuthCallback_with_auth_query",
                    "stored_value_matches_callback": observed["authorization_equals_callback"],
                    "bearer_prefix_absent": observed["bearer_prefix_absent"],
                    "profile_cache_absent": observed["user_info_absent"],
                    "query_parameter_removed": observed["query_auth_removed"],
                    "navigate_root_calls": observed["navigate_root_calls"],
                    "set_search_calls": observed["set_search_calls"],
                    "frontend_probe_passed": observed["frontend_probe_passed"],
                    "frontend_probe_exit_code": observed["frontend_probe_exit_code"],
                    "frontend_probe_log_sha256": observed["frontend_probe_log_sha256"],
                    "frontend_source_sha256": observed["frontend_source_sha256"],
                }
            ],
            oracle={
                "store_raw_callback_value": True,
                "profile_cache_untouched": True,
                "query_parameter_removed": True,
                "navigate_root_calls": 1,
            },
        )
    return _finalize(recorder, case_id)


def run_oauth_callback002() -> dict[str, Any]:
    case_id = "TC-AT-OAUTH-CALLBACK-002"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        observed = _run_frontend_probe(case_id, group)
        recorder.add_group(
            group,
            "PASS" if oauth_callback_noop_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "execute_current_useOAuthCallback_without_auth_query",
                    "primary_state_absent": observed["authorization_absent"],
                    "set_search_calls": observed["set_search_calls"],
                    "navigate_calls": observed["navigate_calls"],
                    "frontend_probe_passed": observed["frontend_probe_passed"],
                    "frontend_probe_exit_code": observed["frontend_probe_exit_code"],
                    "frontend_probe_log_sha256": observed["frontend_probe_log_sha256"],
                    "frontend_source_sha256": observed["frontend_source_sha256"],
                }
            ],
            oracle={
                "primary_state_absent": True,
                "set_search_calls": 0,
                "navigate_calls": 0,
            },
        )
    return _finalize(recorder, case_id)


def run_pwd_length001() -> dict[str, Any]:
    case_id = "TC-AT-PWD-LENGTH-001"
    email = "at-pwd-length-001@fresh.invalid"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        if BASE._email_count(group, [email]):
            if not BASE._delete_user_via_admin(group, email):
                raise RuntimeError("failed to clear short-password fixture")
        registration = BASE._register(
            case_id,
            group,
            "register_with_one_character_password",
            {"email": email, "nickname": "PwdLength001", "password": "1"},
        )
        account_count = BASE._email_count(group, [email])
        cleanup = BASE._delete_user_via_admin(group, email)
        observed = {
            "http_status": registration["http_status"],
            "code": registration["code"],
            "account_count": account_count,
            "signed_header_absent": not registration["auth_header_present"],
            "cleanup_succeeded": cleanup and BASE._email_count(group, [email]) == 0,
        }
        passed = short_password_registration_security_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if passed else "FAIL",
            [
                {
                    "name": "register_one_character_credential_through_public_api",
                    "http_status": registration["http_status"],
                    "code": registration["code"],
                    "signed_header_was_issued": registration["auth_header_present"],
                    "database_account_count": account_count,
                    "raw_sha256": registration["raw_sha256"],
                },
                {
                    "name": "cleanup_fixture_through_admin_api",
                    "cleanup_succeeded": observed["cleanup_succeeded"],
                    "remaining_account_count": BASE._email_count(group, [email]),
                },
            ],
            oracle={
                "http_status": 400,
                "database_account_count": 0,
                "signed_header_absent": True,
            },
            findings=[]
            if passed
            else [
                {
                    "id": "AT-PWD-MIN-001",
                    "summary": "public registration accepts a one-character password",
                    "code_location": "api/apps/restful_apis/user_api.py",
                }
            ],
        )
    return _finalize(recorder, case_id)


def run_pwd_length002() -> dict[str, Any]:
    case_id = "TC-AT-PWD-LENGTH-002"
    email = "at-pwd-length-002@fresh.invalid"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        if BASE._email_count(group, [email]):
            if not BASE._delete_user_via_admin(group, email):
                raise RuntimeError("failed to clear short-password login fixture")
        registration = BASE._register(
            case_id,
            group,
            "prepare_one_character_account_through_public_api",
            {"email": email, "nickname": "PwdLength002", "password": "1"},
        )
        count_before_login = BASE._email_count(group, [email])
        login = _login(
            case_id,
            group,
            "login_with_one_character_password",
            email,
            "1",
        )
        cleanup = BASE._delete_user_via_admin(group, email)
        observed = {
            "registration_rejected": registration["http_status"] == 400 and count_before_login == 0,
            "account_count_before_login": count_before_login,
            "login_succeeded": login["http_status"] == 200 and login["code"] == 0,
            "signed_header_absent": not login["authorization_present"],
            "cleanup_succeeded": cleanup and BASE._email_count(group, [email]) == 0,
        }
        passed = short_password_login_security_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if passed else "FAIL",
            [
                {
                    "name": "prepare_security_precondition_through_public_api",
                    "registration_http_status": registration["http_status"],
                    "registration_code": registration["code"],
                    "database_account_count": count_before_login,
                    "raw_sha256": registration["raw_sha256"],
                },
                {
                    "name": "attempt_login_with_one_character_credential",
                    "http_status": login["http_status"],
                    "code": login["code"],
                    "signed_header_was_issued": login["authorization_present"],
                    "raw_sha256": login["raw_sha256"],
                },
                {
                    "name": "cleanup_fixture_through_admin_api",
                    "cleanup_succeeded": observed["cleanup_succeeded"],
                    "remaining_account_count": BASE._email_count(group, [email]),
                },
            ],
            oracle={
                "registration_rejected": True,
                "database_account_count": 0,
                "login_succeeded": False,
            },
            findings=[]
            if passed
            else [
                {
                    "id": "AT-PWD-MIN-002",
                    "summary": "an account with a one-character password can authenticate",
                    "code_location": "api/apps/restful_apis/user_api.py",
                }
            ],
        )
    return _finalize(recorder, case_id)


def run_exception001() -> dict[str, Any]:
    case_id = "TC-AT-EXCEPTION-001"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        response = _raw_body_request(
            case_id,
            group,
            "submit_malformed_json",
            BASE._api_base(group),
            "/auth/login",
            b"{invalid json}",
        )
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
        }
        passed = invalid_json_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if passed else "FAIL",
            [
                {
                    "name": "submit_malformed_application_json",
                    "request_length": response["request_length"],
                    "request_sha256": response["request_sha256"],
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "message": response["message"],
                    "response_content_type": response["response_content_type"],
                    "elapsed_seconds": round(response["elapsed_seconds"], 3),
                    "raw_sha256": response["raw_sha256"],
                }
            ],
            oracle={"http_status": 400},
            findings=[]
            if passed
            else [
                {
                    "id": "AT-HTTP-STATUS-001",
                    "summary": "malformed JSON is wrapped in a non-400 response",
                    "code_location": "api/apps/__init__.py:server_error_response",
                }
            ],
        )
    return _finalize(recorder, case_id)


def run_exception002() -> dict[str, Any]:
    case_id = "TC-AT-EXCEPTION-002"
    email = "at-exception-002@fresh.invalid"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        if BASE._email_count(group, [email]):
            if not BASE._delete_user_via_admin(group, email):
                raise RuntimeError("failed to clear missing-field fixture")
        created = _ensure_user(case_id, group, email, "Exception002", "Exception002@Fresh")
        response = _request(
            case_id,
            group,
            "login_without_password_field",
            "POST",
            "/auth/login",
            payload={"email": email},
        )
        cleanup = BASE._delete_user_via_admin(group, email)
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "message": response["message"],
        }
        passed = missing_password_contract_ok(observed) and cleanup
        recorder.add_group(
            group,
            "PASS" if passed else "FAIL",
            [
                {
                    "name": "prepare_registered_email_through_public_api",
                    "created": created["created"],
                    "raw_sha256": created.get("raw_sha256"),
                },
                {
                    "name": "submit_login_without_required_password_field",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "message": response["message"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "cleanup_fixture_through_admin_api",
                    "cleanup_succeeded": cleanup,
                    "remaining_account_count": BASE._email_count(group, [email]),
                },
            ],
            oracle={
                "current_characterization": [
                    200,
                    500,
                    "Fail to crypt password",
                ]
            },
            findings=[
                {
                    "id": "AT-ARGUMENT-001",
                    "summary": "missing required password is reported as an internal crypt error instead of an argument error",
                    "code_location": "api/apps/restful_apis/user_api.py:login",
                }
            ],
        )
    return _finalize(recorder, case_id)


def run_exception003() -> dict[str, Any]:
    case_id = "TC-AT-EXCEPTION-003"
    configured_limit = 1024 * 1024
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        port = _free_loopback_port()
        root, environment = _prepare_isolated_runtime(
            case_id,
            group,
            port,
            environment_overrides={"MAX_CONTENT_LENGTH": str(configured_limit)},
        )
        process = None
        log_path = None
        ready_seconds = None
        stopped = False
        try:
            process, log_path, ready_seconds = _launch_isolated_api(case_id, group, environment, port)
            body = json.dumps(
                {
                    "email": "oversize@fresh.invalid",
                    "password": "x" * (configured_limit + 4096),
                },
                separators=(",", ":"),
            ).encode("utf-8")
            response = _raw_body_request(
                case_id,
                group,
                "submit_body_above_isolated_limit",
                f"http://127.0.0.1:{port}/api/v1",
                "/auth/login",
                body,
            )
        finally:
            stopped = _stop_isolated_api(process)
        log_summaries = []
        for candidate in (log_path, root / "logs" / "ragflow_server.log"):
            if candidate is not None and candidate.exists():
                candidate.chmod(0o600)
                log_summaries.append(_private_log_summary(candidate))
        observed = {
            "http_status": response["http_status"],
            "configured_limit": configured_limit,
            "request_length_exceeds_limit": response["request_length"] > configured_limit,
            "isolated_process_stopped": stopped,
            "main_api_healthy": _main_api_healthy(group),
        }
        recorder.add_group(
            group,
            "PASS" if oversize_request_contract_ok(observed) else "FAIL",
            [
                {
                    "name": "launch_isolated_api_with_one_mib_body_limit",
                    "runtime_root": str(root.relative_to(PROJECT_ROOT)),
                    "port": port,
                    "configured_limit": configured_limit,
                    "ready_seconds": round(float(ready_seconds), 3),
                },
                {
                    "name": "submit_body_above_configured_limit_without_persisting_body",
                    "request_length": response["request_length"],
                    "request_sha256": response["request_sha256"],
                    "request_length_exceeds_limit": observed["request_length_exceeds_limit"],
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "message": response["message"],
                    "response_content_type": response["response_content_type"],
                    "response_length": response["response_length"],
                    "elapsed_seconds": round(response["elapsed_seconds"], 3),
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "stop_isolated_api_and_verify_main_health",
                    "isolated_process_stopped": stopped,
                    "main_api_healthy": observed["main_api_healthy"],
                    "private_log_summaries": log_summaries,
                },
            ],
            oracle={"http_status": 413, "configured_limit": configured_limit},
            findings=[]
            if oversize_request_contract_ok(observed)
            else [
                {
                    "id": "AT-HTTP-STATUS-002",
                    "summary": "request-body limit exception is wrapped in HTTP 200 instead of preserving HTTP 413",
                    "code_location": "api/apps/__init__.py:server_error_response",
                }
            ],
        )
    return _finalize(recorder, case_id)


RUNNERS = {
    "TC-AT-001": run_at001,
    "TC-AT-002": run_at002,
    "TC-AT-003": run_at003,
    "TC-AT-004": run_at004,
    "TC-AT-005": run_at005,
    "TC-AT-006": run_at006,
    "TC-AT-007": run_at007,
    "TC-AT-008": run_at008,
    "TC-AT-009": run_at009,
    "TC-AT-010": run_at010,
    "TC-AT-011": run_at011,
    "TC-AT-012": run_at012,
    "TC-AT-013": run_at013,
    "TC-AT-014": run_at014,
    "TC-AT-015": run_at015,
    "TC-AT-016": run_at016,
    "TC-AT-017": run_at017,
    "TC-AT-018": run_at018,
    "TC-AT-019": run_at019,
    "TC-AT-020": run_at020,
    "TC-AT-021": run_at021,
    "TC-AT-022": run_at022,
    "TC-AT-023": run_at023,
    "TC-AT-024": run_at024,
    "TC-AT-025": run_at025,
    "TC-AT-026": run_at026,
    "TC-AT-027": run_at027,
    "TC-AT-028": run_at028,
    "TC-AT-029": run_at029,
    "TC-AT-030": run_at030,
    "TC-AT-031": run_at031,
    "TC-AT-032": run_at032,
    "TC-AT-033": run_at033,
    "TC-AT-034": run_at034,
    "TC-AT-035": run_at035,
    "TC-AT-036": run_at036,
    "TC-AT-037": run_at037,
    "TC-AT-038": run_at038,
    "TC-AT-039": run_at039,
    "TC-AT-040": run_at040,
    "TC-AT-041": run_at041,
    "TC-AT-042": run_at042,
    "TC-AT-043": run_at043,
    "TC-AT-044": run_at044,
    "TC-AT-045": run_at045,
    "TC-AT-046": run_at046,
    "TC-AT-047": run_at047,
    "TC-AT-048": run_at048,
    "TC-AT-049": run_at049,
    "TC-AT-050": run_at050,
    "TC-AT-051": run_at051,
    "TC-AT-052": run_at052,
    "TC-AT-053": run_at053,
    "TC-AT-SOFTDEL-001": run_softdel001,
    "TC-AT-SOFTDEL-002": run_softdel002,
    "TC-AT-SOFTDEL-003": run_softdel003,
    "TC-AT-KEYPAIR-001": run_keypair001,
    "TC-AT-KEYPAIR-002": run_keypair002,
    "TC-AT-TOKEN-401-001": run_token401001,
    "TC-AT-TOKEN-401-002": run_token401002,
    "TC-AT-OAUTH-CALLBACK-001": run_oauth_callback001,
    "TC-AT-OAUTH-CALLBACK-002": run_oauth_callback002,
    "TC-AT-PWD-LENGTH-001": run_pwd_length001,
    "TC-AT-PWD-LENGTH-002": run_pwd_length002,
    "TC-AT-EXCEPTION-001": run_exception001,
    "TC-AT-EXCEPTION-002": run_exception002,
    "TC-AT-EXCEPTION-003": run_exception003,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fresh authentication cases")
    parser.add_argument("--case", choices=sorted(CASE_TITLES), required=True)
    args = parser.parse_args()
    if args.case not in RUNNERS:
        raise SystemExit(f"runner not implemented yet: {args.case}")
    result = RUNNERS[args.case]()
    print(
        json.dumps(
            {
                "case_id": result["case_id"],
                "pair_status": result["pair_status"],
                "group_statuses": {item["group"]: item["status"] for item in result["groups"]},
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
