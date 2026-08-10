import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_environment_validate.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_environment_validate", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_summarize_health_payload_omits_errors_and_heartbeat_ids():
    module = load_module()
    payload = {
        "doc_engine": {
            "type": "gaussdb",
            "status": "green",
            "elapsed": "3.2",
            "error": "password=must-not-leak",
        },
        "storage": {"storage": "minio", "status": "green", "elapsed": "1.2"},
        "database": {"database": "gaussdb", "status": "green", "elapsed": "2.1"},
        "redis": {"status": "green", "elapsed": "0.4"},
        "task_executor_heartbeats": {"secret-worker-id": [{"now": 1}]},
    }

    summary = module.summarize_health_payload(payload)

    assert summary == {
        "doc_engine": {
            "type": "gaussdb",
            "status": "green",
            "elapsed_ms": "3.2",
            "server_encoding": None,
            "client_encoding": None,
            "warning": None,
        },
        "storage": {"type": "minio", "status": "green", "elapsed_ms": "1.2"},
        "database": {"type": "gaussdb", "status": "green", "elapsed_ms": "2.1"},
        "redis": {"type": None, "status": "green", "elapsed_ms": "0.4"},
        "task_executor_count": 1,
    }
    assert "must-not-leak" not in str(summary)
    assert "secret-worker-id" not in str(summary)


def test_health_summary_preserves_gaussdb_encoding_contract():
    module = load_module()
    payload = {
        "doc_engine": {
            "type": "gaussdb",
            "status": "healthy",
            "elapsed": "1.2",
            "server_encoding": "SQL_ASCII",
            "client_encoding": "UTF8",
            "warning": "server does not validate bytes",
        }
    }

    result = module.summarize_health_payload(payload)

    assert result["doc_engine"]["server_encoding"] == "SQL_ASCII"
    assert result["doc_engine"]["client_encoding"] == "UTF8"
    assert result["doc_engine"]["warning"] == "server does not validate bytes"


def test_scan_log_text_reports_counts_without_copying_secret():
    module = load_module()
    secret = "gauss-private-value"
    summary = module.scan_log_text(
        f"config password={secret}\nTraceback (most recent call last):\ntoken sk-exampleSecret123456789\nERROR failed\n",
        [secret],
    )

    assert summary == {
        "known_secret_occurrences": 1,
        "key_pattern_occurrences": 1,
        "traceback_occurrences": 1,
        "error_occurrences": 1,
    }
    assert secret not in str(summary)


def test_scan_log_path_streams_without_reading_entire_file(tmp_path, monkeypatch):
    module = load_module()
    secret = "gauss-private-value"
    path = tmp_path / "managed_worker.log"
    path.write_text(
        ("ordinary line\n" * 10_000) + f"password={secret}\n" + "Traceback (most recent call last):\n" + "token sk-exampleSecret123456789\n" + "ERROR failed\n",
        encoding="utf-8",
    )

    def reject_read_text(*_args, **_kwargs):
        raise AssertionError("whole-file Path.read_text is forbidden for managed logs")

    monkeypatch.setattr(Path, "read_text", reject_read_text)
    summary = module.scan_log_path(path, [secret])

    assert summary == {
        "known_secret_occurrences": 1,
        "key_pattern_occurrences": 1,
        "traceback_occurrences": 1,
        "error_occurrences": 1,
    }


def test_group_order_is_control_first():
    module = load_module()

    assert module.GROUP_ORDER == ("control", "experiment")


def test_component_health_accepts_both_product_success_vocabularies():
    module = load_module()

    assert module.component_is_healthy({"status": "green"}) is True
    assert module.component_is_healthy({"status": "healthy"}) is True
    assert module.component_is_healthy({"status": "red"}) is False
    assert module.component_is_healthy({"status": None}) is False


def test_public_conflict_oracle_allows_empty_public_structures_for_isolated_schema():
    module = load_module()

    assert module.public_conflict_oracle_ok("public", 2, 9) is True
    assert module.public_conflict_oracle_ok("isolated_metadata", 0, 0) is True
    assert module.public_conflict_oracle_ok("isolated_metadata", 2, 0) is True
    assert module.public_conflict_oracle_ok("isolated_metadata", 2, 1) is False


def test_direct_gauss_validation_explicitly_sets_search_path_after_connect(monkeypatch):
    import psycopg2

    module = load_module()

    class Cursor:
        def __init__(self):
            self.calls = []
            self.current = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def execute(self, query, params=None):
            self.current = str(query)
            self.calls.append((query, params))

        def fetchone(self):
            if "current_database" in self.current:
                return ("zws_test2", "isolated_metadata", "GaussDB test")
            if "sql_compatibility" in self.current:
                return ("ORA",)
            raise AssertionError(f"unexpected fetchone for {self.current}")

        def fetchall(self):
            if "information_schema.tables" in self.current:
                return []
            raise AssertionError(f"unexpected fetchall for {self.current}")

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()
            self.closed = False

        def cursor(self):
            return self.cursor_instance

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(psycopg2, "connect", lambda **_kwargs: connection)

    result = module._validate_gauss_connection(
        {
            "host": "127.0.0.1",
            "port": 15432,
            "database": "zws_test2",
            "user": "tester",
            "password": "secret",
            "schema": "isolated_metadata",
        }
    )

    first_query = str(connection.cursor_instance.calls[0][0])
    assert "SET search_path TO" in first_query
    assert "Identifier('isolated_metadata')" in first_query
    assert result["schema_matches"] is True
    assert connection.closed is True
