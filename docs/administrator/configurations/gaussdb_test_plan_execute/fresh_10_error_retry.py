#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import MethodType
from typing import Any, Callable

from peewee import OperationalError

try:
    from api.db import db_error_utils as ERROR_UTILS
except ImportError:
    from api.db import gaussdb_error_utils as ERROR_UTILS


EXECUTE_DIR = Path(__file__).resolve().parent
CASE_IDS = tuple(f"TC-FR-{number:03d}" for number in range(1, 13))


def _load_module(path: Path, name: str):
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


COMMON = _load_module(
    EXECUTE_DIR / "fresh_10_fault_recovery_common.py",
    "fresh_10_fault_recovery_common_runtime",
)


class _DriverError(Exception):
    def __init__(self, sqlstate: str, message: str):
        super().__init__(message)
        self.pgcode = sqlstate


EXCEPTION_SPECS = {
    "08006": ("connection failure", "08006"),
    "57P01": ("administrative shutdown", "57P01"),
    "57P02": ("crash shutdown", "57P02"),
    "57P03": ("cannot connect now", "57P03"),
    "40P01": ("deadlock detected", "40P01"),
    "40001": ("could not serialize access", "40001"),
    "55P03": ("could not obtain lock on row", "55P03"),
    "42701": ("column already exists", "42701"),
    "42P07": ("object already exists", "42P07"),
    "42704": ("object does not exist", "42704"),
    "23505": ("duplicate key violates unique constraint", "23505"),
    "23502": ("null value violates not-null constraint", "23502"),
    "42601": ("syntax error", "42601"),
    # The incidental word is deliberate: an explicit non-08 SQLSTATE must win
    # over the connection-text fallback.
    "22P02": ("invalid integer from connection-shaped input", "22P02"),
    "2006": ("server has gone away", 2006),
    "2013": ("lost connection during query", 2013),
    "1213": ("deadlock found when trying to get lock", 1213),
    "1205": ("lock wait timeout exceeded", 1205),
    "1060": ("duplicate column", 1060),
    "1061": ("duplicate key name", 1061),
    "1091": ("can't drop missing object", 1091),
    "1062": ("duplicate entry", 1062),
    "1048": ("column cannot be null", 1048),
    "1064": ("syntax error", 1064),
    "1366": ("incorrect integer value", 1366),
}


def make_error(code: str) -> OperationalError:
    message, native = EXCEPTION_SPECS[code]
    if isinstance(native, int):
        return OperationalError(native, message)
    wrapped = OperationalError(message)
    wrapped.__cause__ = _DriverError(native, message)
    return wrapped


def exercise_connection_retry(
    group: str,
    failure_codes: list[str],
    *,
    max_retries: int = 3,
    succeed: bool = True,
) -> dict[str, Any]:
    from api.db import db_models

    failures = [make_error(code) for code in failure_codes]
    attempts = 0
    reconnects = 0
    sleeps: list[float] = []
    raised: str | None = None
    returned: Any = None

    if group == "experiment":

        class _Base:
            def __init__(self, *args, **kwargs):
                self._failures = list(failures)

            def execute_sql(self, sql, params=None, commit=True):
                nonlocal attempts
                attempts += 1
                if self._failures:
                    raise self._failures.pop(0)
                if not succeed:
                    raise make_error(failure_codes[-1])
                return "baseline-result"

            def close(self):
                return None

            def connect(self):
                nonlocal reconnects
                reconnects += 1

        retry_mixin = getattr(
            db_models,
            "PsycopgRetryMixin",
            db_models.GaussDBPsycopgRetryMixin,
        )

        class _Probe(retry_mixin, _Base):
            database_display_name = "GaussDB test probe"

        probe = _Probe(max_retries=max_retries, retry_delay=0.1)
        original_sleep = db_models.time.sleep
        db_models.time.sleep = sleeps.append
        try:
            try:
                returned = probe.execute_sql("SELECT 1")
            except Exception as error:
                raised = type(error).__name__
        finally:
            db_models.time.sleep = original_sleep
    elif group == "control":
        original_execute = db_models.PooledMySQLDatabase.execute_sql
        original_sleep = db_models.time.sleep

        def fake_execute(_self, _sql, _params=None, _commit=True):
            nonlocal attempts
            attempts += 1
            if failures:
                raise failures.pop(0)
            if not succeed:
                raise make_error(failure_codes[-1])
            return "baseline-result"

        probe = object.__new__(db_models.RetryingPooledMySQLDatabase)
        probe.max_retries = max_retries
        probe.retry_delay = 0.1

        def reconnect(_self):
            nonlocal reconnects
            reconnects += 1

        probe._handle_connection_loss = MethodType(reconnect, probe)
        db_models.PooledMySQLDatabase.execute_sql = fake_execute
        db_models.time.sleep = sleeps.append
        try:
            try:
                returned = db_models.RetryingPooledMySQLDatabase.execute_sql(probe, "SELECT 1")
            except Exception as error:
                raised = type(error).__name__
        finally:
            db_models.PooledMySQLDatabase.execute_sql = original_execute
            db_models.time.sleep = original_sleep
    else:
        raise ValueError(f"unknown group: {group}")

    return {
        "attempts": attempts,
        "reconnects": reconnects,
        "sleeps": sleeps,
        "returned": returned,
        "raised": raised,
    }


def exercise_transaction_retry(group: str, error_code: str, *, failures: int) -> dict[str, Any]:
    from api.db.services import common_service
    from common import settings

    attempts = 0
    sleeps: list[float] = []
    committed_values: list[str] = []

    @common_service.retry_deadlock_operation(max_retries=3, retry_delay=0.1)
    def operation():
        nonlocal attempts
        attempts += 1
        if attempts <= failures:
            raise make_error(error_code)
        committed_values.append("committed-once")
        return committed_values[-1]

    original_sleep = common_service.time.sleep
    original_database_type = settings.DATABASE_TYPE
    common_service.time.sleep = sleeps.append
    settings.DATABASE_TYPE = "gaussdb" if group == "experiment" else "mysql"
    try:
        returned = operation()
    finally:
        common_service.time.sleep = original_sleep
        settings.DATABASE_TYPE = original_database_type
    return {
        "group": group,
        "attempts": attempts,
        "sleeps": sleeps,
        "committed_values": committed_values,
        "returned": returned,
    }


def classifier_observation(group: str, label: str) -> dict[str, Any]:
    codes = {
        "control": {
            "duplicate_column": "1060",
            "duplicate_object": "1061",
            "undefined_object": "1091",
            "unique": "1062",
            "not_null": "1048",
            "syntax": "1064",
            "type_conversion": "1366",
        },
        "experiment": {
            "duplicate_column": "42701",
            "duplicate_object": "42P07",
            "undefined_object": "42704",
            "unique": "23505",
            "not_null": "23502",
            "syntax": "42601",
            "type_conversion": "22P02",
        },
    }
    try:
        code = codes[group][label]
    except KeyError as exc:
        raise ValueError(f"unknown classifier input: {group}/{label}") from exc
    error = make_error(code)
    duplicate_column = ERROR_UTILS.is_duplicate_column_error(error)
    duplicate_object = ERROR_UTILS.is_duplicate_object_error(error)
    undefined_object = ERROR_UTILS.is_undefined_object_error(error)
    expected_helper = {
        "duplicate_column": duplicate_column,
        "duplicate_object": duplicate_object,
        "undefined_object": undefined_object,
    }.get(label)
    return {
        "native_code": code,
        "sqlstate": ERROR_UTILS.sqlstate_from_exception(error),
        "errno": ERROR_UTILS.mysql_errno_from_exception(error),
        "connection_error": ERROR_UTILS.is_psycopg_connection_error(error),
        "retryable_transaction": ERROR_UTILS.is_retryable_transaction_error(error),
        "duplicate_column": duplicate_column,
        "duplicate_object": duplicate_object,
        "undefined_object": undefined_object,
        "idempotent_ddl": duplicate_column or duplicate_object or undefined_object,
        "matched": expected_helper,
    }


def evaluate_nonretry_contract(observed: dict[str, Any]) -> bool:
    return observed.get("attempts") == 1 and observed.get("reconnects") == 0 and observed.get("raised") is not None


def _connection_contract(observed: dict[str, Any], failures: int) -> bool:
    return (
        observed.get("attempts") == failures + 1
        and observed.get("reconnects") == failures
        and observed.get("sleeps") == [0.1 * (2**attempt) for attempt in range(failures)]
        and observed.get("returned") == "baseline-result"
        and observed.get("raised") is None
    )


def _run_001_group(group: str) -> dict[str, Any]:
    code = "2006" if group == "control" else "08006"
    observed = exercise_connection_retry(group, [code], max_retries=2)
    return COMMON.pass_or_fail(
        _connection_contract(observed, 1),
        [{"name": "invoke_real_metadata_retry_adapter", **observed}],
        {"native_code": code, "bounded_recovery": True},
        "TC-FR-001-CONNECTION-RETRY",
        f"{group} metadata connection retry did not recover within its budget",
        code_location="api/db/db_models.py:PsycopgRetryMixin.execute_sql",
    )


def _run_002_group(group: str) -> dict[str, Any]:
    codes = ["2006", "2013"] if group == "control" else ["57P01", "57P02", "57P03"]
    recovered = {code: exercise_connection_retry(group, [code], max_retries=1) for code in codes}
    exhaustion_code = codes[-1]
    exhausted = exercise_connection_retry(
        group,
        [exhaustion_code, exhaustion_code],
        max_retries=1,
        succeed=False,
    )
    passed = all(_connection_contract(item, 1) for item in recovered.values()) and (
        exhausted["attempts"] == 2 and exhausted["reconnects"] == 1 and exhausted["raised"] == "OperationalError" and exhausted["returned"] is None
    )
    return COMMON.pass_or_fail(
        passed,
        [
            {"name": "recover_each_service_shutdown_class", "results": recovered},
            {"name": "exhaust_retry_budget", **exhausted},
        ],
        {"codes": codes, "bounded_failure": True, "sensitive_match_count": 0},
        "TC-FR-002-SHUTDOWN-RETRY",
        f"{group} service-shutdown retry classification or exhaustion is incorrect",
        code_location="api/db/db_error_utils.py:is_psycopg_connection_error",
    )


def _transaction_case(case_id: str, group: str, control_code: str, experiment_code: str) -> dict[str, Any]:
    code = control_code if group == "control" else experiment_code
    observed = exercise_transaction_retry(group, code, failures=2)
    passed = observed["attempts"] == 3 and observed["sleeps"] == [0.1, 0.2] and observed["committed_values"] == ["committed-once"]
    return COMMON.pass_or_fail(
        passed,
        [{"name": "retry_complete_operation", **observed}],
        {"native_code": code, "max_attempts": 3, "commits": 1},
        f"{case_id}-TRANSACTION-RETRY",
        f"{group} transaction conflict did not retry the complete operation safely",
        code_location="api/db/services/common_service.py:retry_deadlock_operation",
    )


def _run_003_group(group: str) -> dict[str, Any]:
    return _transaction_case("TC-FR-003", group, "1213", "40P01")


def _run_004_group(group: str) -> dict[str, Any]:
    return _transaction_case("TC-FR-004", group, "1213", "40001")


def _live_lock_observation(group: str) -> dict[str, Any]:
    table = f"{COMMON.RESOURCE_PREFIX}_005_{group}"
    first = COMMON.open_metadata_database(group, writable=True)
    second = COMMON.open_metadata_database(group, writable=True)
    caught: Exception | None = None
    cleanup = False
    recovered = False
    try:
        with first.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
            if group == "control":
                cursor.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY, value_int INT)")
            else:
                cursor.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value_int INTEGER)")
            cursor.execute(f"INSERT INTO {table} (id,value_int) VALUES (1,1)")
        first.commit()
        with first.cursor() as cursor:
            if group == "control":
                cursor.execute("SET SESSION innodb_lock_wait_timeout=1")
                cursor.execute(f"UPDATE {table} SET value_int=2 WHERE id=1")
            else:
                cursor.execute(f"SELECT id FROM {table} WHERE id=1 FOR UPDATE")
        with second.cursor() as cursor:
            if group == "control":
                cursor.execute("SET SESSION innodb_lock_wait_timeout=1")
                cursor.execute(f"UPDATE {table} SET value_int=3 WHERE id=1")
            else:
                cursor.execute(f"SELECT id FROM {table} WHERE id=1 FOR UPDATE NOWAIT")
    except Exception as error:
        caught = error
    finally:
        first.rollback()
        second.rollback()
        try:
            with second.cursor() as cursor:
                cursor.execute("SELECT 1")
                recovered = cursor.fetchone()[0] == 1
            second.rollback()
        except Exception:
            recovered = False
        try:
            with first.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {table}")
            first.commit()
            cleanup = True
        finally:
            first.close()
            second.close()
    if caught is None:
        return {
            "sqlstate": None,
            "errno": None,
            "retryable": False,
            "recovered": recovered,
            "cleanup": cleanup,
        }
    return {
        "sqlstate": ERROR_UTILS.sqlstate_from_exception(caught),
        "errno": ERROR_UTILS.mysql_errno_from_exception(caught),
        "retryable": ERROR_UTILS.is_retryable_transaction_error(caught),
        "recovered": recovered,
        "cleanup": cleanup,
        "error_class": type(caught).__name__,
    }


def _run_005_group(group: str) -> dict[str, Any]:
    live = _live_lock_observation(group)
    deterministic = exercise_transaction_retry("experiment", "55P03", failures=1) if group == "experiment" else None
    if group == "control":
        passed = live.get("errno") == 1205 and live.get("retryable") is False and live.get("recovered") is True and live.get("cleanup") is True
    else:
        passed = (
            live.get("sqlstate") == "55P03"
            and live.get("retryable") is True
            and live.get("recovered") is True
            and live.get("cleanup") is True
            and deterministic is not None
            and deterministic.get("attempts") == 2
            and deterministic.get("committed_values") == ["committed-once"]
        )
    return COMMON.pass_or_fail(
        passed,
        [
            {"name": "trigger_live_isolated_row_lock_conflict", **live},
            {
                "name": "exercise_gaussdb_complete_operation_retry",
                "result": deterministic or {"not_applicable": True},
            },
        ],
        {
            "control_errno": 1205,
            "experiment_sqlstate": "55P03",
            "group_specific_semantics": True,
        },
        "TC-FR-005-ROW-LOCK",
        f"{group} row-lock behavior or post-error recovery does not match its adapter",
        code_location="api/db/services/common_service.py:_is_deadlock_error",
    )


def _idempotent_case(case_id: str, label: str, group: str) -> dict[str, Any]:
    observed = classifier_observation(group, label)
    unrelated = classifier_observation(group, "unique")
    helper_key = label
    passed = observed.get("matched") is True and unrelated.get(helper_key) is False
    return COMMON.pass_or_fail(
        passed,
        [
            {"name": "classify_expected_idempotent_error", **observed},
            {"name": "reject_unrelated_ddl_error", **unrelated},
        ],
        {"expected_helper": label, "unrelated_error_propagates": True},
        f"{case_id}-IDEMPOTENT-CLASSIFIER",
        f"{group} DDL idempotency classifier accepted the wrong error set",
        code_location=f"api/db/db_error_utils.py:is_{label}_error",
    )


def _run_006_group(group: str) -> dict[str, Any]:
    return _idempotent_case("TC-FR-006", "duplicate_column", group)


def _run_007_group(group: str) -> dict[str, Any]:
    return _idempotent_case("TC-FR-007", "duplicate_object", group)


def _run_008_group(group: str) -> dict[str, Any]:
    return _idempotent_case("TC-FR-008", "undefined_object", group)


def _nonretry_case(case_id: str, label: str, group: str, control_code: str, experiment_code: str) -> dict[str, Any]:
    code = control_code if group == "control" else experiment_code
    classifier = classifier_observation(group, label)
    retry = exercise_connection_retry(group, [code], max_retries=2)
    passed = classifier["retryable_transaction"] is False and classifier["idempotent_ddl"] is False and evaluate_nonretry_contract(retry)
    return COMMON.pass_or_fail(
        passed,
        [
            {"name": "classify_nonretry_error", **classifier},
            {"name": "invoke_real_connection_retry_adapter", **retry},
        ],
        {"native_code": code, "attempts": 1, "blind_replay": False},
        f"{case_id}-NONRETRY",
        f"{group} non-retryable {label} error was retried or treated as idempotent",
        code_location="api/db/db_error_utils.py:is_psycopg_connection_error",
    )


def _run_009_group(group: str) -> dict[str, Any]:
    return _nonretry_case("TC-FR-009", "unique", group, "1062", "23505")


def _run_010_group(group: str) -> dict[str, Any]:
    return _nonretry_case("TC-FR-010", "not_null", group, "1048", "23502")


def _run_011_group(group: str) -> dict[str, Any]:
    return _nonretry_case("TC-FR-011", "syntax", group, "1064", "42601")


def _run_012_group(group: str) -> dict[str, Any]:
    return _nonretry_case("TC-FR-012", "type_conversion", group, "1366", "22P02")


GROUP_RUNNERS: dict[str, Callable[[str], dict[str, Any]]] = {
    "TC-FR-001": _run_001_group,
    "TC-FR-002": _run_002_group,
    "TC-FR-003": _run_003_group,
    "TC-FR-004": _run_004_group,
    "TC-FR-005": _run_005_group,
    "TC-FR-006": _run_006_group,
    "TC-FR-007": _run_007_group,
    "TC-FR-008": _run_008_group,
    "TC-FR-009": _run_009_group,
    "TC-FR-010": _run_010_group,
    "TC-FR-011": _run_011_group,
    "TC-FR-012": _run_012_group,
}


def get_runners(titles: dict[str, str]) -> dict[str, Callable[[], dict[str, Any]]]:
    result: dict[str, Callable[[], dict[str, Any]]] = {}
    for case_id in CASE_IDS:
        if case_id not in titles:
            continue
        execute_group = GROUP_RUNNERS[case_id]
        title = titles[case_id]
        result[case_id] = lambda current_id=case_id, current_title=title, current_execute=execute_group: COMMON.run_paired_case(current_id, current_title, current_execute)
    return result
