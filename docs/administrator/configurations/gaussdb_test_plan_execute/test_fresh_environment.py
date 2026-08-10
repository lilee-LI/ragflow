import copy
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("fresh_environment.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_environment", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def base_config():
    return {
        "ragflow": {"host": "0.0.0.0", "http_port": 9380},
        "admin": {"host": "0.0.0.0", "http_port": 9381},
        "mysql": {
            "name": "rag_flow",
            "user": "mysql-user",
            "password": "mysql-secret",
            "host": "127.0.0.1",
            "port": 3306,
        },
        "infinity": {"uri": "127.0.0.1:23817", "db_name": "default_db"},
        "redis": {
            "db": 1,
            "username": "",
            "password": "redis-secret",
            "host": "127.0.0.1:6379",
        },
        "minio": {
            "user": "minio-user",
            "password": "minio-secret",
            "host": "127.0.0.1:9000",
            "bucket": "",
            "prefix_path": "",
        },
        "es": {"hosts": "http://unused:9200", "password": "unused-secret"},
    }


def gauss_info():
    return {
        "host": "gauss.example.invalid",
        "port": "8000",
        "dbname": "zws_test2",
        "user": "guser",
        "password": "gauss-secret",
    }


def test_parse_gauss_info_requires_exact_keys_and_rejects_duplicates(tmp_path):
    module = load_module()
    path = tmp_path / "gaussdb_info.md"
    path.write_text(
        "user: u\nhost: h\nport: 8000\ndbname: d\npassword: p\n",
        encoding="utf-8",
    )
    assert module.parse_gauss_info(path) == {
        "user": "u",
        "host": "h",
        "port": "8000",
        "dbname": "d",
        "password": "p",
    }

    path.write_text("user: u\nuser: other\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate key"):
        module.parse_gauss_info(path)


def test_build_group_configs_isolates_every_mutable_service():
    module = load_module()
    original = base_config()
    snapshot = copy.deepcopy(original)

    control, experiment = module.build_group_configs(original, gauss_info())

    assert original == snapshot
    assert control["ragflow"]["http_port"] == 9380
    assert experiment["ragflow"]["http_port"] == 9480
    assert control["mysql"]["name"] == module.CONTROL_MYSQL_DATABASE
    assert control["mysql"]["port"] == module.PROXY_PORTS["control_metadata"]
    assert control["infinity"]["db_name"] == module.CONTROL_INFINITY_DATABASE
    assert control["redis"]["db"] == 11
    assert experiment["redis"]["db"] == 12
    assert control["minio"]["bucket"] != experiment["minio"]["bucket"]
    assert control["minio"]["prefix_path"] != experiment["minio"]["prefix_path"]
    assert "gaussdb" not in control
    assert "mysql" not in experiment
    assert "infinity" not in experiment
    assert "es" not in control
    assert "es" not in experiment
    assert experiment["gaussdb"]["config"]["database"] == module.EXPERIMENT_DATABASE
    assert experiment["gaussdb"]["config"]["schema"] == module.EXPERIMENT_DOCSTORE_SCHEMA
    assert experiment["gaussdb"]["config"]["password"] == "gauss-secret"


def test_build_private_environments_uses_distinct_token_secrets():
    module = load_module()
    environments = module.build_private_environments(gauss_info())

    assert environments["control"]["RAGFLOW_SECRET_KEY"] != environments["experiment"]["RAGFLOW_SECRET_KEY"]
    assert len(environments["control"]["RAGFLOW_SECRET_KEY"]) >= 32
    assert environments["control"]["DB_TYPE"] == "mysql"
    assert environments["control"]["DOC_ENGINE"] == "infinity"
    assert environments["experiment"]["DB_TYPE"] == "gaussdb"
    assert environments["experiment"]["DOC_ENGINE"] == "gaussdb"
    assert environments["experiment"]["GAUSSDB_METADATA_DBNAME"] == module.EXPERIMENT_DATABASE
    assert environments["experiment"]["GAUSSDB_METADATA_SCHEMA"] == module.EXPERIMENT_METADATA_SCHEMA


def test_gauss_connect_kwargs_explicitly_disable_default_read_only():
    module = load_module()

    kwargs = module.gauss_connect_kwargs(gauss_info(), dbname=module.EXPERIMENT_DATABASE)

    assert kwargs["options"] == "-c default_transaction_read_only=off"
    assert kwargs["connect_timeout"] == 5
    assert kwargs["dbname"] == module.EXPERIMENT_DATABASE


def test_clean_infinity_clears_only_default_db_tables(monkeypatch):
    module = load_module()
    dropped = []

    class FakeDatabase:
        def __init__(self):
            self.tables = ["ragflow_a", "ragflow_b"]

        def list_tables(self):
            return types.SimpleNamespace(table_names=list(self.tables))

        def drop_table(self, table_name, conflict_type):
            assert conflict_type == "ignore"
            dropped.append(table_name)
            self.tables.remove(table_name)

    database = FakeDatabase()

    class FakeConnection:
        def list_databases(self):
            return types.SimpleNamespace(db_names=["default_db", "system"])

        def get_database(self, name):
            assert name == "default_db"
            return database

        def disconnect(self):
            pass

    fake_infinity = types.SimpleNamespace(connect=lambda _address: FakeConnection())
    fake_common = types.SimpleNamespace(
        ConflictType=types.SimpleNamespace(Ignore="ignore"),
        NetworkAddress=lambda host, port: (host, port),
    )
    monkeypatch.setitem(sys.modules, "infinity", fake_infinity)
    monkeypatch.setitem(sys.modules, "infinity.common", fake_common)

    result = module._clean_infinity(base_config())

    assert dropped == ["ragflow_a", "ragflow_b"]
    assert result == {
        "database_present": True,
        "table_count_before": 2,
        "table_count_after": 0,
    }


def test_clean_gauss_clears_only_public_objects_inside_zws_test2(monkeypatch):
    module = load_module()
    calls = []
    system_snapshots = 0

    class FakeCursor:
        def __init__(self, connection):
            self.connection = connection
            self.last_sql = ""
            self.public_truncated = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=None):
            self.last_sql = sql
            if sql.startswith("TRUNCATE TABLE"):
                self.public_truncated = True
            calls.append((self.connection.kwargs["dbname"], sql, params))

        def fetchone(self):
            if "current_database()" in self.last_sql:
                return ("zws_test2",)
            if "SHOW sql_compatibility" in self.last_sql:
                return ("A",)
            if "SHOW maintenance_work_mem" in self.last_sql:
                maintenance_set = any(sql.startswith("SET maintenance_work_mem") for _, sql, _ in calls)
                return ("1GB" if maintenance_set else "64MB",)
            if self.last_sql.startswith('SELECT COUNT(*) FROM "public".'):
                return (0 if self.public_truncated else 5,)
            if self.last_sql.startswith("SELECT setval"):
                return (1,)
            raise AssertionError(f"unexpected fetchone for {self.last_sql}")

        def fetchall(self):
            nonlocal system_snapshots
            if "SELECT table_name" in self.last_sql:
                return [("tenant",)]
            if "sequence_name" in self.last_sql:
                return [("tenant_id_seq",)]
            if "table_schema <> 'public'" in self.last_sql:
                system_snapshots += 1
                return [("db4ai", "BASE TABLE", 1)]
            if "information_schema.schemata" in self.last_sql:
                return [
                    (module.EXPERIMENT_METADATA_SCHEMA,),
                    (module.EXPERIMENT_DOCSTORE_SCHEMA,),
                ]
            raise AssertionError(f"unexpected fetchall for {self.last_sql}")

    class FakeConnection:
        def __init__(self, kwargs):
            self.kwargs = kwargs
            self.autocommit = False

        def cursor(self):
            return FakeCursor(self)

        def close(self):
            pass

    connections = []

    def connect(**kwargs):
        connection = FakeConnection(kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setitem(sys.modules, "psycopg2", types.SimpleNamespace(connect=connect))

    result = module._clean_gauss(gauss_info())

    assert [conn.kwargs["dbname"] for conn in connections] == ["zws_test2"]
    executed_sql = [sql for _dbname, sql, _params in calls]
    assert not any("DATABASE" in sql for sql in executed_sql)
    assert 'TRUNCATE TABLE "public"."tenant"' in executed_sql
    assert "SELECT setval(%s, %s, %s)" in executed_sql
    assert any(params == ('"public"."tenant_id_seq"', 1, False) for _dbname, sql, params in calls if sql.startswith("SELECT setval"))
    assert not any("db4ai" in sql for sql in executed_sql)
    assert system_snapshots == 2
    assert result == {
        "database": "zws_test2",
        "sql_compatibility": "A",
        "maintenance_work_mem_before": "64MB",
        "maintenance_work_mem_used": "1GB",
        "public_table_count": 1,
        "public_rows_before": 5,
        "public_rows_after": 0,
        "public_sequences_reset": 1,
        "metadata_schema": True,
        "docstore_schema": True,
        "system_catalog_unchanged": True,
    }


def test_redacted_manifest_never_contains_raw_secrets():
    module = load_module()
    configs = module.build_group_configs(base_config(), gauss_info())
    envs = module.build_private_environments(gauss_info())

    manifest = module.redacted_manifest(configs, envs)
    rendered = str(manifest)

    for secret in (
        "mysql-secret",
        "redis-secret",
        "minio-secret",
        "gauss-secret",
        "gauss.example.invalid",
        "guser",
        "fr-20260710-fresh-001-control-admin@example.com",
    ):
        assert secret not in rendered
    assert "fingerprint" in rendered
    assert manifest["batch_id"] == module.BATCH_ID
    assert manifest["resources"]["experiment_database"] == module.EXPERIMENT_DATABASE


@pytest.mark.parametrize(
    "name",
    ["public", "default_db", "rag_flow", "other_batch_control", "fr_20260710_fresh_001"],
)
def test_assert_batch_resource_name_rejects_unsafe_targets(name):
    module = load_module()
    with pytest.raises(ValueError):
        module.assert_batch_resource_name(name)


def test_assert_batch_resource_name_accepts_only_suffixed_batch_targets():
    module = load_module()
    assert module.assert_batch_resource_name(module.EXPERIMENT_METADATA_SCHEMA) == module.EXPERIMENT_METADATA_SCHEMA


def test_authorized_database_guards_are_exact():
    module = load_module()

    assert module.assert_control_mysql_database("rag_flow") == "rag_flow"
    assert module.assert_control_infinity_database("default_db") == "default_db"
    assert module.assert_authorized_gauss_database("zws_test2") == "zws_test2"
    for value in ("zws_test", "postgres", module.EXPERIMENT_METADATA_SCHEMA):
        with pytest.raises(ValueError):
            module.assert_authorized_gauss_database(value)


def test_write_environment_result_persists_private_current_run_evidence(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "ENV_EVIDENCE_DIR", tmp_path / "evidence", raising=False)
    writer = getattr(module, "write_environment_result", None)

    assert writer is not None
    path = writer("clean", {"batch_id": module.BATCH_ID, "mysql": {"table_count": 0}})

    assert path == tmp_path / "evidence" / "clean_result.json"
    assert json.loads(path.read_text(encoding="utf-8"))["batch_id"] == module.BATCH_ID
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_snapshot_initial_api_logs_uses_current_runtime_and_refuses_overwrite(tmp_path, monkeypatch):
    module = load_module()
    runtime = tmp_path / "runtime"
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(module, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(module, "ENV_EVIDENCE_DIR", evidence, raising=False)
    snapshotter = getattr(module, "snapshot_initial_api_logs", None)

    assert snapshotter is not None
    for group in ("control", "experiment"):
        log = runtime / group / "logs" / "managed_api.log"
        log.parent.mkdir(parents=True)
        log.write_bytes(f"{group} ready\n".encode())

    result = snapshotter()

    assert result["group_order"] == ["control", "experiment"]
    for group in ("control", "experiment"):
        archived = evidence / f"startup_attempt1_{group}.log"
        assert archived.read_bytes() == f"{group} ready\n".encode()
        assert archived.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        snapshotter()
