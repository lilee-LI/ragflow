import importlib.util
from pathlib import Path


EXECUTE_DIR = Path(__file__).resolve().parent
MODULE_PATH = EXECUTE_DIR / "fresh_runner_result.py"
RUNNER_PATHS = (
    EXECUTE_DIR / "fresh_01_startup_migration.py",
    EXECUTE_DIR / "fresh_02_user_management.py",
    EXECUTE_DIR / "fresh_04_dataset_document.py",
    EXECUTE_DIR / "fresh_05_chat_session_agent.py",
    EXECUTE_DIR / "fresh_07_memory_store.py",
    EXECUTE_DIR / "fresh_10_fault_recovery.py",
)


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_runner_result", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pair_exit_code_continues_only_for_pair_pass_or_two_recorded_failures():
    module = load_module()

    assert module.pair_exit_code({"pair_status": "PASS"}) == 0
    assert (
        module.pair_exit_code(
            {
                "pair_status": "FAIL",
                "groups": [
                    {"group": "control", "status": "FAIL"},
                    {"group": "experiment", "status": "FAIL"},
                ],
            }
        )
        == 0
    )
    assert (
        module.pair_exit_code(
            {
                "pair_status": "FAIL",
                "groups": [
                    {"group": "control", "status": "PASS"},
                    {"group": "experiment", "status": "FAIL"},
                ],
            }
        )
        == 1
    )
    assert (
        module.pair_exit_code(
            {
                "pair_status": "FAIL",
                "groups": [
                    {"group": "control", "status": "FAIL"},
                    {"group": "experiment", "status": "PASS"},
                ],
            }
        )
        == 1
    )
    assert module.pair_exit_code({"pair_status": "FAIL"}) == 1
    assert module.pair_exit_code({"pair_status": "BLOCKED"}) == 1
    assert module.pair_exit_code({}) == 1


def test_all_formal_runners_propagate_pair_failure_to_the_process_exit_code():
    for path in RUNNER_PATHS:
        source = path.read_text(encoding="utf-8")

        assert "fresh_runner_result import pair_exit_code" in source, path.name
        assert "def main() -> int:" in source, path.name
        assert "return pair_exit_code(result)" in source, path.name
        assert "raise SystemExit(main())" in source, path.name
