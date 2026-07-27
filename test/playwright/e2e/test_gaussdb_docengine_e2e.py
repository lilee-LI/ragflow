#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import hashlib
import json
import os
import platform
import re
import shlex
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from urllib.error import HTTPError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

import pytest
import requests

from test.playwright.conftest import _extract_auth_header_from_page, _rsa_encrypt_password


pytestmark = [pytest.mark.auth]

PARSE_TIMEOUT_MS = 300000
API_TIMEOUT_MS = int(os.getenv("GAUSSDB_E2E_API_TIMEOUT_MS", "1200000"))
CHAT_TIMEOUT_MS = int(os.getenv("GAUSSDB_E2E_CHAT_TIMEOUT_MS", "1200000"))
LONG_RUNNING_TIMEOUT_MS = int(os.getenv("GAUSSDB_E2E_LONG_RUNNING_TIMEOUT_MS", "3600000"))
RESTART_WATCHDOG_S = int(os.getenv("GAUSSDB_E2E_RESTART_WATCHDOG_S", "1200"))
SENSITIVE_PATTERNS = (
    re.compile(r"gaussdb://", re.I),
    re.compile(r"postgresql://", re.I),
    re.compile(r"jdbc:", re.I),
    re.compile(r"password\s*[:=]\s*[^<\s]", re.I),
    re.compile(r"api[_-]?key\s*[:=]\s*[^<\s]", re.I),
    re.compile(r"token\s*[:=]\s*[^<\s]", re.I),
    re.compile(r"(?:postgresql|gaussdb)://[^\s<]+@", re.I),
)

_AUTH_HEADERS_BY_BASE_URL: dict[str, dict[str, str]] = {}

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "gaussdb"
ASSET_SPECS = {
    "basic.txt": (
        379,
        "d4a52faf5e561b2bda30efceb64008907f423a895e978c5ae7b89427be8e1ec2",
    ),
    "basic_canary.txt": (
        270,
        "e33a7ad9d0ca4e34fb971b149602e63fce830b9a75ff3c8a5ae7624f56b62298",
    ),
    "batch_a.txt": (
        329,
        "edae32b02edb4868fd661a8721bf21862d62b5a947ae8a37886c681ea9c8eade",
    ),
    "batch_b.txt": (
        357,
        "93b58a9a6e677187ae1a9d80d40195b50d104c0b528034771b3e89d3f448d272",
    ),
    "table.csv": (
        164,
        "012a359737b999440116ba2db0684a072ce1f42438f0dd6af41024fc538715cf",
    ),
    "multikb_a.csv": (
        124,
        "a637ab1058b09ab755524a8bf202742051a01fd1f8f16b5588993b8c2a3d0a95",
    ),
    "multikb_b.csv": (
        124,
        "8326093dd224b9f5b1c3c0c0060b393dc51d546649651c84419ce913dee5200d",
    ),
    "mother.md": (
        353,
        "10d1750fe6d04332cb31469ad1a8d0629f73cdcb5c1691d8023937531cf85543",
    ),
    "unicode_zh.txt": (
        360,
        "10ed662b88b18beb00de3955cb7f3a763cf43804cf2dac0f7167c9759afc6b14",
    ),
    "unicode_zh_canary.txt": (
        304,
        "6b4427cc0c1431c64ef2ee97963b68b507060f70e5867274d0580c46d6d2a0a2",
    ),
    "mixed_target.txt": (
        372,
        "806b548ca2edb51847257c3be5c085ff54012416cdcb37d8ffffd04c502d5fc9",
    ),
    "mixed_zh_canary.txt": (
        318,
        "55ef0b78c6bf331cd1eb4e5dd166c482a3227c35a55f88025d315da88d5d195c",
    ),
    "mixed_en_canary.txt": (
        358,
        "c8d410426a76aa4658535d5340acbec3d2fa0dd0a40aa1c3f93880f3538dacf4",
    ),
    "large_12mb.txt": (12582912, "d879526c0ede14a9252dfb98324eec83cc9b74f2a021c1955c2ab47d9b4cf39c"),
    "long_text.txt": (1048576, "1b9e5791d8af5c60e33f566dbe3e14ff6af8e800c0726404ffa483eb3fb46433"),
}


@pytest.fixture
def run_id(monkeypatch) -> str:
    value = uuid.uuid4().hex
    monkeypatch.setenv("RUN_ID", value)
    return value


def _api_response(
    page,
    method: str,
    base_url: str,
    path: str,
    auth_header: str | None,
    data: dict | None = None,
    *,
    timeout_ms: int | None = None,
):
    auth_state = _AUTH_HEADERS_BY_BASE_URL.get(base_url)
    effective_auth_header = auth_header
    if auth_state and auth_header in {auth_state["initial"], auth_state["current"]}:
        effective_auth_header = auth_state["current"]
    headers = {"Content-Type": "application/json"}
    if effective_auth_header:
        headers["Authorization"] = effective_auth_header
    options = {
        "method": method,
        "headers": headers,
        "data": json.dumps(data) if data is not None else None,
    }
    options["timeout"] = timeout_ms if timeout_ms is not None else API_TIMEOUT_MS
    response = page.request.fetch(urljoin(base_url.rstrip("/") + "/", path.lstrip("/")), **options)
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if response.status == 401 and payload.get("code") == 401 and auth_state and effective_auth_header == auth_state["current"]:
        email = os.getenv("SEEDED_USER_EMAIL")
        password = os.getenv("SEEDED_USER_PASSWORD")
        if email and password:
            refreshed_auth_header = _fresh_api_auth_header(page, base_url, (email, password))
            auth_state["current"] = refreshed_auth_header
            page.evaluate(
                """(auth) => {
                    localStorage.setItem('Authorization', auth);
                    localStorage.setItem('Token', auth);
                }""",
                refreshed_auth_header,
            )
            options["headers"]["Authorization"] = refreshed_auth_header
            response = page.request.fetch(urljoin(base_url.rstrip("/") + "/", path.lstrip("/")), **options)
            try:
                payload = response.json()
            except Exception:
                payload = {}
    return response, payload


def _threaded_json_post(base_url: str, path: str, auth_header: str, data: dict) -> tuple[int, dict]:
    request = Request(
        urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        data=json.dumps(data).encode(),
        headers={"Authorization": auth_header, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=PARSE_TIMEOUT_MS / 1000) as response:
            return response.status, json.loads(response.read())
    except HTTPError as response:
        try:
            payload = json.loads(response.read())
        except (TypeError, ValueError):
            payload = {}
        return response.code, payload


def _api_json(
    page,
    method: str,
    base_url: str,
    path: str,
    auth_header: str,
    data: dict | None = None,
    *,
    timeout_ms: int | None = None,
) -> dict:
    response, payload = _api_response(page, method, base_url, path, auth_header, data, timeout_ms=timeout_ms)
    assert response.ok, f"{method} {path} failed status={response.status} body={response.text()[:2048]}"
    assert isinstance(payload, dict), f"{method} {path} returned non-object payload: {payload!r}"
    assert payload.get("code") == 0, f"{method} {path} returned code={payload.get('code')} payload={payload}"
    return payload


def _assert_api_error(response, payload: dict, *, status: int | None = None, code: int | None = None, message: str | None = None):
    if status is not None:
        assert response.status == status, f"unexpected status={response.status} payload={payload}"
    assert isinstance(payload, dict), payload
    if code is not None:
        assert payload.get("code") == code, payload
    if message is not None:
        assert payload.get("message") == message, payload


def _fresh_api_auth_header(page, base_url: str, seeded_user_credentials) -> str:
    email, password = seeded_user_credentials
    encrypted_password = _rsa_encrypt_password(password)
    response = page.request.post(
        urljoin(base_url.rstrip("/") + "/", "/api/v1/auth/login"),
        headers={"Content-Type": "application/json"},
        data=json.dumps({"email": email, "password": encrypted_password}),
        timeout=API_TIMEOUT_MS,
    )
    assert response.ok, f"API login failed: status={response.status} body={response.text()[:2048]}"
    payload = response.json()
    assert payload.get("code") == 0, f"API login returned payload={payload}"
    auth_header = response.headers.get("authorization") or response.headers.get("Authorization")
    assert auth_header, f"API login did not return Authorization header: headers={response.headers}"
    return auth_header


def _ensure_authed_and_get_header(
    page,
    base_url: str,
    login_url: str,
    active_auth_context,
    auth_click,
    seeded_user_credentials,
) -> str:
    try:
        auth_header = _extract_auth_header_from_page(page)
    except Exception:
        auth_header = _fresh_api_auth_header(page, base_url, seeded_user_credentials)
        page.goto(base_url, wait_until="domcontentloaded")
        page.evaluate(
            """(auth) => {
                localStorage.setItem('Authorization', auth);
                localStorage.setItem('Token', auth);
            }""",
            auth_header,
        )
        page.goto(base_url, wait_until="domcontentloaded")
    _AUTH_HEADERS_BY_BASE_URL[base_url] = {"initial": auth_header, "current": auth_header}
    return auth_header


def _api_multipart_upload(
    page,
    base_url: str,
    dataset_id: str,
    auth_header: str,
    file_path: Path,
    *,
    timeout_ms: int = PARSE_TIMEOUT_MS,
) -> dict:
    response = page.request.post(
        urljoin(base_url.rstrip("/") + "/", f"/api/v1/datasets/{dataset_id}/documents"),
        headers={"Authorization": auth_header},
        multipart={
            "file": {
                "name": file_path.name,
                "mimeType": "text/plain",
                "buffer": file_path.read_bytes(),
            }
        },
        timeout=timeout_ms,
    )
    assert response.ok, f"upload failed status={response.status} body={response.text()[:2048]}"
    payload = response.json()
    assert payload.get("code") == 0, f"upload returned code={payload.get('code')} payload={payload}"
    return payload


def _api_multipart_upload_files(
    page,
    base_url: str,
    dataset_id: str,
    auth_header: str,
    files: list[Path],
    parser_config=None,
    *,
    timeout_s: int = API_TIMEOUT_MS // 1000,
) -> dict:
    multipart = [("file", (path.name, path.read_bytes(), "text/csv" if path.suffix.lower() == ".csv" else "text/plain")) for path in files]
    form = {"parser_config": json.dumps(parser_config)} if parser_config is not None else None
    response = requests.post(
        urljoin(base_url.rstrip("/") + "/", f"/api/v1/datasets/{dataset_id}/documents"),
        headers={"Authorization": auth_header},
        files=multipart,
        data=form,
        timeout=timeout_s,
    )
    assert response.ok, f"upload failed status={response.status_code} body={response.text[:2048]}"
    payload = response.json()
    assert payload.get("code") == 0, payload
    return payload


def _payload_data(payload: dict):
    data = payload.get("data")
    assert data is not None, f"payload has no data: {payload}"
    return data


def _require_gaussdb_env() -> dict:
    required = ["GAUSSDB_HOST", "GAUSSDB_PORT", "GAUSSDB_DATABASE", "GAUSSDB_USER", "GAUSSDB_PASSWORD", "GAUSSDB_SCHEMA"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.skip(f"set {', '.join(missing)} for GaussDB E2E DB assertions")
    return {
        "host": os.environ["GAUSSDB_HOST"],
        "port": int(os.environ["GAUSSDB_PORT"]),
        "dbname": os.environ["GAUSSDB_DATABASE"],
        "user": os.environ["GAUSSDB_USER"],
        "password": os.environ["GAUSSDB_PASSWORD"],
        "schema": os.environ["GAUSSDB_SCHEMA"],
    }


def _record_embedding_profile(request) -> dict:
    batch_size = os.getenv("GAUSSDB_E2E_EMBEDDING_BATCH_SIZE")
    assert batch_size in {"4", "8"}, "real long-running E2E requires GAUSSDB_E2E_EMBEDDING_BATCH_SIZE=8; use 4 only after recording an OOM, request failure, or progress-instability observation"
    profile_path = Path(os.environ["GAUSSDB_E2E_RUNTIME_PROFILE_PATH"])
    assert profile_path.is_file(), profile_path
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert profile.get("doc_engine") == "gaussdb", profile
    assert profile.get("variant") == os.environ["GAUSSDB_VARIANT"], profile
    assert str(profile.get("embedding_batch_size")) == batch_size, profile
    assert profile.get("cuda_provider") == "CUDAExecutionProvider", profile
    assert profile.get("cuda_model") == "BAAI/bge-large-en-v1.5", profile
    assert profile.get("cuda_dimensions") == 1024, profile
    assert profile.get("container_tei_proof") is True, profile
    request.node.user_properties.extend(
        (
            ("embedding_batch_size", batch_size),
            ("cuda_provider", profile["cuda_provider"]),
            ("cuda_model", profile["cuda_model"]),
            ("cuda_dimensions", profile["cuda_dimensions"]),
            ("ragflow_image", profile["ragflow_image"]),
            ("runtime_profile", str(profile_path)),
        )
    )
    return profile


def _asset_path(tmp_path: Path, name: str, run_id: str, *, output_name: str | None = None) -> Path:
    assert name in ASSET_SPECS, f"unknown fixed E2E asset {name}"
    source = FIXTURE_ROOT / name
    assert source.is_file(), f"missing fixed E2E asset {source}"
    expected_size, expected_hash = ASSET_SPECS[name]
    template = source.read_bytes()
    assert len(template) == expected_size, (source, len(template), expected_size)
    assert hashlib.sha256(template).hexdigest() == expected_hash, source
    content = template.replace(b"{{RUN_ID}}", run_id.encode())
    path = tmp_path / (output_name or name)
    path.write_bytes(content)
    return path


@pytest.fixture
def gaussdb_read_conn():
    db = _open_gaussdb_read_conn()
    try:
        yield db
    finally:
        db[0].close()


_CONTAINER_READ_DRIVER = r"""
import json
import sys

import psycopg2


def send(value):
    sys.stdout.write(json.dumps(value, default=str, ensure_ascii=False) + "\n")
    sys.stdout.flush()


cfg = json.loads(sys.stdin.readline())


def connect():
    connection = psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        dbname=cfg["dbname"],
        user=cfg["user"],
        password=cfg["password"],
        options="-c default_transaction_read_only=on",
    )
    connection.autocommit = True
    return connection


def query(connection, request):
    with connection.cursor() as cursor:
        cursor.execute(request["sql"], request.get("params"))
        return cursor.fetchall()


conn = connect()
send({"ready": True})
for line in sys.stdin:
    request = json.loads(line)
    if request.get("close"):
        break
    try:
        send({"rows": query(conn, request)})
    except (psycopg2.InterfaceError, psycopg2.OperationalError):
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn = connect()
            send({"rows": query(conn, request)})
        except Exception as exc:
            send({"error": f"{type(exc).__name__}: {exc}"})
    except Exception as exc:
        send({"error": f"{type(exc).__name__}: {exc}"})
conn.close()
"""


class _ContainerReadCursor:
    def __init__(self, connection):
        self._connection = connection
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self._rows = self._connection.query(sql, params)
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _ContainerReadConnection:
    def __init__(self, container: str, cfg: dict):
        self._process = subprocess.Popen(
            ["docker", "exec", "-i", container, "/ragflow/.venv/bin/python", "-u", "-c", _CONTAINER_READ_DRIVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._write(cfg)
        response = self._read()
        assert response.get("ready"), response

    def _write(self, value):
        assert self._process.stdin
        self._process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        self._process.stdin.flush()

    def _read(self):
        assert self._process.stdout
        line = self._process.stdout.readline()
        if line:
            return json.loads(line)
        assert self._process.stderr
        error = self._process.stderr.read()
        raise AssertionError(f"GaussDB container read driver stopped unexpectedly: {error}")

    def cursor(self):
        return _ContainerReadCursor(self)

    def query(self, sql, params):
        self._write({"sql": sql, "params": params})
        response = self._read()
        assert "error" not in response, response["error"]
        return response["rows"]

    def close(self):
        if self._process.poll() is None:
            self._write({"close": True})
            assert self._process.stdin
            self._process.stdin.close()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.terminate()
                self._process.wait(timeout=10)
        for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
            if stream and not stream.closed:
                stream.close()


def _open_gaussdb_read_conn():
    cfg = _require_gaussdb_env()
    container = os.getenv("GAUSSDB_E2E_QUERY_CONTAINER")
    if container:
        query_cfg = {
            **cfg,
            "host": os.getenv("GAUSSDB_E2E_QUERY_HOST", cfg["host"]),
            "port": int(os.getenv("GAUSSDB_E2E_QUERY_PORT", cfg["port"])),
        }
        return _ContainerReadConnection(container, query_cfg), cfg["schema"]
    psycopg2 = pytest.importorskip("psycopg2")
    conn = psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        dbname=cfg["dbname"],
        user=cfg["user"],
        password=cfg["password"],
        options="-c default_transaction_read_only=on",
    )
    conn.autocommit = True
    return conn, cfg["schema"]


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_exists(db, schema: str, table: str) -> bool:
    conn, _ = db
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM information_schema.tables
                 WHERE table_schema = %s
                   AND table_name = %s
            )
            """,
            [schema, table],
        )
        return bool(cur.fetchone()[0])


def _table_columns(db, schema: str, table: str) -> set[str]:
    conn, _ = db
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = %s
               AND table_name = %s
            """,
            [schema, table],
        )
        return {row[0] for row in cur.fetchall()}


def _index_defs(db, schema: str, table: str) -> str:
    conn, _ = db
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexdef
              FROM pg_indexes
             WHERE schemaname = %s
               AND tablename = %s
             ORDER BY indexname, indexdef
            """,
            [schema, table],
        )
        return "\n".join(row[0] for row in cur.fetchall()).lower()


def _chunk_count(db, schema: str, tenant_id: str, kb_id: str, doc_id: str | None = None) -> int:
    conn, _ = db
    table = f"{_quote_ident(schema)}.{_quote_ident('ragflow_' + tenant_id)}"
    sql = f"SELECT COUNT(*) FROM {table} WHERE kb_id = %s"
    params = [kb_id]
    if doc_id:
        sql += " AND doc_id = %s"
        params.append(doc_id)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return int(cur.fetchone()[0])


def _meta_count(db, schema: str, tenant_id: str, kb_id: str, doc_id: str | None = None) -> int:
    conn, _ = db
    table = f"{_quote_ident(schema)}.{_quote_ident('ragflow_doc_meta_' + tenant_id)}"
    if not _table_exists(db, schema, f"ragflow_doc_meta_{tenant_id}"):
        return 0
    sql = f"SELECT COUNT(*) FROM {table} WHERE kb_id = %s"
    params = [kb_id]
    if doc_id:
        sql += " AND id = %s"
        params.append(doc_id)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return int(cur.fetchone()[0])


def _list_docs(page, base_url: str, dataset_id: str, auth_header: str) -> list[dict]:
    payload = _api_json(
        page,
        "GET",
        base_url,
        f"/api/v1/datasets/{dataset_id}/documents?page=1&page_size=100",
        auth_header,
    )
    data = _payload_data(payload)
    docs = data.get("docs") or data.get("documents") or []
    assert isinstance(docs, list), f"document list data has no docs list: {data}"
    return docs


def _find_doc_by_name(docs: list[dict], name: str) -> dict:
    for doc in docs:
        if doc.get("name") == name:
            return doc
    raise AssertionError(f"document {name!r} not found in docs={docs!r}")


def _wait_doc_done(page, base_url: str, dataset_id: str, auth_header: str, file_name: str, timeout_ms: int = PARSE_TIMEOUT_MS) -> tuple[dict, list[dict]]:
    deadline = time.monotonic() + timeout_ms / 1000
    history = []
    while time.monotonic() < deadline:
        doc = _find_doc_by_name(_list_docs(page, base_url, dataset_id, auth_header), file_name)
        history.append(doc)
        run_state = str(doc.get("run") or doc.get("status") or "").upper()
        progress = float(doc.get("progress") or 0)
        if run_state in {"4", "FAIL", "FAILED"}:
            raise AssertionError(f"document parse failed: dataset_id={dataset_id} document_id={doc.get('id')} run_id={os.getenv('RUN_ID')} service_logs=docker/ragflow-logs history={history!r}")
        if run_state in {"3", "DONE", "DONE.VALUE", "SUCCESS"} or progress >= 1:
            return doc, history
        time.sleep(2)
    document_id = history[-1].get("id") if history else None
    raise AssertionError(f"document parse timeout: dataset_id={dataset_id} document_id={document_id} run_id={os.getenv('RUN_ID')} service_logs=docker/ragflow-logs history={history!r}")


def _assert_payload_masked(payload: dict) -> None:
    text = str(payload)
    for pattern in SENSITIVE_PATTERNS:
        assert not pattern.search(text), f"sensitive value leaked in payload: pattern={pattern.pattern}"


def _upload_and_parse_documents(
    page,
    base_url: str,
    auth_header: str,
    dataset_id: str,
    files: list[Path],
    parser_config=None,
    *,
    upload_timeout_s: int = API_TIMEOUT_MS // 1000,
    parse_timeout_ms: int = PARSE_TIMEOUT_MS,
):
    uploaded = _payload_data(
        _api_multipart_upload_files(
            page,
            base_url,
            dataset_id,
            auth_header,
            files,
            parser_config,
            timeout_s=upload_timeout_s,
        )
    )
    uploaded_docs = uploaded if isinstance(uploaded, list) else [uploaded]
    uploaded_by_name = {doc.get("name"): doc for doc in uploaded_docs}
    assert all(path.name in uploaded_by_name for path in files), (files, uploaded_docs)
    uploaded_docs = [uploaded_by_name[path.name] for path in files]
    doc_ids = [doc.get("id") for doc in uploaded_docs]
    assert all(doc_ids), uploaded_docs
    _parse_documents(page, base_url, auth_header, dataset_id, doc_ids)
    deadline = time.monotonic() + parse_timeout_ms / 1000
    completed = {}
    histories = {doc_id: [] for doc_id in doc_ids}
    while time.monotonic() < deadline and len(completed) < len(doc_ids):
        docs = _list_docs(page, base_url, dataset_id, auth_header)
        for path, doc_id in zip(files, doc_ids):
            if doc_id in completed:
                continue
            doc = _find_doc_by_name(docs, path.name)
            histories[doc_id].append(doc)
            state = str(doc.get("run") or doc.get("status") or "").upper()
            progress = float(doc.get("progress") or 0)
            if state in {"4", "FAIL", "FAILED"}:
                raise AssertionError(f"document parse failed: dataset_id={dataset_id} document_id={doc_id} run_id={os.getenv('RUN_ID')} service_logs=docker/ragflow-logs histories={histories!r}")
            if state in {"3", "DONE", "DONE.VALUE", "SUCCESS"} or progress >= 1:
                completed[doc_id] = doc
        if len(completed) < len(doc_ids):
            time.sleep(2)
    assert len(completed) == len(doc_ids), f"parse timeout dataset_id={dataset_id} run_id={os.getenv('RUN_ID')} service_logs=docker/ragflow-logs histories={histories!r}"
    return uploaded_docs, completed, histories


def _assert_no_vector_fields(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert not re.fullmatch(r"q_\d+_vec(?:_valid)?", str(key)), value
            _assert_no_vector_fields(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_vector_fields(item)


def _db_table_amounts(db, schema: str, tenant_id: str, kb_id: str, minimum: int | None = None) -> list[int]:
    conn, _ = db
    table = f"{_quote_ident(schema)}.{_quote_ident('ragflow_' + tenant_id)}"
    sql = f"SELECT chunk_data #>> '{{Amount}}' FROM {table} WHERE kb_id = %s AND chunk_data ? 'Amount'"
    params = [kb_id]
    if minimum is not None:
        sql += " AND (chunk_data #>> '{Amount}')::INTEGER > %s"
        params.append(minimum)
    sql += " ORDER BY id"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [int(row[0]) for row in cur.fetchall()]


def _db_meta_fields(db, schema: str, tenant_id: str, kb_id: str, ids: list[str]) -> dict[str, dict]:
    conn, _ = db
    table = f"{_quote_ident(schema)}.{_quote_ident('ragflow_doc_meta_' + tenant_id)}"
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, meta_fields FROM {table} WHERE kb_id = %s AND id = ANY(%s)", [kb_id, ids])
        return {row[0]: row[1] for row in cur.fetchall()}


def _db_pagerank(db, schema: str, tenant_id: str, kb_id: str, ids: list[str]) -> dict[str, float]:
    conn, _ = db
    table = f"{_quote_ident(schema)}.{_quote_ident('ragflow_' + tenant_id)}"
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, COALESCE(pagerank_fea, 0) FROM {table} WHERE kb_id = %s AND id = ANY(%s)", [kb_id, ids])
        return {row[0]: float(row[1]) for row in cur.fetchall()}


def _db_kb_signature(db, schema: str, tenant_id: str, kb_id: str) -> str:
    chunk_rows = _db_rows_by_columns(
        db,
        schema,
        tenant_id,
        kb_id,
        [
            "id",
            "doc_id",
            "kb_id",
            "content_with_weight",
            "available_int",
            "pagerank_fea",
            "chunk_data",
            "tag_kwd",
            "position_int",
        ],
    )
    conn, _ = db
    meta_table = f"{_quote_ident(schema)}.{_quote_ident('ragflow_doc_meta_' + tenant_id)}"
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, meta_fields FROM {meta_table} WHERE kb_id = %s ORDER BY id", [kb_id])
        meta_rows = cur.fetchall()
    payload = {"chunks": chunk_rows, "metadata": meta_rows}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _db_chunk_locations(db, schema: str, tenant_id: str, ids: list[str]) -> list[tuple[str, str, str]]:
    conn, _ = db
    table = f"{_quote_ident(schema)}.{_quote_ident('ragflow_' + tenant_id)}"
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, doc_id, kb_id FROM {table} WHERE id = ANY(%s) ORDER BY id", [ids])
        return [tuple(row) for row in cur.fetchall()]


def _wait_for_run(page, base_url: str, auth_header: str, dataset_id: str, file_name: str, states: set[str], timeout_ms: int = PARSE_TIMEOUT_MS) -> tuple[dict, list[dict]]:
    deadline = time.monotonic() + timeout_ms / 1000
    history = []
    while time.monotonic() < deadline:
        doc = _find_doc_by_name(_list_docs(page, base_url, dataset_id, auth_header), file_name)
        history.append(doc)
        state = str(doc.get("run") or doc.get("status") or "").upper()
        if state in {"4", "FAIL", "FAILED"}:
            raise AssertionError(
                f"document entered failure state: dataset_id={dataset_id} document_id={doc.get('id')} run_id={os.getenv('RUN_ID')} service_logs=docker/ragflow-logs history={history!r}"
            )
        if state in states:
            return doc, history
        time.sleep(2)
    document_id = history[-1].get("id") if history else None
    raise AssertionError(f"document did not reach {states}: dataset_id={dataset_id} document_id={document_id} run_id={os.getenv('RUN_ID')} service_logs=docker/ragflow-logs history={history!r}")


def _wait_for_running_or_chunks(
    page,
    base_url: str,
    auth_header: str,
    dataset_id: str,
    document_id: str,
    file_name: str,
    db,
    schema: str,
    tenant_id: str,
    timeout_ms: int,
) -> tuple[dict, list[dict]]:
    deadline = time.monotonic() + timeout_ms / 1000
    history = []
    while time.monotonic() < deadline:
        doc = _find_doc_by_name(_list_docs(page, base_url, dataset_id, auth_header), file_name)
        state = str(doc.get("run") or doc.get("status") or "").upper()
        db_chunk_count = _chunk_count(db, schema, tenant_id, dataset_id, document_id)
        history.append({"document": doc, "db_chunk_count": db_chunk_count})
        if state in {"4", "FAIL", "FAILED"}:
            raise AssertionError(f"document entered failure state before cancellation: dataset_id={dataset_id} document_id={document_id} run_id={os.getenv('RUN_ID')} history={history!r}")
        if state in {"2", "RUNNING"} or db_chunk_count > 0:
            return doc, history
        time.sleep(2)
    raise AssertionError(f"document never entered RUNNING and no partial chunk was observed: dataset_id={dataset_id} document_id={document_id} run_id={os.getenv('RUN_ID')} history={history!r}")


def _stop_document_before_cleanup(page, base_url: str, auth_header: str, dataset_id: str, document_id: str, file_name: str) -> None:
    doc = _find_doc_by_name(_list_docs(page, base_url, dataset_id, auth_header), file_name)
    state = str(doc.get("run") or doc.get("status") or "").upper()
    if state not in {"2", "RUNNING"}:
        return
    stopped = _payload_data(
        _api_json(
            page,
            "POST",
            base_url,
            f"/api/v1/datasets/{dataset_id}/documents/stop",
            auth_header,
            {"document_ids": [document_id]},
        )
    )
    assert stopped.get("success_count") == 1, stopped
    _wait_for_run(page, base_url, auth_header, dataset_id, file_name, {"5", "CANCEL"}, timeout_ms=120000)


def _wait_gaussdb_status(page, base_url: str, auth_header: str, expected: str, timeout_ms: int = PARSE_TIMEOUT_MS) -> tuple[dict, list[dict]]:
    deadline = time.monotonic() + timeout_ms / 1000
    history = []
    while time.monotonic() < deadline:
        response, payload = _api_response(page, "GET", base_url, "/api/v1/system/gaussdb/status", auth_header)
        history.append({"http_status": response.status, "payload": payload})
        if response.ok and isinstance(payload, dict) and payload.get("code") == 0:
            data = payload.get("data") or {}
            if data.get("status") == expected:
                return payload, history
        time.sleep(2)
    raise AssertionError(f"GaussDB status did not become {expected}: run_id={os.getenv('RUN_ID')} history={history!r}")


def _write_json_artifact(request, name: str, payload: dict) -> Path:
    artifact_dir = Path(__file__).resolve().parents[1] / "artifacts" / "e2e" / "gaussdb"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"{request.node.name}_{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _run_exclusive_command(env_name: str, *, cwd: str | None = None) -> None:
    command = os.getenv(env_name)
    assert command, f"exclusive E2E requires {env_name}"
    subprocess.run(shlex.split(command, posix=os.name != "nt"), check=True, cwd=cwd, timeout=300)


def _wait_ragflow_health(base_url: str, timeout_s: int = 300) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            response = requests.get(
                urljoin(base_url.rstrip("/") + "/", "/api/v1/system/healthz"),
                timeout=5,
            )
            if response.status_code == 200 and response.json().get("status") == "ok":
                return
        except (requests.RequestException, ValueError):
            pass
        time.sleep(5)
    raise AssertionError("RAGFlow did not recover after restart")


def _capture_compose_evidence(request, compose_dir: str) -> None:
    artifact_dir = Path(__file__).resolve().parents[1] / "artifacts" / "e2e" / "gaussdb"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    for label, args in (
        ("compose-ps", ["docker", "compose", "ps"]),
        ("ragflow-cpu-logs", ["docker", "compose", "logs", "--tail", "500", "ragflow-cpu"]),
    ):
        result = subprocess.run(
            args,
            cwd=compose_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        path = artifact_dir / f"{request.node.name}_{label}.log"
        path.write_text(result.stdout + "\nSTDERR\n" + result.stderr, encoding="utf-8")


def _tenant_id(provider_state: dict) -> str:
    tenant_id = str(provider_state["tenant_id"])
    assert re.fullmatch(r"[A-Za-z0-9_-]+", tenant_id), tenant_id
    return tenant_id


def _create_dataset(page, base_url: str, auth_header: str, name: str, chunk_method: str = "naive", parser_config=None) -> str:
    parser_config = dict(parser_config or {})
    parser_config.setdefault("raptor", {"use_raptor": False})
    parser_config.setdefault("graphrag", {"use_graphrag": False})
    payload = {"name": name, "chunk_method": chunk_method, "permission": "me"}
    payload["parser_config"] = parser_config
    created = _payload_data(_api_json(page, "POST", base_url, "/api/v1/datasets", auth_header, payload))
    dataset_id = created.get("id") or created.get("kb_id")
    assert dataset_id, created
    return dataset_id


def _update_dataset(page, base_url: str, auth_header: str, dataset_id: str, data: dict) -> dict:
    return _api_json(page, "PUT", base_url, f"/api/v1/datasets/{dataset_id}", auth_header, data)


def _get_dataset(page, base_url: str, auth_header: str, dataset_id: str) -> dict:
    return _payload_data(_api_json(page, "GET", base_url, f"/api/v1/datasets/{dataset_id}", auth_header))


def _delete_dataset(page, base_url: str, auth_header: str, dataset_id: str) -> None:
    _api_json(page, "DELETE", base_url, "/api/v1/datasets", auth_header, {"ids": [dataset_id]})


def _parse_documents(page, base_url: str, auth_header: str, dataset_id: str, document_ids: list[str]) -> dict:
    payload = _api_json(page, "POST", base_url, f"/api/v1/datasets/{dataset_id}/documents/parse", auth_header, {"document_ids": document_ids})
    data = _payload_data(payload)
    assert data.get("success_count") == len(document_ids), data
    return data


def _update_document(page, base_url: str, auth_header: str, dataset_id: str, document_id: str, data: dict) -> dict:
    return _api_json(page, "PATCH", base_url, f"/api/v1/datasets/{dataset_id}/documents/{document_id}", auth_header, data)


def _search_dataset(browser_page, base_url: str, auth_header: str, dataset_id: str, question: str, **options) -> dict:
    payload = {"question": question, "top_k": 10, "page": 1, "size": 10, "similarity_threshold": 0.0, "vector_similarity_weight": 0.0}
    payload.update(options)
    return _payload_data(_api_json(browser_page, "POST", base_url, f"/api/v1/datasets/{dataset_id}/search", auth_header, payload))


def _search_datasets(browser_page, base_url: str, auth_header: str, dataset_ids: list[str], question: str, **options) -> dict:
    payload = {
        "dataset_ids": dataset_ids,
        "question": question,
        "top_k": 10,
        "page": 1,
        "size": 10,
        "similarity_threshold": 0.0,
        "vector_similarity_weight": 0.0,
    }
    payload.update(options)
    return _payload_data(_api_json(browser_page, "POST", base_url, "/api/v1/datasets/search", auth_header, payload))


def _add_chunk(page, base_url: str, auth_header: str, dataset_id: str, document_id: str, content: str, **fields) -> dict:
    payload = {"content": content, **fields}
    data = _payload_data(
        _api_json(
            page,
            "POST",
            base_url,
            f"/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks",
            auth_header,
            payload,
        )
    )
    chunk = data.get("chunk")
    assert isinstance(chunk, dict) and chunk.get("id"), data
    return chunk


def _list_chunks(page, base_url: str, auth_header: str, dataset_id: str, document_id: str, *, page_size: int = 100, keywords: str = "") -> dict:
    query = f"page=1&page_size={page_size}"
    if keywords:
        query += f"&keywords={quote(keywords)}"
    return _payload_data(
        _api_json(
            page,
            "GET",
            base_url,
            f"/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks?{query}",
            auth_header,
        )
    )


def _update_chunk(page, base_url: str, auth_header: str, dataset_id: str, document_id: str, chunk_id: str, data: dict) -> None:
    _api_json(
        page,
        "PATCH",
        base_url,
        f"/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}",
        auth_header,
        data,
    )


def _delete_chunks(page, base_url: str, auth_header: str, dataset_id: str, document_id: str, chunk_ids: list[str]) -> None:
    _api_json(
        page,
        "DELETE",
        base_url,
        f"/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks",
        auth_header,
        {"chunk_ids": chunk_ids},
    )


def _set_document_status(page, base_url: str, auth_header: str, dataset_id: str, document_id: str, enabled: bool) -> dict:
    data = _payload_data(
        _api_json(
            page,
            "POST",
            base_url,
            f"/api/v1/datasets/{dataset_id}/documents/batch-update-status",
            auth_header,
            {"doc_ids": [document_id], "status": "1" if enabled else "0"},
        )
    )
    assert data.get(document_id, {}).get("status") == ("1" if enabled else "0"), data
    return data


def _db_doc_rows(db, schema: str, tenant_id: str, kb_id: str, doc_id: str | None = None) -> list[dict]:
    return _db_rows_by_columns(db, schema, tenant_id, kb_id, ["id", "doc_id", "kb_id", "docnm_kwd", "content_with_weight", "chunk_data"], doc_id)


def _db_rows_by_columns(db, schema: str, tenant_id: str, kb_id: str, columns: list[str], doc_id: str | None = None) -> list[dict]:
    conn, _ = db
    table = f"{_quote_ident(schema)}.{_quote_ident('ragflow_' + tenant_id)}"
    available = _table_columns(db, schema, f"ragflow_{tenant_id}")
    selected = [column for column in columns if column in available]
    assert selected, (table, columns, available)
    select_clause = ", ".join(_quote_ident(column) for column in selected)
    where = "kb_id = %s"
    params = [kb_id]
    if doc_id:
        where += " AND doc_id = %s"
        params.append(doc_id)
    with conn.cursor() as cur:
        cur.execute(f"SELECT {select_clause} FROM {table} WHERE {where} ORDER BY id", params)
        return [dict(zip(selected, row)) for row in cur.fetchall()]


def _db_vector_valid(db, schema: str, tenant_id: str, kb_id: str, doc_id: str) -> list[bool]:
    return list(_db_vector_valid_by_id(db, schema, tenant_id, kb_id, doc_id).values())


def _db_vector_valid_by_id(db, schema: str, tenant_id: str, kb_id: str, doc_id: str) -> dict[str, bool]:
    _, state = _db_vector_valid_state(db, schema, tenant_id, kb_id, doc_id)
    return state


def _db_vector_valid_state(db, schema: str, tenant_id: str, kb_id: str, doc_id: str) -> tuple[str, dict[str, bool]]:
    conn, _ = db
    table = f"{_quote_ident(schema)}.{_quote_ident('ragflow_' + tenant_id)}"
    vector_valid_columns = sorted(column for column in _table_columns(db, schema, f"ragflow_{tenant_id}") if re.fullmatch(r"q_\d+_vec_valid", column))
    assert vector_valid_columns, f"missing vector valid column in {table}"
    select_columns = ", ".join(_quote_ident(column) for column in vector_valid_columns)
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, {select_columns} FROM {table} WHERE kb_id = %s AND doc_id = %s ORDER BY id", [kb_id, doc_id])
        rows = cur.fetchall()
    assert rows, (kb_id, doc_id)
    active = [column for index, column in enumerate(vector_valid_columns, start=1) if any(bool(row[index]) for row in rows)]
    assert len(active) == 1, (kb_id, doc_id, active, vector_valid_columns)
    index = vector_valid_columns.index(active[0]) + 1
    return active[0], {row[0]: bool(row[index]) for row in rows}


def _create_chat(page, base_url: str, auth_header: str, name: str, dataset_ids: list[str]) -> str:
    data = _payload_data(_api_json(page, "POST", base_url, "/api/v1/chats", auth_header, {"name": name, "dataset_ids": dataset_ids}))
    chat_id = data.get("id")
    assert chat_id, data
    return chat_id


def _create_session(page, base_url: str, auth_header: str, chat_id: str, name: str) -> str:
    data = _payload_data(_api_json(page, "POST", base_url, f"/api/v1/chats/{chat_id}/sessions", auth_header, {"name": name}))
    session_id = data.get("id")
    assert session_id, data
    return session_id


def _complete_chat(page, base_url: str, auth_header: str, chat_id: str, session_id: str, message: str, message_id: str) -> dict:
    data = _payload_data(
        _api_json(
            page,
            "POST",
            base_url,
            "/api/v1/chat/completions",
            auth_header,
            {
                "chat_id": chat_id,
                "session_id": session_id,
                "messages": [{"id": message_id, "role": "user", "content": message}],
                "stream": False,
            },
            timeout_ms=CHAT_TIMEOUT_MS,
        )
    )
    assert isinstance(data.get("answer"), str) and data["answer"].strip(), data
    assert isinstance(data.get("reference"), dict), data
    return data


def _delete_chat_resources(page, base_url: str, auth_header: str, chat_id: str | None, session_id: str | None) -> None:
    if chat_id and session_id:
        _api_json(page, "DELETE", base_url, f"/api/v1/chats/{chat_id}/sessions", auth_header, {"ids": [session_id]})
    if chat_id:
        _api_json(page, "DELETE", base_url, f"/api/v1/chats/{chat_id}", auth_header)


def _cleanup_chat_and_datasets(
    page,
    base_url: str,
    auth_header: str,
    chat_id: str | None,
    session_id: str | None,
    db,
    schema: str,
    tenant_id: str,
    dataset_ids,
) -> None:
    errors = []
    try:
        _delete_chat_resources(page, base_url, auth_header, chat_id, session_id)
    except Exception as exc:  # noqa: BLE001 - cleanup must continue after any API failure
        errors.append(exc)
    for dataset_id in dict.fromkeys(dataset_id for dataset_id in dataset_ids if dataset_id):
        try:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(db, schema, tenant_id, dataset_id) == 0
            assert _meta_count(db, schema, tenant_id, dataset_id) == 0
        except Exception as exc:  # noqa: BLE001 - cleanup must attempt every dataset
            errors.append(exc)
    if errors:
        raise ExceptionGroup("GaussDB E2E cleanup failed", errors)


def _public_chunk_id(chunk: dict) -> str | None:
    return chunk.get("id") or chunk.get("chunk_id")


def _public_doc_id(chunk: dict) -> str | None:
    return chunk.get("doc_id") or chunk.get("document_id")


def _public_doc_name(chunk: dict) -> str | None:
    return chunk.get("docnm_kwd") or chunk.get("document_name") or chunk.get("doc_name")


def _assert_search_sources(
    result: dict,
    db,
    schema: str,
    tenant_id: str,
    dataset_id: str,
    expected_doc_names: dict[str, str],
    *,
    exact_doc_ids: set[str] | None = None,
) -> list[dict]:
    chunks = result.get("chunks") or []
    assert chunks, result
    actual_doc_ids = {_public_doc_id(chunk) for chunk in chunks}
    if exact_doc_ids is not None:
        assert actual_doc_ids == exact_doc_ids, result
    assert actual_doc_ids <= set(expected_doc_names), result
    db_chunk_ids = {}
    for doc_id in actual_doc_ids:
        rows = _db_doc_rows(db, schema, tenant_id, dataset_id, doc_id)
        assert rows, (doc_id, result)
        db_chunk_ids[doc_id] = {row["id"] for row in rows}
    for chunk in chunks:
        doc_id = _public_doc_id(chunk)
        assert chunk.get("kb_id") == dataset_id, chunk
        assert _public_doc_name(chunk) == expected_doc_names[doc_id], chunk
        assert _public_chunk_id(chunk) in db_chunk_ids[doc_id], chunk
    return chunks


def _assert_chat_sources(
    result: dict,
    db,
    schema: str,
    tenant_id: str,
    dataset_id: str,
    expected_doc_names: dict[str, str],
    target_doc_id: str,
) -> list[dict]:
    references = result["reference"].get("chunks") or []
    reference_doc_ids = {chunk.get("document_id") for chunk in references}
    assert references and target_doc_id in reference_doc_ids, result
    cited_reference_ids = {int(value) for value in re.findall(r"\[ID:(\d+)\]", str(result.get("answer") or ""))}
    target_reference_ids = {index for index, chunk in enumerate(references) if chunk.get("document_id") == target_doc_id}
    assert cited_reference_ids & target_reference_ids, result
    db_chunk_ids = {}
    for doc_id in reference_doc_ids:
        assert doc_id in expected_doc_names, references
        rows = _db_doc_rows(db, schema, tenant_id, dataset_id, doc_id)
        assert rows and {row["docnm_kwd"] for row in rows} == {expected_doc_names[doc_id]}, (doc_id, references)
        db_chunk_ids[doc_id] = {row["id"] for row in rows}
    for chunk in references:
        doc_id = chunk.get("document_id")
        assert chunk.get("dataset_id") == dataset_id, chunk
        assert chunk.get("document_name") == expected_doc_names[doc_id], chunk
        assert _public_chunk_id(chunk) in db_chunk_ids[doc_id], chunk
    return references


def _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, provider_state, db):
    auth_header = _ensure_authed_and_get_header(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials)
    return auth_header, _tenant_id(provider_state), db[1]


@pytest.fixture(scope="module")
def gaussdb_cleanup_canary(ensure_model_provider_configured, base_url, seeded_user_credentials, tmp_path_factory):
    provider_state = ensure_model_provider_configured
    page = provider_state["page"]
    auth_header = provider_state["auth_header"]
    tenant_id = _tenant_id(provider_state)
    canary_run_id = uuid.uuid4().hex
    dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-cleanup-canary-{canary_run_id}")
    canary_dir = tmp_path_factory.mktemp("gaussdb-cleanup-canary")
    file_path = _asset_path(canary_dir, "basic_canary.txt", canary_run_id)
    uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, [file_path])
    doc_id = uploaded[0]["id"]
    _update_document(
        page,
        base_url,
        auth_header,
        dataset_id,
        doc_id,
        {"meta_fields": {"role": "cleanup-canary", "run_id": canary_run_id}},
    )

    def snapshot() -> str:
        db = _open_gaussdb_read_conn()
        try:
            signature = _db_kb_signature(db, db[1], tenant_id, dataset_id)
            assert _chunk_count(db, db[1], tenant_id, dataset_id) > 0
            assert _meta_count(db, db[1], tenant_id, dataset_id) > 0
            return signature
        finally:
            db[0].close()

    initial_signature = snapshot()
    try:
        yield {"dataset_id": dataset_id, "snapshot": snapshot, "initial_signature": initial_signature}
    finally:
        cleanup_auth_header = _fresh_api_auth_header(page, base_url, seeded_user_credentials)
        _delete_dataset(page, base_url, cleanup_auth_header, dataset_id)
        db = _open_gaussdb_read_conn()
        try:
            assert _chunk_count(db, db[1], tenant_id, dataset_id) == 0
            assert _meta_count(db, db[1], tenant_id, dataset_id) == 0
        finally:
            db[0].close()


@pytest.fixture(autouse=True)
def preserve_gaussdb_cleanup_canary(gaussdb_cleanup_canary):
    before = gaussdb_cleanup_canary["snapshot"]()
    yield
    after = gaussdb_cleanup_canary["snapshot"]()
    assert after == before == gaussdb_cleanup_canary["initial_signature"]


@pytest.mark.p0
def test_tc_e2e_101_dataset_parse_initializes_gaussdb_objects(
    page,
    base_url,
    login_url,
    active_auth_context,
    auth_click,
    seeded_user_credentials,
    ensure_model_provider_configured,
    gaussdb_read_conn,
    run_id,
    tmp_path,
):
    auth_header = _ensure_authed_and_get_header(
        page,
        base_url,
        login_url,
        active_auth_context,
        auth_click,
        seeded_user_credentials,
    )
    tenant_id = _tenant_id(ensure_model_provider_configured)
    schema = gaussdb_read_conn[1]
    dataset_name = f"gaussdb-e2e-objects-{run_id}"
    dataset_id = None

    try:
        chunk_table = f"ragflow_{tenant_id}"
        meta_table = f"ragflow_doc_meta_{tenant_id}"
        initial_chunk_table = _table_exists(gaussdb_read_conn, schema, chunk_table)
        initial_meta_table = _table_exists(gaussdb_read_conn, schema, meta_table)
        create_payload = _api_json(page, "POST", base_url, "/api/v1/datasets", auth_header, {"name": dataset_name, "chunk_method": "naive", "permission": "me"})
        created = _payload_data(create_payload)
        dataset_id = created.get("id") or created.get("kb_id")
        assert dataset_id, created
        if initial_chunk_table:
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
        if initial_meta_table:
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0

        file_path = _asset_path(tmp_path, "basic.txt", run_id)
        uploaded = _payload_data(_api_multipart_upload(page, base_url, dataset_id, auth_header, file_path))
        doc = uploaded[0] if isinstance(uploaded, list) else uploaded
        doc_id = doc.get("id")
        assert doc_id, uploaded
        _parse_documents(page, base_url, auth_header, dataset_id, [doc_id])
        parsed_doc, poll_history = _wait_doc_done(page, base_url, dataset_id, auth_header, file_path.name)
        assert poll_history and str(parsed_doc.get("run") or parsed_doc.get("status") or "").upper() in {"3", "DONE", "SUCCESS"}

        assert _table_exists(gaussdb_read_conn, schema, chunk_table)
        assert _table_exists(gaussdb_read_conn, schema, meta_table)
        columns = _table_columns(gaussdb_read_conn, schema, chunk_table)
        assert {"id", "kb_id", "doc_id", "chunk_data", "pagerank_fea"}.issubset(columns)
        valid_column, valid = _db_vector_valid_state(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
        dimension = re.search(r"q_(\d+)_vec_valid$", valid_column).group(1)
        assert f"q_{dimension}_vec" in columns
        index_defs = _index_defs(gaussdb_read_conn, schema, chunk_table)
        assert f"idx_gdb_{chunk_table}_fts_all" in index_defs
        vector_defs = [definition for definition in index_defs.splitlines() if re.search(rf"using\s+gsdiskann\s*\(\s*q_{dimension}_vec(?:\s+cosine)?\s*\)", definition)]
        assert len(vector_defs) == 1, index_defs
        assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) > 0
        assert all(valid.values())
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _table_exists(gaussdb_read_conn, schema, chunk_table)
            assert _table_exists(gaussdb_read_conn, schema, meta_table)


@pytest.mark.p0
def test_tc_e2e_201_parse_persists_and_searches_fixed_document(
    page,
    base_url,
    login_url,
    active_auth_context,
    auth_click,
    seeded_user_credentials,
    ensure_model_provider_configured,
    gaussdb_read_conn,
    run_id,
    tmp_path,
):
    auth_header = _ensure_authed_and_get_header(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials)
    tenant_id = _tenant_id(ensure_model_provider_configured)
    schema = gaussdb_read_conn[1]
    dataset_id = None
    doc_id = None
    file_path = _asset_path(tmp_path, "basic.txt", run_id)
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-doc-{run_id}")
        uploaded = _payload_data(_api_multipart_upload(page, base_url, dataset_id, auth_header, file_path))
        doc = uploaded[0] if isinstance(uploaded, list) else uploaded
        doc_id = doc.get("id")
        assert doc_id, uploaded
        _parse_documents(page, base_url, auth_header, dataset_id, [doc_id])
        parsed_doc, history = _wait_doc_done(page, base_url, dataset_id, auth_header, file_path.name)
        parsed_chunk_count = parsed_doc.get("chunk_count", parsed_doc.get("chunk_num"))
        assert isinstance(parsed_chunk_count, int) and parsed_chunk_count >= 1, (parsed_doc, history)
        rows = _db_doc_rows(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
        assert len(rows) == parsed_chunk_count
        assert all(run_id in str(row["content_with_weight"]) for row in rows)
        assert all(_db_vector_valid(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id))

        query = f"GaussDB E2E Shenzhen audit {run_id}"
        search_data = _search_dataset(page, base_url, auth_header, dataset_id, query, size=5)
        chunks = search_data.get("chunks") or []
        assert any(chunk.get("doc_id") == doc_id or chunk.get("document_id") == doc_id for chunk in chunks), search_data
        assert all(chunk.get("kb_id") == dataset_id for chunk in chunks)
        assert int(search_data.get("total", 0)) >= len(chunks)

    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p1
def test_tc_e2e_202_batch_upload_parses_both_documents_independently(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-batch-{run_id}")
        files = [_asset_path(tmp_path, "batch_a.txt", run_id), _asset_path(tmp_path, "batch_b.txt", run_id)]
        uploaded, completed, histories = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, files)
        doc_ids = [doc["id"] for doc in uploaded]
        assert len(doc_ids) == 2, (uploaded, completed, histories)
        assert all((completed[doc_id].get("chunk_count", completed[doc_id].get("chunk_num")) or 0) > 0 for doc_id in doc_ids), (completed, histories)
        alpha = _search_dataset(page, base_url, auth_header, dataset_id, "alpha", similarity_threshold=0.000001)
        beta = _search_dataset(page, base_url, auth_header, dataset_id, "beta", similarity_threshold=0.000001)
        alpha_ids = {chunk.get("doc_id") or chunk.get("document_id") for chunk in alpha.get("chunks") or []}
        beta_ids = {chunk.get("doc_id") or chunk.get("document_id") for chunk in beta.get("chunks") or []}
        assert alpha_ids and beta_ids and alpha_ids.isdisjoint(beta_ids), (alpha, beta, histories)
        assert alpha_ids <= {doc_ids[0]} and beta_ids <= {doc_ids[1]}, (alpha, beta, histories)
        assert all(chunk.get("kb_id") == dataset_id for result in (alpha, beta) for chunk in result.get("chunks") or []), (alpha, beta, histories)
        for doc_id in doc_ids:
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id) > 0, (
                doc_id,
                alpha,
                beta,
                histories,
            )
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p0
def test_tc_e2e_203_reparse_replaces_old_chunks_without_orphans(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-reparse-{run_id}")
        file_path = _asset_path(tmp_path, "basic.txt", run_id)
        uploaded = _payload_data(_api_multipart_upload(page, base_url, dataset_id, auth_header, file_path))
        doc = uploaded[0] if isinstance(uploaded, list) else uploaded
        doc_id = doc["id"]
        _update_document(
            page,
            base_url,
            auth_header,
            dataset_id,
            doc_id,
            {"chunk_method": "naive", "parser_config": {"chunk_token_num": 1, "delimiter": "\n"}},
        )
        _parse_documents(page, base_url, auth_header, dataset_id, [doc_id])
        completed, _ = _wait_doc_done(page, base_url, dataset_id, auth_header, file_path.name)
        old_rows = _db_doc_rows(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
        old_ids = {row["id"] for row in old_rows}
        assert old_ids and completed
        assert len(old_ids) >= 2, old_rows
        _update_document(page, base_url, auth_header, dataset_id, doc_id, {"parser_config": {"chunk_token_num": 1, "delimiter": "@@@"}})
        _parse_documents(page, base_url, auth_header, dataset_id, [doc_id])
        deadline = time.monotonic() + PARSE_TIMEOUT_MS / 1000
        history = []
        while time.monotonic() < deadline:
            parsed = _find_doc_by_name(_list_docs(page, base_url, dataset_id, auth_header), file_path.name)
            history.append(parsed)
            state = str(parsed.get("run") or parsed.get("status") or "").upper()
            if state in {"4", "FAIL", "FAILED"}:
                raise AssertionError((dataset_id, doc_id, run_id, history))
            new_rows = _db_doc_rows(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
            new_ids = {row["id"] for row in new_rows}
            if (state in {"3", "DONE", "DONE.VALUE", "SUCCESS"} or float(parsed.get("progress") or 0) >= 1) and new_ids and old_ids.isdisjoint(new_ids):
                break
            time.sleep(2)
        else:
            raise AssertionError((dataset_id, doc_id, run_id, history))
        new_ids = {row["id"] for row in new_rows}
        current_count = parsed.get("chunk_count", parsed.get("chunk_num"))
        assert isinstance(current_count, int) and current_count == len(new_rows), (parsed, history)
        assert old_ids.isdisjoint(new_ids)
        merged_content = "\n".join(str(row["content_with_weight"]) for row in new_rows)
        for fact in (
            f"GaussDB E2E Shenzhen audit {run_id}",
            "Elena Brooks",
            "Atlas-A17",
            "4 C",
            "calibration drift stayed below 0.2 C",
            f"This audit record belongs to {run_id}",
        ):
            assert fact in merged_content
        assert len({(row["kb_id"], row["id"]) for row in new_rows}) == len(new_rows)
        search = _search_dataset(page, base_url, auth_header, dataset_id, f"GaussDB E2E Shenzhen audit {run_id}")
        search_ids = {chunk.get("id") or chunk.get("chunk_id") for chunk in search.get("chunks") or []}
        assert search_ids and search_ids <= new_ids
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p0
def test_tc_e2e_204_mother_chunk_is_invalid_for_vector_retrieval(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-mother-{run_id}")
        parser_config = {"chunk_token_num": 512, "delimiter": "@@@", "parent_child": {"use_parent_child": True, "children_delimiter": "\n"}}
        _update_dataset(page, base_url, auth_header, dataset_id, {"parser_config": parser_config})
        file_path = _asset_path(tmp_path, "mother.md", run_id)
        uploaded = _payload_data(_api_multipart_upload(page, base_url, dataset_id, auth_header, file_path))
        doc = uploaded[0] if isinstance(uploaded, list) else uploaded
        doc_id = doc["id"]
        _parse_documents(page, base_url, auth_header, dataset_id, [doc_id])
        _wait_doc_done(page, base_url, dataset_id, auth_header, file_path.name)
        rows = _db_rows_by_columns(gaussdb_read_conn, schema, tenant_id, dataset_id, ["id", "doc_id", "mom_id"], doc_id)
        mother_ids = {row["id"] for row in rows if not row.get("mom_id")}
        child_ids = {row["id"] for row in rows if row.get("mom_id")}
        assert mother_ids and child_ids, rows
        valid = _db_vector_valid_by_id(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
        assert all(valid[mother_id] is False for mother_id in mother_ids)
        assert all(valid[child_id] is True for child_id in child_ids)
        result = _search_dataset(
            page,
            base_url,
            auth_header,
            dataset_id,
            "Atlas-A17 calibration Maya Chen vaccine route",
            vector_similarity_weight=1.0,
        )
        result_ids = {chunk.get("id") or chunk.get("chunk_id") for chunk in result.get("chunks") or []}
        assert result_ids and result_ids <= mother_ids
        assert result_ids.isdisjoint(child_ids)
        assert all(chunk.get("vector_similarity") is not None for chunk in result.get("chunks") or [])
        _assert_no_vector_fields(result)
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p0
def test_tc_e2e_205_embedding_check_skips_invalid_mother_chunk(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-embedding-check-{run_id}")
        parser_config = {"chunk_token_num": 512, "delimiter": "@@@", "parent_child": {"use_parent_child": True, "children_delimiter": "\n"}}
        _update_dataset(page, base_url, auth_header, dataset_id, {"parser_config": parser_config})
        file_path = _asset_path(tmp_path, "mother.md", run_id)
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, [file_path])
        doc_id = uploaded[0]["id"]
        rows = _db_rows_by_columns(gaussdb_read_conn, schema, tenant_id, dataset_id, ["id", "mom_id"], doc_id)
        mother_ids = {row["id"] for row in rows if not row.get("mom_id")}
        child_ids = {row["id"] for row in rows if row.get("mom_id")}
        assert mother_ids and child_ids, rows
        _, before = _db_vector_valid_state(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
        assert all(not before[chunk_id] for chunk_id in mother_ids)
        assert all(before[chunk_id] for chunk_id in child_ids)
        models = _payload_data(_api_json(page, "GET", base_url, "/api/v1/users/me/models", auth_header))
        embd_id = models.get("embd_id") or models.get("embedding_model")
        assert embd_id, models
        checked = _payload_data(
            _api_json(
                page,
                "POST",
                base_url,
                f"/api/v1/datasets/{dataset_id}/embedding/check",
                auth_header,
                {"embd_id": embd_id, "check_num": len(rows)},
                timeout_ms=PARSE_TIMEOUT_MS,
            )
        )
        results = checked.get("results") or []
        by_id = {item.get("chunk_id"): item for item in results}
        assert set(by_id) == child_ids and mother_ids.isdisjoint(by_id), checked
        assert all(by_id[chunk_id].get("vector_dim", 0) > 0 and "cos_sim" in by_id[chunk_id] for chunk_id in child_ids)
        assert checked.get("summary", {}).get("sampled") == len(child_ids)
        assert checked.get("summary", {}).get("valid") == len(child_ids)
        assert _db_vector_valid_by_id(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id) == before
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p0
def test_tc_e2e_206_manual_chunk_crud_round_trips_persisted_fields(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-manual-chunk-{run_id}")
        file_path = _asset_path(tmp_path, "basic.txt", run_id)
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, [file_path])
        doc_id = uploaded[0]["id"]
        original_count = _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
        first = _add_chunk(
            page,
            base_url,
            auth_header,
            dataset_id,
            doc_id,
            f"MANUAL_OLD_{run_id}",
            important_keywords=[f"manual-{run_id}"],
            questions=[f"Where is manual chunk {run_id}?"],
            tag_kwd=["before"],
        )
        second = _add_chunk(page, base_url, auth_header, dataset_id, doc_id, f"MANUAL_SECOND_{run_id}")
        first_id, second_id = first["id"], second["id"]
        after_add_doc = _find_doc_by_name(_list_docs(page, base_url, dataset_id, auth_header), file_path.name)
        assert after_add_doc.get("chunk_count", after_add_doc.get("chunk_num")) == original_count + 2
        assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id) == original_count + 2
        _update_chunk(
            page,
            base_url,
            auth_header,
            dataset_id,
            doc_id,
            first_id,
            {"content": f"MANUAL_UPDATED_{run_id}", "positions": [[1, 0, 0, 20, 30]], "tag_kwd": ["after"]},
        )
        _update_chunk(page, base_url, auth_header, dataset_id, doc_id, second_id, {"positions": [[1, 0, 0, 10, 20]]})
        rows = _db_rows_by_columns(
            gaussdb_read_conn,
            schema,
            tenant_id,
            dataset_id,
            ["id", "content_with_weight", "available_int", "position_int", "tag_kwd"],
            doc_id,
        )
        by_id = {row["id"]: row for row in rows}
        assert by_id[first_id]["content_with_weight"] == f"MANUAL_UPDATED_{run_id}"
        assert by_id[first_id]["position_int"] == [[1, 0, 0, 20, 30]] and by_id[first_id]["tag_kwd"] == ["after"]
        assert by_id[second_id]["position_int"] == [[1, 0, 0, 10, 20]]
        _, vector_state = _db_vector_valid_state(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
        assert {first_id, second_id} <= set(vector_state)
        assert vector_state[first_id] and vector_state[second_id]
        listed = {chunk.get("id"): chunk for chunk in _list_chunks(page, base_url, auth_header, dataset_id, doc_id).get("chunks") or []}
        assert listed[first_id]["positions"] == [[1, 0, 0, 20, 30]], listed
        assert listed[second_id]["positions"] == [[1, 0, 0, 10, 20]], listed
        old = _search_dataset(page, base_url, auth_header, dataset_id, f"MANUAL_OLD_{run_id}")
        assert all(f"MANUAL_OLD_{run_id}" not in str(chunk.get("content") or chunk.get("content_with_weight") or "") for chunk in old.get("chunks") or [])
        updated = _search_dataset(page, base_url, auth_header, dataset_id, f"MANUAL_UPDATED_{run_id}")
        updated_by_id = {chunk.get("id") or chunk.get("chunk_id"): chunk for chunk in updated.get("chunks") or []}
        assert f"MANUAL_UPDATED_{run_id}" in str(updated_by_id[first_id].get("content") or updated_by_id[first_id].get("content_with_weight") or "")
        _update_chunk(page, base_url, auth_header, dataset_id, doc_id, first_id, {"available": False})
        disabled = _db_rows_by_columns(gaussdb_read_conn, schema, tenant_id, dataset_id, ["id", "available_int"], doc_id)
        assert {row["id"]: row["available_int"] for row in disabled}[first_id] == 0
        hidden = _search_dataset(page, base_url, auth_header, dataset_id, f"MANUAL_UPDATED_{run_id}")
        assert first_id not in {chunk.get("id") or chunk.get("chunk_id") for chunk in hidden.get("chunks") or []}
        _update_chunk(page, base_url, auth_header, dataset_id, doc_id, first_id, {"available": True})
        reenabled = _db_rows_by_columns(gaussdb_read_conn, schema, tenant_id, dataset_id, ["id", "available_int"], doc_id)
        assert {row["id"]: row["available_int"] for row in reenabled}[first_id] == 1
        visible = _search_dataset(
            page,
            base_url,
            auth_header,
            dataset_id,
            f"MANUAL_UPDATED_{run_id}",
            vector_similarity_weight=1.0,
        )
        assert first_id in {chunk.get("id") or chunk.get("chunk_id") for chunk in visible.get("chunks") or []}
        _delete_chunks(page, base_url, auth_header, dataset_id, doc_id, [first_id, second_id])
        remaining = _db_rows_by_columns(gaussdb_read_conn, schema, tenant_id, dataset_id, ["id"], doc_id)
        assert {first_id, second_id}.isdisjoint({row["id"] for row in remaining})
        after_delete = _list_chunks(page, base_url, auth_header, dataset_id, doc_id)
        assert {first_id, second_id}.isdisjoint({chunk.get("id") for chunk in after_delete.get("chunks") or []})
        listed_doc = _find_doc_by_name(_list_docs(page, base_url, dataset_id, auth_header), file_path.name)
        assert listed_doc.get("chunk_count", listed_doc.get("chunk_num")) == len(remaining)
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p0
def test_tc_e2e_207_document_disable_and_reenable_preserves_chunks(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-document-status-{run_id}")
        file_path = _asset_path(tmp_path, "basic.txt", run_id)
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, [file_path])
        doc_id = uploaded[0]["id"]
        before_rows = _db_rows_by_columns(gaussdb_read_conn, schema, tenant_id, dataset_id, ["id", "available_int"], doc_id)
        before_ids = {row["id"] for row in before_rows}
        assert before_ids and len(before_rows) == len(before_ids) and all(row["available_int"] == 1 for row in before_rows)
        query = f"GaussDB E2E Shenzhen audit {run_id}"
        assert doc_id in {chunk.get("doc_id") or chunk.get("document_id") for chunk in _search_dataset(page, base_url, auth_header, dataset_id, query).get("chunks") or []}
        _set_document_status(page, base_url, auth_header, dataset_id, doc_id, False)
        disabled_rows = _db_rows_by_columns(gaussdb_read_conn, schema, tenant_id, dataset_id, ["id", "available_int"], doc_id)
        assert len(disabled_rows) == len(before_ids)
        assert {row["id"] for row in disabled_rows} == before_ids and all(row["available_int"] == 0 for row in disabled_rows)
        hidden = _search_dataset(page, base_url, auth_header, dataset_id, query)
        assert doc_id not in {chunk.get("doc_id") or chunk.get("document_id") for chunk in hidden.get("chunks") or []}
        assert str(_find_doc_by_name(_list_docs(page, base_url, dataset_id, auth_header), file_path.name).get("status")) == "0"
        _set_document_status(page, base_url, auth_header, dataset_id, doc_id, True)
        enabled_rows = _db_rows_by_columns(gaussdb_read_conn, schema, tenant_id, dataset_id, ["id", "available_int"], doc_id)
        assert len(enabled_rows) == len(before_ids)
        assert {row["id"] for row in enabled_rows} == before_ids and all(row["available_int"] == 1 for row in enabled_rows)
        assert str(_find_doc_by_name(_list_docs(page, base_url, dataset_id, auth_header), file_path.name).get("status")) == "1"
        visible = _search_dataset(page, base_url, auth_header, dataset_id, query)
        assert doc_id in {chunk.get("doc_id") or chunk.get("document_id") for chunk in visible.get("chunks") or []}
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p0
def test_tc_e2e_401_fulltext_search_returns_scoped_document(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-fulltext-{run_id}")
        files = [_asset_path(tmp_path, "batch_a.txt", run_id), _asset_path(tmp_path, "batch_b.txt", run_id)]
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, files)
        doc_ids = [doc["id"] for doc in uploaded]
        result = _search_dataset(page, base_url, auth_header, dataset_id, "alpha", vector_similarity_weight=0.0, similarity_threshold=0.000001)
        result_ids = {chunk.get("doc_id") or chunk.get("document_id") for chunk in result.get("chunks") or []}
        assert doc_ids[0] in result_ids and doc_ids[1] not in result_ids
        assert all(chunk.get("kb_id") == dataset_id for chunk in result.get("chunks") or [])
        assert {row["doc_id"] for row in _db_doc_rows(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_ids[0])} == {doc_ids[0]}
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p0
def test_tc_e2e_402_vector_search_returns_only_valid_vectors(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-vector-{run_id}")
        files = [_asset_path(tmp_path, "batch_a.txt", run_id), _asset_path(tmp_path, "batch_b.txt", run_id)]
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, files)
        result = _search_dataset(page, base_url, auth_header, dataset_id, f"batch alpha {run_id}", vector_similarity_weight=1.0)
        chunks = result.get("chunks") or []
        assert chunks
        assert uploaded[0]["id"] in {chunk.get("doc_id") or chunk.get("document_id") for chunk in chunks}
        for chunk in chunks:
            doc_id = chunk.get("doc_id") or chunk.get("document_id")
            assert doc_id in {doc["id"] for doc in uploaded}
            vector_valid = _db_vector_valid(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
            assert vector_valid and all(vector_valid)
        _assert_no_vector_fields(result)
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p0
def test_tc_e2e_403_hybrid_weight_changes_scores_without_cross_kb_results(
    request, page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-hybrid-{run_id}")
        files = [_asset_path(tmp_path, "batch_a.txt", run_id), _asset_path(tmp_path, "batch_b.txt", run_id)]
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, files)
        expected_doc_ids = {doc["id"] for doc in uploaded}
        models = _payload_data(_api_json(page, "GET", base_url, "/api/v1/users/me/models", auth_header))
        embedding_model = models.get("embd_id") or models.get("embedding_model")
        assert embedding_model, models
        request.node.user_properties.append(("embedding_model", embedding_model))
        results = [_search_dataset(page, base_url, auth_header, dataset_id, "alpha cold-chain disruption", vector_similarity_weight=weight) for weight in (0.0, 0.5, 1.0)]
        score_maps = [{(chunk.get("kb_id"), chunk.get("id") or chunk.get("chunk_id")): chunk.get("similarity") for chunk in result.get("chunks") or []} for result in results]
        assert all(result.get("chunks") for result in results)
        assert all({_public_doc_id(chunk) for chunk in result["chunks"]} == expected_doc_ids for result in results), results
        assert all(all(key[0] == dataset_id for key in scores) for scores in score_maps)
        common = set(score_maps[0]) & set(score_maps[-1])
        assert common and any(score_maps[0][key] != score_maps[-1][key] for key in common), score_maps
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p1
def test_tc_e2e_404_threshold_topk_and_pagination_are_consistent(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-page-{run_id}")
        file_path = _asset_path(tmp_path, "basic.txt", run_id)
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, [file_path])
        doc_id = uploaded[0]["id"]
        token = f"PAGINATION_TOKEN_{run_id}"
        manual_ids = {_add_chunk(page, base_url, auth_header, dataset_id, doc_id, f"{token} deterministic item {index:02d}")["id"] for index in range(12)}
        assert len(manual_ids) == 12
        count = _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
        assert count >= 13
        page1 = _search_dataset(page, base_url, auth_header, dataset_id, token, page=1, size=5, top_k=12, similarity_threshold=0.000001)
        page2 = _search_dataset(page, base_url, auth_header, dataset_id, token, page=2, size=5, top_k=12, similarity_threshold=0.000001)
        page3 = _search_dataset(page, base_url, auth_header, dataset_id, token, page=3, size=5, top_k=12, similarity_threshold=0.000001)
        ids1 = {chunk.get("id") or chunk.get("chunk_id") for chunk in page1.get("chunks") or []}
        ids2 = {chunk.get("id") or chunk.get("chunk_id") for chunk in page2.get("chunks") or []}
        ids3 = {chunk.get("id") or chunk.get("chunk_id") for chunk in page3.get("chunks") or []}
        assert len(ids1) == len(ids2) == 5 and len(ids3) == 2
        assert (page1.get("total"), page2.get("total"), page3.get("total")) == (5, 5, 2)
        assert ids1.isdisjoint(ids2) and ids1.isdisjoint(ids3) and ids2.isdisjoint(ids3)
        assert ids1 | ids2 | ids3 == manual_ids
        low = _search_dataset(page, base_url, auth_header, dataset_id, token, top_k=count, size=count, similarity_threshold=0.0)
        high = _search_dataset(page, base_url, auth_header, dataset_id, token, top_k=count, size=count, similarity_threshold=0.99)
        assert {(c.get("id") or c.get("chunk_id")) for c in high.get("chunks") or []} <= {(c.get("id") or c.get("chunk_id")) for c in low.get("chunks") or []}
        response, payload = _api_response(page, "POST", base_url, f"/api/v1/datasets/{dataset_id}/search", auth_header, {"question": "invalid", "top_k": 0, "page": 1, "size": 5})
        _assert_api_error(response, payload, code=101)
        assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id) == count
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p0
def test_tc_e2e_405_doc_ids_omitted_empty_and_explicit_scope_are_consistent(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-doc-scope-{run_id}")
        files = [_asset_path(tmp_path, "batch_a.txt", run_id), _asset_path(tmp_path, "batch_b.txt", run_id)]
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, files)
        expected = {doc["id"] for doc in uploaded}
        base_payload = {
            "question": f"batch {run_id}",
            "top_k": 10,
            "page": 1,
            "size": 10,
            "similarity_threshold": 0.0,
            "vector_similarity_weight": 0.0,
        }
        omitted = _payload_data(_api_json(page, "POST", base_url, f"/api/v1/datasets/{dataset_id}/search", auth_header, dict(base_payload)))
        empty = _payload_data(_api_json(page, "POST", base_url, f"/api/v1/datasets/{dataset_id}/search", auth_header, {**base_payload, "doc_ids": []}))
        explicit = _payload_data(
            _api_json(
                page,
                "POST",
                base_url,
                f"/api/v1/datasets/{dataset_id}/search",
                auth_header,
                {**base_payload, "doc_ids": [uploaded[0]["id"]]},
            )
        )

        def doc_ids(result):
            return {chunk.get("doc_id") or chunk.get("document_id") for chunk in result.get("chunks") or []}

        assert doc_ids(omitted) == expected and doc_ids(empty) == expected, (omitted, empty)
        assert doc_ids(explicit) == {uploaded[0]["id"]}, explicit
        assert omitted.get("total") == empty.get("total")
        assert all(chunk.get("kb_id") == dataset_id for result in (omitted, empty, explicit) for chunk in result.get("chunks") or [])
        assert {row["doc_id"] for row in _db_doc_rows(gaussdb_read_conn, schema, tenant_id, dataset_id)} == expected
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p0
def test_tc_e2e_406_utf8_chinese_fulltext_search_and_highlight(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = chat_id = session_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-zh-{run_id}")
        files = [_asset_path(tmp_path, "unicode_zh.txt", run_id), _asset_path(tmp_path, "unicode_zh_canary.txt", run_id)]
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, files)
        target_id, canary_id = uploaded[0]["id"], uploaded[1]["id"]
        expected_names = {doc["id"]: path.name for doc, path in zip(uploaded, files)}
        expected_meta = {
            target_id: {"language_profile": "zh", "role": "target"},
            canary_id: {"language_profile": "zh", "role": "canary"},
        }
        for doc_id, meta_fields in expected_meta.items():
            _update_document(page, base_url, auth_header, dataset_id, doc_id, {"meta_fields": meta_fields})
        assert _db_meta_fields(gaussdb_read_conn, schema, tenant_id, dataset_id, [target_id, canary_id]) == expected_meta
        result = _search_dataset(
            page,
            base_url,
            auth_header,
            dataset_id,
            "苍穹冷却泵",
            vector_similarity_weight=0.0,
            similarity_threshold=0.000001,
        )
        _assert_search_sources(
            result,
            gaussdb_read_conn,
            schema,
            tenant_id,
            dataset_id,
            expected_names,
            exact_doc_ids={target_id},
        )
        highlighted = _list_chunks(page, base_url, auth_header, dataset_id, target_id, keywords="苍穹冷却泵")
        serialized = json.dumps(highlighted, ensure_ascii=False).lower()
        assert "<em>" in serialized and "苍穹" in serialized, highlighted
        rows = _db_doc_rows(gaussdb_read_conn, schema, tenant_id, dataset_id, target_id)
        content = "\n".join(str(row["content_with_weight"]) for row in rows)
        assert "苍穹冷却泵" in content and "每分钟48升" in content
        assert "单引号'" in content and "百分号%" in content and "下划线_" in content and "反斜杠\\" in content
        chat_id = _create_chat(page, base_url, auth_header, f"zh-chat-{run_id}", [dataset_id])
        session_id = _create_session(page, base_url, auth_header, chat_id, f"zh-session-{run_id}")
        answer = _complete_chat(
            page,
            base_url,
            auth_header,
            chat_id,
            session_id,
            "2026年4月18日，林岚在苏州南岭冷链中心复核的设备是什么，稳定流量是多少？只回答正式巡检记录。",
            f"q-{run_id}",
        )
        compact_answer = re.sub(r"\s+", "", answer["answer"])
        assert "苍穹冷却泵" in compact_answer and re.search(r"(?:每分钟48升|48升/分钟)", compact_answer), answer
        assert "星河冷却泵" not in compact_answer and not re.search(r"(?:每分钟42升|42升/分钟)", compact_answer), answer
        _assert_chat_sources(answer, gaussdb_read_conn, schema, tenant_id, dataset_id, expected_names, target_id)
    finally:
        _cleanup_chat_and_datasets(
            page,
            base_url,
            auth_header,
            chat_id,
            session_id,
            gaussdb_read_conn,
            schema,
            tenant_id,
            [dataset_id],
        )


@pytest.mark.p0
def test_tc_e2e_407_multidataset_search_returns_only_requested_kbs(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_ids = []
    try:
        for suffix in ("a", "b", "canary"):
            dataset_ids.append(_create_dataset(page, base_url, auth_header, f"gaussdb-e2e-multisearch-{suffix}-{run_id}"))
        documents = []
        manual_chunks = []
        token = f"MULTIKB_SHARED_{run_id}"
        for index, dataset_id in enumerate(dataset_ids):
            file_path = _asset_path(tmp_path, "basic.txt", run_id, output_name=f"multisearch-{index}-{run_id}.txt")
            uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, [file_path])
            doc_id = uploaded[0]["id"]
            documents.append(doc_id)
            manual_chunks.append(_add_chunk(page, base_url, auth_header, dataset_id, doc_id, f"{token} source {index}")["id"])
        result = _search_datasets(page, base_url, auth_header, dataset_ids[:2], token, size=20, top_k=20)
        result_pairs = {
            (chunk.get("doc_id") or chunk.get("document_id"), chunk.get("kb_id")) for chunk in result.get("chunks") or [] if (chunk.get("id") or chunk.get("chunk_id")) in set(manual_chunks)
        }
        assert result_pairs == {(documents[0], dataset_ids[0]), (documents[1], dataset_ids[1])}, result
        assert all(chunk.get("kb_id") in set(dataset_ids[:2]) for chunk in result.get("chunks") or [])
        assert set(_db_chunk_locations(gaussdb_read_conn, schema, tenant_id, manual_chunks)) == {
            (chunk_id, doc_id, dataset_id) for chunk_id, doc_id, dataset_id in zip(manual_chunks, documents, dataset_ids)
        }
        scoped = _search_datasets(page, base_url, auth_header, dataset_ids[:2], token, doc_ids=[documents[0]], size=20, top_k=20)
        assert {chunk.get("doc_id") or chunk.get("document_id") for chunk in scoped.get("chunks") or []} == {documents[0]}, scoped
        for dataset_id, doc_id in zip(dataset_ids, documents):
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id) >= 2
    finally:
        _cleanup_chat_and_datasets(page, base_url, auth_header, None, None, gaussdb_read_conn, schema, tenant_id, dataset_ids)


@pytest.mark.p0
def test_tc_e2e_408_mixed_language_search_and_chat_require_combined_terms(
    page,
    base_url,
    login_url,
    active_auth_context,
    auth_click,
    seeded_user_credentials,
    ensure_model_provider_configured,
    gaussdb_read_conn,
    run_id,
    tmp_path,
):
    auth_header, tenant_id, schema = _e2e_context(
        page,
        base_url,
        login_url,
        active_auth_context,
        auth_click,
        seeded_user_credentials,
        ensure_model_provider_configured,
        gaussdb_read_conn,
    )
    dataset_id = chat_id = session_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-mixed-{run_id}")
        files = [
            _asset_path(tmp_path, "mixed_target.txt", run_id),
            _asset_path(tmp_path, "mixed_zh_canary.txt", run_id),
            _asset_path(tmp_path, "mixed_en_canary.txt", run_id),
        ]
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, files)
        target_id, zh_canary_id, en_canary_id = [doc["id"] for doc in uploaded]
        expected_names = {doc["id"]: path.name for doc, path in zip(uploaded, files)}
        expected_meta = {
            target_id: {"language_profile": "mixed", "role": "target"},
            zh_canary_id: {"language_profile": "zh", "role": "canary"},
            en_canary_id: {"language_profile": "en", "role": "canary"},
        }
        for doc_id, meta_fields in expected_meta.items():
            _update_document(page, base_url, auth_header, dataset_id, doc_id, {"meta_fields": meta_fields})
        assert _db_meta_fields(gaussdb_read_conn, schema, tenant_id, dataset_id, list(expected_names)) == expected_meta

        search = _search_dataset(
            page,
            base_url,
            auth_header,
            dataset_id,
            "星桥机器人 EdgePilot X7 SLA 99.95% 38ms",
            vector_similarity_weight=0.0,
            similarity_threshold=0.000001,
        )
        _assert_search_sources(
            search,
            gaussdb_read_conn,
            schema,
            tenant_id,
            dataset_id,
            expected_names,
            exact_doc_ids={target_id},
        )

        chat_id = _create_chat(page, base_url, auth_header, f"mixed-chat-{run_id}", [dataset_id])
        session_id = _create_session(page, base_url, auth_header, chat_id, f"mixed-session-{run_id}")
        answer = _complete_chat(
            page,
            base_url,
            auth_header,
            chat_id,
            session_id,
            "星桥机器人为 EdgePilot X7 设定的 SLA 和 latency 告警阈值分别是多少？为什么采用该阈值？",
            f"q-{run_id}",
        )
        compact_answer = re.sub(r"\s+", "", answer["answer"])
        for value in ("星桥机器人", "EdgePilotX7", "99.95%"):
            assert value in compact_answer, answer
        assert "38ms" in compact_answer or "38毫秒" in compact_answer, answer
        assert "视觉网关" in compact_answer and "双链路" in compact_answer, answer
        assert re.search(
            r"(?:因为|由于|原因是[：:]?).{0,60}视觉网关.{0,60}(?:完成|实现).{0,20}双链路切换",
            compact_answer,
        ), answer
        assert "未完成双链路" not in compact_answer and "单链路切换" not in compact_answer, answer
        assert "99.50%" not in compact_answer and "83ms" not in compact_answer and "83毫秒" not in compact_answer, answer
        _assert_chat_sources(answer, gaussdb_read_conn, schema, tenant_id, dataset_id, expected_names, target_id)
    finally:
        _cleanup_chat_and_datasets(
            page,
            base_url,
            auth_header,
            chat_id,
            session_id,
            gaussdb_read_conn,
            schema,
            tenant_id,
            [dataset_id],
        )


@pytest.mark.p0
def test_tc_e2e_501_table_chat_returns_markdown_and_references(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = chat_id = session_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-table-{run_id}", "table")
        file_path = _asset_path(tmp_path, "table.csv", run_id)
        uploaded, completed, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, [file_path], {"table_column_mode": "auto"})
        assert uploaded
        doc_id = uploaded[0]["id"]
        assert completed[doc_id]
        parser_config = _get_dataset(page, base_url, auth_header, dataset_id).get("parser_config") or {}
        field_map = parser_config.get("field_map") or {}
        assert field_map == {"amount": "Amount", "date": "Date", "description": "Description"}, parser_config
        assert set(_db_table_amounts(gaussdb_read_conn, schema, tenant_id, dataset_id)) == {80, 120, 150, 250, 300}
        chat_id = _create_chat(page, base_url, auth_header, f"sql-{run_id}", [dataset_id])
        session_id = _create_session(page, base_url, auth_header, chat_id, f"sql-session-{run_id}")
        result = _complete_chat(page, base_url, auth_header, chat_id, session_id, "列出 Amount 大于 200 的行", f"q-{run_id}")
        answer = result["answer"]
        assert "|" in answer and re.search(r"\|\s*:?-{3,}", answer), answer
        assert all(value in answer for value in ("250", "300"))
        assert all(value not in answer for value in ("120", "80", "150"))
        chunks = result["reference"].get("chunks") or []
        db_names = {row["docnm_kwd"] for row in _db_doc_rows(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)}
        assert db_names == {file_path.name}
        assert chunks and all(chunk.get("document_id") == doc_id and chunk.get("document_name") in db_names and chunk.get("dataset_id") == dataset_id for chunk in chunks)
        assert set(_db_table_amounts(gaussdb_read_conn, schema, tenant_id, dataset_id, minimum=200)) == {250, 300}
    finally:
        _cleanup_chat_and_datasets(page, base_url, auth_header, chat_id, session_id, gaussdb_read_conn, schema, tenant_id, [dataset_id])


@pytest.mark.p0
def test_tc_e2e_502_multikb_chat_references_have_correct_kb_ids(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_ids = []
    expected_doc_to_kb = {}
    expected_doc_to_name = {}
    chat_id = session_id = None
    try:
        for name, asset in (("a", "multikb_a.csv"), ("b", "multikb_b.csv")):
            dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-multikb-{name}-{run_id}", "table")
            dataset_ids.append(dataset_id)
            uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, [_asset_path(tmp_path, asset, run_id)], {"table_column_mode": "auto"})
            doc_id = uploaded[0]["id"]
            rows = _db_doc_rows(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
            assert rows and {row["kb_id"] for row in rows} == {dataset_id}
            serialized_rows = json.dumps(rows, ensure_ascii=False, default=str)
            expected_source = f"KB_{name.upper()}_{run_id}"
            expected_amount = "111" if name == "a" else "222"
            assert expected_source in serialized_rows and expected_amount in serialized_rows
            expected_doc_to_kb[doc_id] = dataset_id
            expected_doc_to_name[doc_id] = asset
        chat_id = _create_chat(page, base_url, auth_header, f"multikb-{run_id}", dataset_ids)
        session_id = _create_session(page, base_url, auth_header, chat_id, f"multikb-session-{run_id}")
        result = _complete_chat(page, base_url, auth_header, chat_id, session_id, f"列出 Source 和 Amount，必须同时包含 KB_A_{run_id} 与 KB_B_{run_id}", f"q-{run_id}")
        assert all(value in result["answer"] for value in (f"KB_A_{run_id}", "111", f"KB_B_{run_id}", "222"))
        answer_lines = [re.sub(r"\s+", " ", line) for line in result["answer"].splitlines() if line.strip()]
        assert any(f"KB_A_{run_id}" in line and re.search(r"(?<!\d)111(?!\d)", line) for line in answer_lines), result
        assert any(f"KB_B_{run_id}" in line and re.search(r"(?<!\d)222(?!\d)", line) for line in answer_lines), result
        assert not any(f"KB_A_{run_id}" in line and re.search(r"(?<!\d)222(?!\d)", line) for line in answer_lines), result
        assert not any(f"KB_B_{run_id}" in line and re.search(r"(?<!\d)111(?!\d)", line) for line in answer_lines), result
        refs = result["reference"].get("chunks") or []
        actual_doc_to_kb = {chunk.get("document_id"): chunk.get("dataset_id") for chunk in refs}
        assert actual_doc_to_kb == expected_doc_to_kb
        assert {chunk.get("document_id"): chunk.get("document_name") for chunk in refs} == expected_doc_to_name
    finally:
        _cleanup_chat_and_datasets(page, base_url, auth_header, chat_id, session_id, gaussdb_read_conn, schema, tenant_id, dataset_ids)


@pytest.mark.p1
def test_tc_e2e_503_count_query_returns_exact_answer_and_sources(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = chat_id = session_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-count-{run_id}", "table")
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, [_asset_path(tmp_path, "table.csv", run_id)], {"table_column_mode": "auto"})
        doc_id = uploaded[0]["id"]
        chat_id = _create_chat(page, base_url, auth_header, f"count-{run_id}", [dataset_id])
        session_id = _create_session(page, base_url, auth_header, chat_id, f"count-session-{run_id}")
        result = _complete_chat(page, base_url, auth_header, chat_id, session_id, "表格共有多少条数据行？不要把表头计入", f"q-{run_id}")
        assert re.search(r"(?<!\d)5\s*(?:条|行|rows?)", result["answer"], re.I), result
        refs = result["reference"]
        chunks = refs.get("chunks") or []
        doc_aggs = refs.get("doc_aggs") or []
        assert chunks or doc_aggs, refs
        assert all(item.get("dataset_id") == dataset_id and item.get("document_id") == doc_id for item in chunks)
        assert all(item.get("doc_id") == doc_id and item.get("doc_name") == "table.csv" for item in doc_aggs)
        db_rows = _db_doc_rows(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
        assert len([row for row in db_rows if row.get("chunk_data")]) == 5
        assert len(_db_table_amounts(gaussdb_read_conn, schema, tenant_id, dataset_id)) == 5
    finally:
        _cleanup_chat_and_datasets(page, base_url, auth_header, chat_id, session_id, gaussdb_read_conn, schema, tenant_id, [dataset_id])


@pytest.mark.p0
def test_tc_e2e_504_naive_chat_returns_grounded_answer_and_reference(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = chat_id = session_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-chat-{run_id}")
        files = [_asset_path(tmp_path, "basic.txt", run_id), _asset_path(tmp_path, "basic_canary.txt", run_id)]
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, files)
        target_id, canary_id = uploaded[0]["id"], uploaded[1]["id"]
        expected_names = {doc["id"]: path.name for doc, path in zip(uploaded, files)}
        expected_meta = {
            target_id: {"language_profile": "en", "role": "target"},
            canary_id: {"language_profile": "en", "role": "canary"},
        }
        for doc_id, meta_fields in expected_meta.items():
            _update_document(page, base_url, auth_header, dataset_id, doc_id, {"meta_fields": meta_fields})
        assert _db_meta_fields(gaussdb_read_conn, schema, tenant_id, dataset_id, [target_id, canary_id]) == expected_meta
        search = _search_dataset(
            page,
            base_url,
            auth_header,
            dataset_id,
            "Elena Brooks Atlas-A17 4 C",
            vector_similarity_weight=0.0,
            similarity_threshold=0.000001,
        )
        _assert_search_sources(
            search,
            gaussdb_read_conn,
            schema,
            tenant_id,
            dataset_id,
            expected_names,
            exact_doc_ids={target_id},
        )
        chat_id = _create_chat(page, base_url, auth_header, f"naive-chat-{run_id}", [dataset_id])
        session_id = _create_session(page, base_url, auth_header, chat_id, f"naive-session-{run_id}")
        result = _complete_chat(
            page,
            base_url,
            auth_header,
            chat_id,
            session_id,
            "Which sensor model and maximum transit temperature did Elena Brooks approve on 14 March 2026 for the Shenzhen vaccine route?",
            f"q-{run_id}",
        )
        compact_answer = re.sub(r"\s+", "", result["answer"])
        assert re.search(r"Atlas-?A17", compact_answer, re.IGNORECASE) and re.search(r"4°?C", compact_answer, re.IGNORECASE), result
        assert not re.search(r"Helios-?H22", compact_answer, re.IGNORECASE) and not re.search(r"8°?C", compact_answer, re.IGNORECASE), result
        _assert_chat_sources(result, gaussdb_read_conn, schema, tenant_id, dataset_id, expected_names, target_id)
        db_rows = _db_doc_rows(gaussdb_read_conn, schema, tenant_id, dataset_id, target_id)
        assert any("Atlas-A17" in str(row["content_with_weight"]) and "4 C" in str(row["content_with_weight"]) for row in db_rows)
    finally:
        _cleanup_chat_and_datasets(page, base_url, auth_header, chat_id, session_id, gaussdb_read_conn, schema, tenant_id, [dataset_id])


@pytest.mark.p0
def test_tc_e2e_701_metadata_update_and_filter_full_chain(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-meta-{run_id}")
        states = [None, "", [], "active"]
        files = [_asset_path(tmp_path, "basic.txt", run_id, output_name=f"meta-{index}-{run_id}.txt") for index in range(len(states))]
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, files)
        doc_ids = [doc["id"] for doc in uploaded]
        for doc_id, state in zip(doc_ids[1:3], states[1:3]):
            _update_document(page, base_url, auth_header, dataset_id, doc_id, {"meta_fields": {"state": state}})
        _update_document(page, base_url, auth_header, dataset_id, doc_ids[3], {"meta_fields": {"state": "draft", "old_key": "remove"}})
        _update_document(page, base_url, auth_header, dataset_id, doc_ids[3], {"meta_fields": {"state": "active", "category": "blue"}})
        meta = _db_meta_fields(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_ids)
        assert "state" not in (meta.get(doc_ids[0]) or {})
        assert meta[doc_ids[1]] == {"state": ""}
        assert meta[doc_ids[2]] == {"state": []}
        assert meta[doc_ids[3]] == {"state": "active", "category": "blue"}
        base = {"dataset_ids": [dataset_id], "question": f"metadata {run_id}", "top_k": 20, "page": 1, "size": 20, "vector_similarity_weight": 0.0}
        cases = [
            ({"method": "manual", "manual": [{"key": "state", "op": "=", "value": "active"}]}, {doc_ids[3]}),
            ({"method": "manual", "manual": [{"key": "state", "op": "empty", "value": None}]}, {doc_ids[1], doc_ids[2]}),
            ({"method": "manual", "manual": [{"key": "state", "op": "not empty", "value": None}]}, {doc_ids[3]}),
            ({"method": "manual", "manual": [{"key": "state", "op": "=", "value": "active"}, {"key": "state", "op": "contains", "value": "act"}], "logic": "and"}, {doc_ids[3]}),
            ({"method": "manual", "manual": [{"key": "state", "op": "=", "value": "active"}, {"key": "state", "op": "empty", "value": None}], "logic": "or"}, set(doc_ids[1:])),
        ]
        for meta_filter, expected in cases:
            query = dict(base)
            query["meta_data_filter"] = meta_filter
            result = _payload_data(_api_json(page, "POST", base_url, "/api/v1/datasets/search", auth_header, query))
            actual = {chunk.get("doc_id") or chunk.get("document_id") for chunk in result.get("chunks") or []}
            assert actual == expected, (meta_filter, result)
            assert actual <= set(doc_ids)
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p1
def test_tc_e2e_801_message_feedback_updates_scoped_pagerank_once(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    assert os.getenv("CHUNK_FEEDBACK_ENABLED", "").lower() in {"1", "true", "yes", "on"}, "feedback profile must be enabled before E2E"
    assert os.getenv("CHUNK_FEEDBACK_WEIGHTING") == "uniform", "feedback profile must use uniform weighting"
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = canary_id = chat_id = session_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-feedback-{run_id}")
        canary_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-feedback-canary-{run_id}")
        main_file = _asset_path(tmp_path, "basic.txt", run_id)
        canary_file = _asset_path(tmp_path, "batch_b.txt", run_id, output_name=f"canary-{run_id}.txt")
        main_docs, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, [main_file])
        canary_docs, _, _ = _upload_and_parse_documents(page, base_url, auth_header, canary_id, [canary_file])
        chat_id = _create_chat(page, base_url, auth_header, f"feedback-{run_id}", [dataset_id])
        session_id = _create_session(page, base_url, auth_header, chat_id, f"feedback-session-{run_id}")
        result = _complete_chat(page, base_url, auth_header, chat_id, session_id, f"GaussDB E2E Shenzhen audit {run_id}", f"q-{run_id}")
        message_id = result.get("id")
        references = result["reference"].get("chunks") or []
        target_ids = [chunk.get("id") or chunk.get("chunk_id") for chunk in references]
        assert message_id and target_ids and all(chunk.get("dataset_id") == dataset_id for chunk in references)
        same_kb_canary = _add_chunk(
            page,
            base_url,
            auth_header,
            dataset_id,
            main_docs[0]["id"],
            f"SAME_KB_NON_REFERENCE_{run_id}: retired dry-goods controller at 18 C.",
        )
        same_kb_canary_id = same_kb_canary["id"]
        assert same_kb_canary_id not in target_ids
        before = _db_pagerank(gaussdb_read_conn, schema, tenant_id, dataset_id, target_ids)
        assert set(before) == set(target_ids)
        same_kb_before = _db_pagerank(gaussdb_read_conn, schema, tenant_id, dataset_id, [same_kb_canary_id])
        assert set(same_kb_before) == {same_kb_canary_id}
        canary_chunk_ids = [row["id"] for row in _db_doc_rows(gaussdb_read_conn, schema, tenant_id, canary_id, canary_docs[0]["id"])]
        canary_before = _db_pagerank(gaussdb_read_conn, schema, tenant_id, canary_id, canary_chunk_ids)
        assert canary_before
        path = f"/api/v1/chats/{chat_id}/sessions/{session_id}/messages/{message_id}/feedback"
        _api_json(page, "PUT", base_url, path, auth_header, {"thumbup": True})
        after = _db_pagerank(gaussdb_read_conn, schema, tenant_id, dataset_id, target_ids)
        assert after == {key: min(value + 1, 100) for key, value in before.items()}
        assert _db_pagerank(gaussdb_read_conn, schema, tenant_id, dataset_id, [same_kb_canary_id]) == same_kb_before
        assert _db_pagerank(gaussdb_read_conn, schema, tenant_id, canary_id, canary_chunk_ids) == canary_before
        _api_json(page, "PUT", base_url, path, auth_header, {"thumbup": True})
        assert _db_pagerank(gaussdb_read_conn, schema, tenant_id, dataset_id, target_ids) == after
        assert _db_pagerank(gaussdb_read_conn, schema, tenant_id, dataset_id, [same_kb_canary_id]) == same_kb_before
        assert _db_pagerank(gaussdb_read_conn, schema, tenant_id, canary_id, canary_chunk_ids) == canary_before
        response, payload = _api_response(page, "PUT", base_url, path, auth_header, {"thumbup": "true"})
        _assert_api_error(response, payload, code=102, message="thumbup must be a boolean")
        assert _db_pagerank(gaussdb_read_conn, schema, tenant_id, dataset_id, target_ids) == after
        assert _db_pagerank(gaussdb_read_conn, schema, tenant_id, dataset_id, [same_kb_canary_id]) == same_kb_before
        assert _db_pagerank(gaussdb_read_conn, schema, tenant_id, canary_id, canary_chunk_ids) == canary_before
    finally:
        _cleanup_chat_and_datasets(
            page,
            base_url,
            auth_header,
            chat_id,
            session_id,
            gaussdb_read_conn,
            schema,
            tenant_id,
            [dataset_id, canary_id],
        )


@pytest.mark.p1
def test_tc_e2e_802_feedback_changes_user_visible_search_order(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    assert os.getenv("CHUNK_FEEDBACK_ENABLED", "").lower() in {"1", "true", "yes", "on"}, "feedback profile must be enabled before E2E"
    assert os.getenv("CHUNK_FEEDBACK_WEIGHTING") == "uniform", "feedback profile must use uniform weighting"
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = chat_id = session_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-feedback-rank-{run_id}")
        files = [_asset_path(tmp_path, "batch_a.txt", run_id), _asset_path(tmp_path, "batch_b.txt", run_id)]
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, files)
        shared = f"RANK_SHARED_{run_id}"
        unique = [f"RANK_TARGET_A_{run_id}", f"RANK_TARGET_B_{run_id}"]
        chunks = [
            _add_chunk(
                page,
                base_url,
                auth_header,
                dataset_id,
                doc["id"],
                shared,
                questions=[unique[index]],
            )
            for index, doc in enumerate(uploaded)
        ]
        chunk_ids = [chunk["id"] for chunk in chunks]

        def ranked_manual_ids(result):
            return [chunk.get("id") or chunk.get("chunk_id") for chunk in result.get("chunks") or [] if (chunk.get("id") or chunk.get("chunk_id")) in set(chunk_ids)]

        before_search = _search_dataset(page, base_url, auth_header, dataset_id, shared, size=20, top_k=20)
        before_order = ranked_manual_ids(before_search)
        assert set(before_order) == set(chunk_ids), before_search
        target_id = before_order[-1]
        target_index = chunk_ids.index(target_id)
        canary_id = chunk_ids[1 - target_index]
        before_pagerank = _db_pagerank(gaussdb_read_conn, schema, tenant_id, dataset_id, chunk_ids)
        chat_id = _create_chat(page, base_url, auth_header, f"feedback-rank-{run_id}", [dataset_id])
        session_id = _create_session(page, base_url, auth_header, chat_id, f"feedback-rank-session-{run_id}")
        non_target_rows = [row for row in _db_rows_by_columns(gaussdb_read_conn, schema, tenant_id, dataset_id, ["id", "doc_id"]) if row["id"] != target_id]
        for row in non_target_rows:
            _update_chunk(page, base_url, auth_header, dataset_id, row["doc_id"], row["id"], {"available": False})
        try:
            answer = _complete_chat(page, base_url, auth_header, chat_id, session_id, unique[target_index], f"q-{run_id}")
        finally:
            for row in non_target_rows:
                _update_chunk(page, base_url, auth_header, dataset_id, row["doc_id"], row["id"], {"available": True})
        message_id = answer.get("id")
        reference_ids = {chunk.get("id") or chunk.get("chunk_id") for chunk in answer["reference"].get("chunks") or []}
        assert message_id and reference_ids == {target_id}, answer
        feedback_path = f"/api/v1/chats/{chat_id}/sessions/{session_id}/messages/{message_id}/feedback"
        _api_json(page, "PUT", base_url, feedback_path, auth_header, {"thumbup": True})
        after_pagerank = _db_pagerank(gaussdb_read_conn, schema, tenant_id, dataset_id, chunk_ids)
        assert after_pagerank[target_id] == min(before_pagerank[target_id] + 1, 100)
        assert after_pagerank[canary_id] == before_pagerank[canary_id]
        after_search = _search_dataset(page, base_url, auth_header, dataset_id, shared, size=20, top_k=20)
        after_order = ranked_manual_ids(after_search)
        assert set(after_order) == set(chunk_ids) and after_order.index(target_id) < before_order.index(target_id), (before_search, after_search)
        _api_json(page, "PUT", base_url, feedback_path, auth_header, {"thumbup": True})
        assert _db_pagerank(gaussdb_read_conn, schema, tenant_id, dataset_id, chunk_ids) == after_pagerank
    finally:
        _cleanup_chat_and_datasets(page, base_url, auth_header, chat_id, session_id, gaussdb_read_conn, schema, tenant_id, [dataset_id])


@pytest.mark.p0
def test_tc_e2e_901_delete_document_removes_chunks_metadata_and_search_hits(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-delete-doc-{run_id}")
        files = [_asset_path(tmp_path, "batch_a.txt", run_id), _asset_path(tmp_path, "batch_b.txt", run_id)]
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, files)
        delete_id, survivor_id = uploaded[0]["id"], uploaded[1]["id"]
        _update_document(page, base_url, auth_header, dataset_id, delete_id, {"meta_fields": {"state": "delete"}})
        _update_document(page, base_url, auth_header, dataset_id, survivor_id, {"meta_fields": {"state": "keep"}})
        _api_json(page, "DELETE", base_url, f"/api/v1/datasets/{dataset_id}/documents", auth_header, {"ids": [delete_id]})
        assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id, delete_id) == 0
        assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id, delete_id) == 0
        requests = [
            {"question": f"ALPHA_ONLY_{run_id}", "vector_similarity_weight": 0.0},
            {"question": f"ALPHA_ONLY_{run_id}", "vector_similarity_weight": 1.0},
            {"question": f"ALPHA_ONLY_{run_id}", "vector_similarity_weight": 0.5},
            {"question": f"BETA_ONLY_{run_id}", "vector_similarity_weight": 0.0, "meta_data_filter": {"method": "manual", "manual": [{"key": "state", "op": "=", "value": "keep"}]}},
        ]
        for options in requests:
            metadata_filtered = "meta_data_filter" in options
            result = _search_dataset(page, base_url, auth_header, dataset_id, options.pop("question"), **options)
            ids = {chunk.get("doc_id") or chunk.get("document_id") for chunk in result.get("chunks") or []}
            assert delete_id not in ids
            if metadata_filtered:
                assert ids == {survivor_id}, result
        survivor_result = _search_dataset(page, base_url, auth_header, dataset_id, f"BETA_ONLY_{run_id}")
        assert survivor_id in {chunk.get("doc_id") or chunk.get("document_id") for chunk in survivor_result.get("chunks") or []}
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p0
def test_tc_e2e_902_delete_dataset_cleans_only_target_kb(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    first_id = second_id = None
    try:
        first_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-delete-kb-a-{run_id}")
        second_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-delete-kb-b-{run_id}")
        first_docs, _, _ = _upload_and_parse_documents(page, base_url, auth_header, first_id, [_asset_path(tmp_path, "batch_a.txt", run_id)])
        second_docs, _, _ = _upload_and_parse_documents(page, base_url, auth_header, second_id, [_asset_path(tmp_path, "batch_b.txt", run_id)])
        _update_document(page, base_url, auth_header, first_id, first_docs[0]["id"], {"meta_fields": {"canary": f"a-{run_id}"}})
        _update_document(page, base_url, auth_header, second_id, second_docs[0]["id"], {"meta_fields": {"canary": f"b-{run_id}"}})
        before_b = _db_doc_rows(gaussdb_read_conn, schema, tenant_id, second_id)
        second_doc_ids = [doc["id"] for doc in second_docs]
        before_b_meta = _db_meta_fields(gaussdb_read_conn, schema, tenant_id, second_id, second_doc_ids)
        assert before_b and before_b_meta
        chunk_table, meta_table = f"ragflow_{tenant_id}", f"ragflow_doc_meta_{tenant_id}"
        assert _table_exists(gaussdb_read_conn, schema, chunk_table) and _table_exists(gaussdb_read_conn, schema, meta_table)
        deleted_id = first_id
        _delete_dataset(page, base_url, auth_header, deleted_id)
        first_id = None
        assert _chunk_count(gaussdb_read_conn, schema, tenant_id, deleted_id) == 0
        assert _meta_count(gaussdb_read_conn, schema, tenant_id, deleted_id) == 0
        assert _db_doc_rows(gaussdb_read_conn, schema, tenant_id, second_id) == before_b
        assert _db_meta_fields(gaussdb_read_conn, schema, tenant_id, second_id, second_doc_ids) == before_b_meta
        assert _table_exists(gaussdb_read_conn, schema, chunk_table) and _table_exists(gaussdb_read_conn, schema, meta_table)
        survivor_search = _search_dataset(page, base_url, auth_header, second_id, f"BETA_ONLY_{run_id}")
        assert second_docs[0]["id"] in {chunk.get("doc_id") or chunk.get("document_id") for chunk in survivor_search.get("chunks") or []}
        response, payload = _api_response(page, "GET", base_url, f"/api/v1/datasets/{deleted_id}", auth_header)
        _assert_api_error(response, payload, code=102)
        response_again, payload_again = _api_response(page, "GET", base_url, f"/api/v1/datasets/{deleted_id}", auth_header)
        assert (response_again.status, payload_again.get("code"), payload_again.get("message")) == (response.status, payload.get("code"), payload.get("message"))
        assert first_docs and second_docs
    finally:
        if first_id:
            _delete_dataset(page, base_url, auth_header, first_id)
        if second_id:
            _delete_dataset(page, base_url, auth_header, second_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, second_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, second_id) == 0


@pytest.mark.p1
def test_tc_e2e_904_stop_parse_removes_partial_chunks(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-stop-{run_id}")
        file_path = _asset_path(tmp_path, "large_12mb.txt", run_id)
        uploaded = _payload_data(_api_multipart_upload(page, base_url, dataset_id, auth_header, file_path))
        doc_id = (uploaded[0] if isinstance(uploaded, list) else uploaded)["id"]
        _api_json(page, "POST", base_url, f"/api/v1/datasets/{dataset_id}/documents/parse", auth_header, {"document_ids": [doc_id]})
        stoppable, history = _wait_for_running_or_chunks(
            page,
            base_url,
            auth_header,
            dataset_id,
            doc_id,
            file_path.name,
            gaussdb_read_conn,
            schema,
            tenant_id,
            timeout_ms=60000,
        )
        assert stoppable and history
        stop = _api_json(page, "POST", base_url, f"/api/v1/datasets/{dataset_id}/documents/stop", auth_header, {"document_ids": [doc_id]})
        assert _payload_data(stop).get("success_count") == 1
        cancelled, cancel_history = _wait_for_run(page, base_url, auth_header, dataset_id, file_path.name, {"CANCEL", "5"}, timeout_ms=120000)
        assert cancelled and cancel_history
        assert cancelled.get("chunk_count", cancelled.get("chunk_num")) == 0
        assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id) == 0
        assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id) == 0
        second_stop = _api_json(page, "POST", base_url, f"/api/v1/datasets/{dataset_id}/documents/stop", auth_header, {"document_ids": [doc_id]})
        assert _payload_data(second_stop).get("success_count") == 1
        after_second_stop = _find_doc_by_name(_list_docs(page, base_url, dataset_id, auth_header), file_path.name)
        assert str(after_second_stop.get("run") or after_second_stop.get("status") or "").upper() in {"CANCEL", "5"}
        assert after_second_stop.get("chunk_count", after_second_stop.get("chunk_num")) == 0
        assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id) == 0
        assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id) == 0
    finally:
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p0
def test_tc_e2e_1001_gaussdb_status_is_authenticated_structured_and_masked(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials):
    response, payload = _api_response(page, "GET", base_url, "/api/v1/system/gaussdb/status", None)
    _assert_api_error(response, payload, status=401)
    assert payload.get("data") is None
    _assert_payload_masked(payload)
    response, payload = _api_response(page, "GET", base_url, "/api/v1/system/healthz", None)
    assert response.status == 200, payload
    auth_header = _ensure_authed_and_get_header(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials)
    status_payload = _api_json(page, "GET", base_url, "/api/v1/system/gaussdb/status", auth_header)
    _assert_payload_masked(status_payload)
    serialized_status = json.dumps(status_payload, ensure_ascii=False)
    for env_name in ("GAUSSDB_PASSWORD", "E2E_ADMIN_PASSWORD"):
        secret = os.getenv(env_name)
        if secret:
            assert secret not in serialized_status, f"{env_name} leaked in status payload"
    status_data = _payload_data(status_payload)
    assert status_data.get("status") == "alive", status_payload
    assert status_data.get("message", {}).get("health", {}).get("status") == "healthy", status_payload
    assert status_data.get("message", {}).get("performance", {}).get("connection") == "connected", status_payload
    health_response, health_payload = _api_response(page, "GET", base_url, "/api/v1/system/healthz", None)
    assert health_response.status == 200 and isinstance(health_payload, dict) and health_payload.get("status") == "ok", health_payload
    _assert_payload_masked(health_payload)


@pytest.mark.p1
def test_tc_e2e_1101_gaussdb_disconnect_and_recovery(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    assert os.getenv("GAUSSDB_FAULT_INJECTION") == "1", "TC-E2E-1101 requires an exclusive fault-injection profile"
    for env_name in ("GAUSSDB_E2E_FAULT_COMMAND", "GAUSSDB_E2E_RECOVER_COMMAND"):
        assert os.getenv(env_name), f"TC-E2E-1101 requires {env_name}"
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    fault_injected = False
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-fault-{run_id}")
        file_path = _asset_path(tmp_path, "basic.txt", run_id)
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, [file_path])
        doc_id = uploaded[0]["id"]
        before_count = _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
        fault_injected = True
        _run_exclusive_command("GAUSSDB_E2E_FAULT_COMMAND")
        _wait_gaussdb_status(page, base_url, auth_header, "timeout", timeout_ms=60000)
        failures = []
        for _ in range(2):
            response, payload = _api_response(page, "POST", base_url, f"/api/v1/datasets/{dataset_id}/search", auth_header, {"question": f"GaussDB E2E Shenzhen audit {run_id}"})
            _assert_payload_masked(payload)
            assert os.environ["GAUSSDB_PASSWORD"] not in json.dumps(payload, ensure_ascii=False, default=str)
            assert isinstance(payload, dict) and payload.get("code") != 0 and isinstance(payload.get("message"), str), (response.status, payload)
            failures.append((response.status, payload.get("code"), payload.get("message")))
        assert failures[0] == failures[1], failures
        _run_exclusive_command("GAUSSDB_E2E_RECOVER_COMMAND")
        alive, _ = _wait_gaussdb_status(page, base_url, auth_header, "alive")
        assert _payload_data(alive).get("status") == "alive", alive
        assert _search_dataset(page, base_url, auth_header, dataset_id, f"GaussDB E2E Shenzhen audit {run_id}").get("chunks")
        assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id) == before_count
    finally:
        if fault_injected:
            _run_exclusive_command("GAUSSDB_E2E_RECOVER_COMMAND")
            _wait_gaussdb_status(page, base_url, auth_header, "alive")
        if dataset_id:
            _delete_dataset(page, base_url, auth_header, dataset_id)
            assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
            assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0


@pytest.mark.p1
def test_tc_e2e_1102_fixed_large_file_preserves_data_integrity(
    request, page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    runtime_profile = _record_embedding_profile(request)
    dataset_id = doc_id = file_name = None
    assertion_db = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-large-{run_id}", parser_config={"chunk_token_num": 1024, "delimiter": "\n\n"})
        file_path = _asset_path(tmp_path, "large_12mb.txt", run_id)
        file_name = file_path.name
        upload_started = time.monotonic()
        uploaded = _payload_data(
            _api_multipart_upload_files(
                page,
                base_url,
                dataset_id,
                auth_header,
                [file_path],
                {"chunk_token_num": 1024, "delimiter": "\n\n"},
                timeout_s=LONG_RUNNING_TIMEOUT_MS // 1000,
            )
        )
        upload_seconds = time.monotonic() - upload_started
        uploaded = uploaded if isinstance(uploaded, list) else [uploaded]
        doc_id = uploaded[0]["id"]
        parse_started = time.monotonic()
        _parse_documents(page, base_url, auth_header, dataset_id, [doc_id])
        parsed, history = _wait_doc_done(page, base_url, dataset_id, auth_header, file_path.name, LONG_RUNNING_TIMEOUT_MS)
        parse_seconds = time.monotonic() - parse_started
        chunk_count = parsed.get("chunk_count", parsed.get("chunk_num"))
        assertion_db = _open_gaussdb_read_conn()
        rows = _db_doc_rows(assertion_db, schema, tenant_id, dataset_id, doc_id)
        dataset_rows = _db_doc_rows(assertion_db, schema, tenant_id, dataset_id)
        assert isinstance(chunk_count, int) and 1 <= chunk_count <= 20000 and chunk_count == len(rows), (parsed, history)
        assert len(dataset_rows) == len(rows) and {row["doc_id"] for row in dataset_rows} == {doc_id}
        assert all(row["content_with_weight"] for row in rows)
        assert all(_db_vector_valid(assertion_db, schema, tenant_id, dataset_id, doc_id))
        assert len({(row["kb_id"], row["id"]) for row in rows}) == len(rows)
        expected_names = {doc_id: file_name}
        for token in (
            "GAUSSDB_LARGE_HEAD_ANCHOR",
            "GAUSSDB_LARGE_MIDDLE_ANCHOR",
            "GAUSSDB_LARGE_TAIL_ANCHOR",
        ):
            result = _search_dataset(
                page,
                base_url,
                auth_header,
                dataset_id,
                token,
                vector_similarity_weight=0.0,
                similarity_threshold=0.000001,
            )
            _assert_search_sources(
                result,
                assertion_db,
                schema,
                tenant_id,
                dataset_id,
                expected_names,
                exact_doc_ids={doc_id},
            )
        canary = _search_dataset(
            page,
            base_url,
            auth_header,
            dataset_id,
            "GAUSSDB_LARGE_NEVER_APPROVED_CANARY",
            vector_similarity_weight=0.0,
            similarity_threshold=0.000001,
        )
        assert not (canary.get("chunks") or []), canary
        conn, _ = assertion_db
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            gaussdb_version = str(cur.fetchone()[0])
        model_data = _payload_data(_api_json(page, "GET", base_url, "/api/v1/users/me/models", auth_header))
        metrics = {
            "upload_seconds": upload_seconds,
            "parse_seconds": parse_seconds,
            "chunk_count": chunk_count,
            "embedding_model": model_data.get("embd_id") or model_data.get("embedding_model"),
            "ragflow_image": runtime_profile["ragflow_image"],
            "gaussdb_version": gaussdb_version,
            "machine": platform.platform(),
            "embedding_batch_size": runtime_profile["embedding_batch_size"],
            "cuda_provider": runtime_profile["cuda_provider"],
            "cuda_model": runtime_profile["cuda_model"],
            "cuda_dimensions": runtime_profile["cuda_dimensions"],
            "runtime_profile": os.environ["GAUSSDB_E2E_RUNTIME_PROFILE_PATH"],
        }
        assert metrics["embedding_model"]
        artifact = _write_json_artifact(request, "performance", metrics)
        request.node.user_properties.extend(metrics.items())
        request.node.user_properties.append(("performance_artifact", str(artifact)))
    finally:
        if assertion_db:
            assertion_db[0].close()
        if dataset_id:
            if doc_id and file_name:
                _stop_document_before_cleanup(page, base_url, auth_header, dataset_id, doc_id, file_name)
            _delete_dataset(page, base_url, auth_header, dataset_id)
            cleanup_db = _open_gaussdb_read_conn()
            try:
                assert _chunk_count(cleanup_db, schema, tenant_id, dataset_id) == 0
                assert _meta_count(cleanup_db, schema, tenant_id, dataset_id) == 0
            finally:
                cleanup_db[0].close()


@pytest.mark.p1
def test_tc_e2e_1103_fixed_long_text_has_bounded_chunk_count(
    request, page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    _record_embedding_profile(request)
    dataset_id = doc_id = file_name = None
    assertion_db = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-long-{run_id}", parser_config={"chunk_token_num": 128, "delimiter": "\n"})
        file_path = _asset_path(tmp_path, "long_text.txt", run_id)
        file_name = file_path.name
        uploaded, _, _ = _upload_and_parse_documents(
            page,
            base_url,
            auth_header,
            dataset_id,
            [file_path],
            {"chunk_token_num": 128, "delimiter": "\n"},
            upload_timeout_s=LONG_RUNNING_TIMEOUT_MS // 1000,
            parse_timeout_ms=LONG_RUNNING_TIMEOUT_MS,
        )
        doc_id = uploaded[0]["id"]
        parsed, history = _wait_doc_done(page, base_url, dataset_id, auth_header, file_path.name, LONG_RUNNING_TIMEOUT_MS)
        assertion_db = _open_gaussdb_read_conn()
        rows = _db_doc_rows(assertion_db, schema, tenant_id, dataset_id, doc_id)
        dataset_rows = _db_doc_rows(assertion_db, schema, tenant_id, dataset_id)
        chunk_count = parsed.get("chunk_count", parsed.get("chunk_num"))
        assert isinstance(chunk_count, int) and 1 <= chunk_count <= 20000 and chunk_count == len(rows), (parsed, history)
        assert len(dataset_rows) == len(rows) and {row["doc_id"] for row in dataset_rows} == {doc_id}
        expected_names = {doc_id: file_name}
        for token in ("FIRST_GAUSSDB_LONG_TOKEN", "MIDDLE_GAUSSDB_LONG_TOKEN", "LAST_GAUSSDB_LONG_TOKEN"):
            result = _search_dataset(
                page,
                base_url,
                auth_header,
                dataset_id,
                token,
                vector_similarity_weight=0.0,
                similarity_threshold=0.000001,
            )
            _assert_search_sources(
                result,
                assertion_db,
                schema,
                tenant_id,
                dataset_id,
                expected_names,
                exact_doc_ids={doc_id},
            )
        canary = _search_dataset(
            page,
            base_url,
            auth_header,
            dataset_id,
            "GAUSSDB_LONG_NEVER_APPROVED_CANARY",
            vector_similarity_weight=0.0,
            similarity_threshold=0.000001,
        )
        assert not (canary.get("chunks") or []), canary
        assert all(row["kb_id"] == dataset_id for row in rows)
        assert all(_db_vector_valid(assertion_db, schema, tenant_id, dataset_id, doc_id))
        assert len({(row["kb_id"], row["id"]) for row in rows}) == len(rows)
    finally:
        if assertion_db:
            assertion_db[0].close()
        if dataset_id:
            if doc_id and file_name:
                _stop_document_before_cleanup(page, base_url, auth_header, dataset_id, doc_id, file_name)
            _delete_dataset(page, base_url, auth_header, dataset_id)
            cleanup_db = _open_gaussdb_read_conn()
            try:
                assert _chunk_count(cleanup_db, schema, tenant_id, dataset_id) == 0
                assert _meta_count(cleanup_db, schema, tenant_id, dataset_id) == 0
            finally:
                cleanup_db[0].close()


@pytest.mark.p0
def test_tc_e2e_1104_concurrent_first_parses_isolate_two_datasets(
    page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_ids = []
    try:
        dataset_ids = [_create_dataset(page, base_url, auth_header, f"gaussdb-e2e-concurrent-{suffix}-{run_id}") for suffix in ("a", "b")]
        file_paths = [_asset_path(tmp_path, "batch_a.txt", run_id), _asset_path(tmp_path, "batch_b.txt", run_id)]
        documents = []
        for dataset_id, file_path in zip(dataset_ids, file_paths):
            uploaded = _payload_data(_api_multipart_upload(page, base_url, dataset_id, auth_header, file_path))
            documents.append((uploaded[0] if isinstance(uploaded, list) else uploaded)["id"])
        barrier = Barrier(2)

        def post_parse(index):
            barrier.wait()
            return _threaded_json_post(
                base_url,
                f"/api/v1/datasets/{dataset_ids[index]}/documents/parse",
                auth_header,
                {"document_ids": [documents[index]]},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(post_parse, range(2)))
        assert all(status == 200 and payload.get("code") == 0 and _payload_data(payload).get("success_count") == 1 for status, payload in responses), responses
        parsed = [_wait_doc_done(page, base_url, dataset_id, auth_header, file_path.name) for dataset_id, file_path in zip(dataset_ids, file_paths)]
        row_sets = []
        valid_columns = []
        for index, (dataset_id, doc_id) in enumerate(zip(dataset_ids, documents)):
            rows = _db_doc_rows(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
            row_sets.append(rows)
            assert rows and parsed[index][0].get("chunk_count", parsed[index][0].get("chunk_num")) == len(rows), parsed[index]
            assert all(row["kb_id"] == dataset_id and row["doc_id"] == doc_id for row in rows)
            assert len({(row["kb_id"], row["id"]) for row in rows}) == len(rows)
            valid_column, vector_state = _db_vector_valid_state(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
            valid_columns.append(valid_column)
            assert set(vector_state) == {row["id"] for row in rows} and all(vector_state.values()), vector_state
        assert {row["id"] for row in row_sets[0]}.isdisjoint({row["id"] for row in row_sets[1]})
        assert len(set(valid_columns)) == 1, valid_columns
        table = f"ragflow_{tenant_id}"
        index_defs = _index_defs(gaussdb_read_conn, schema, table)
        fulltext_index = f"idx_gdb_{table}_fts_all"
        assert len(re.findall(rf"create\s+index\s+{re.escape(fulltext_index)}\s+on\b", index_defs)) == 1, index_defs
        dimension = re.search(r"q_(\d+)_vec_valid$", valid_columns[0]).group(1)
        vector_defs = [definition for definition in index_defs.splitlines() if re.search(rf"using\s+gsdiskann\s*\(\s*q_{dimension}_vec(?:\s+cosine)?\s*\)", definition)]
        assert len(vector_defs) == 1, index_defs
        alpha = _search_dataset(page, base_url, auth_header, dataset_ids[0], f"ALPHA_ONLY_{run_id}")
        beta = _search_dataset(page, base_url, auth_header, dataset_ids[1], f"BETA_ONLY_{run_id}")
        assert {chunk.get("doc_id") or chunk.get("document_id") for chunk in alpha.get("chunks") or []} == {documents[0]}
        assert {chunk.get("doc_id") or chunk.get("document_id") for chunk in beta.get("chunks") or []} == {documents[1]}
    finally:
        _cleanup_chat_and_datasets(page, base_url, auth_header, None, None, gaussdb_read_conn, schema, tenant_id, dataset_ids)


@pytest.mark.p1
def test_tc_e2e_1105_service_restart_preserves_gaussdb_state(
    request, page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn, run_id, tmp_path
):
    restart_command = os.getenv("GAUSSDB_E2E_RESTART_COMMAND")
    assert restart_command, "TC-E2E-1105 requires an exclusive restart command"
    compose_dir = os.getenv("GAUSSDB_E2E_COMPOSE_DIR")
    assert compose_dir and Path(compose_dir).is_dir(), "TC-E2E-1105 requires GAUSSDB_E2E_COMPOSE_DIR"
    assert os.getenv("DOC_ENGINE") == "gaussdb"
    profiles = {item.strip() for item in os.getenv("COMPOSE_PROFILES", "").split(",") if item.strip()}
    assert {"gaussdb", "cpu"} <= profiles and any(item.startswith("tei-") for item in profiles), profiles
    for env_name in (
        "GAUSSDB_HOST",
        "GAUSSDB_PORT",
        "GAUSSDB_DATABASE",
        "GAUSSDB_USER",
        "GAUSSDB_PASSWORD",
        "GAUSSDB_SCHEMA",
    ):
        assert os.getenv(env_name), f"TC-E2E-1105 requires {env_name}"
    assert os.getenv("TEI_HOST") == "tei"
    assert os.getenv("TEI_MODEL") == "BAAI/bge-large-en-v1.5"
    restart_args = shlex.split(restart_command, posix=os.name != "nt")
    assert restart_args == [
        "docker",
        "compose",
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        "ragflow-cpu",
    ], restart_args
    auth_header, tenant_id, schema = _e2e_context(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials, ensure_model_provider_configured, gaussdb_read_conn)
    dataset_id = None
    restarted_db = None
    try:
        dataset_id = _create_dataset(page, base_url, auth_header, f"gaussdb-e2e-restart-{run_id}")
        file_path = _asset_path(tmp_path, "basic.txt", run_id)
        uploaded, _, _ = _upload_and_parse_documents(page, base_url, auth_header, dataset_id, [file_path])
        doc_id = uploaded[0]["id"]
        before_rows = _db_doc_rows(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
        before_indexes = _index_defs(gaussdb_read_conn, schema, f"ragflow_{tenant_id}")
        assert before_rows and len({row["id"] for row in before_rows}) == len(before_rows)
        assert "using gsdiskann" in before_indexes and "idx_gdb_" in before_indexes, before_indexes
        before_status = _api_json(page, "GET", base_url, "/api/v1/system/gaussdb/status", auth_header)
        assert _payload_data(before_status).get("status") == "alive", before_status
        request.node.user_properties.append(("compose_dir", compose_dir))
        restart_started = time.monotonic()
        _run_exclusive_command("GAUSSDB_E2E_RESTART_COMMAND", cwd=compose_dir)
        _wait_ragflow_health(base_url, RESTART_WATCHDOG_S)
        auth_header = _ensure_authed_and_get_header(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials)
        status, _ = _wait_gaussdb_status(page, base_url, auth_header, "alive", timeout_ms=RESTART_WATCHDOG_S * 1000)
        recovery_seconds = time.monotonic() - restart_started
        restarted_db = _open_gaussdb_read_conn()
        gaussdb_read_conn = restarted_db
        search = _search_dataset(page, base_url, auth_header, dataset_id, f"GaussDB E2E Shenzhen audit {run_id}")
        assert doc_id in {chunk.get("doc_id") or chunk.get("document_id") for chunk in search.get("chunks") or []}
        after_rows = _db_doc_rows(gaussdb_read_conn, schema, tenant_id, dataset_id, doc_id)
        after_indexes = _index_defs(gaussdb_read_conn, schema, f"ragflow_{tenant_id}")
        assert after_rows == before_rows and len({row["id"] for row in after_rows}) == len(after_rows)
        assert after_indexes == before_indexes
        evidence = {
            "recovery_seconds": recovery_seconds,
            "before_status": _payload_data(before_status),
            "after_status": _payload_data(status),
            "chunk_ids": [row["id"] for row in before_rows],
            "index_definitions_sha256": hashlib.sha256(before_indexes.encode()).hexdigest(),
        }
        _assert_payload_masked(evidence)
        artifact = _write_json_artifact(request, "restart", evidence)
        request.node.user_properties.append(("restart_recovery_seconds", recovery_seconds))
        request.node.user_properties.append(("restart_artifact", str(artifact)))
    except BaseException:
        _capture_compose_evidence(request, compose_dir)
        raise
    finally:
        try:
            _wait_ragflow_health(base_url, min(RESTART_WATCHDOG_S, 60))
        except AssertionError:
            _run_exclusive_command("GAUSSDB_E2E_RESTART_COMMAND", cwd=compose_dir)
            _wait_ragflow_health(base_url, RESTART_WATCHDOG_S)
        auth_header = _ensure_authed_and_get_header(page, base_url, login_url, active_auth_context, auth_click, seeded_user_credentials)
        if restarted_db is None:
            restarted_db = _open_gaussdb_read_conn()
            gaussdb_read_conn = restarted_db
        try:
            if dataset_id:
                _delete_dataset(page, base_url, auth_header, dataset_id)
                assert _chunk_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
                assert _meta_count(gaussdb_read_conn, schema, tenant_id, dataset_id) == 0
        finally:
            restarted_db[0].close()
