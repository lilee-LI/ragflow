import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_09_connector_scheduling.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_09_scheduling_tested", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_registry_contains_only_interval_adaptation_cases():
    module = load_module()

    assert list(module.GROUP_RUNNERS) == ["TC-CONN-015", "TC-CONN-016"]


def test_due_scheduler_contract_requires_group_dialect_and_exact_target():
    module = load_module()
    common = {
        "returned_ids": ["target"],
        "target_id": "target",
        "excluded_ids_absent": True,
        "fixture_restored": True,
        "cleanup_succeeded": True,
    }

    assert module.due_scheduler_contract_ok(
        "control",
        {
            **common,
            "expression": "NOW() - INTERVAL `t2`.`refresh_freq` MINUTE",
        },
        frequency_field="refresh_freq",
    )
    assert module.due_scheduler_contract_ok(
        "experiment",
        {
            **common,
            "expression": ("NOW() AT TIME ZONE 'Asia/Shanghai' - (t2.prune_freq * INTERVAL '1 minute')"),
        },
        frequency_field="prune_freq",
    )
    assert not module.due_scheduler_contract_ok(
        "experiment",
        {
            **common,
            "expression": "NOW() - INTERVAL `t2`.`prune_freq` MINUTE",
        },
        frequency_field="prune_freq",
    )


def test_quiesced_sync_service_stops_and_restores_only_current_group():
    module = load_module()
    calls = []

    class Registry:
        def __init__(self, _path):
            pass

        def status(self):
            return {
                "control:sync": {"alive": True, "identity_matches": True},
                "experiment:sync": {"alive": True, "identity_matches": True},
            }

        def stop(self, label):
            calls.append(("stop", label))
            return {"stopped": True}

    class Manager:
        STATE_PATH = Path("state.json")
        ProcessRegistry = Registry

        @staticmethod
        def start_service(group, service):
            calls.append(("start", f"{group}:{service}"))
            return {"pid": 123}

    module.MANAGER = Manager

    with module.quiesced_sync_service("control") as state:
        assert calls == [("stop", "control:sync")]
        assert state["was_running"] is True
        assert state["stopped"] is True

    assert calls == [
        ("stop", "control:sync"),
        ("start", "control:sync"),
    ]
    assert state["restored"] is True


def test_linked_fixture_cleanup_unlinks_before_deleting_parent_objects():
    module = load_module()
    calls = []
    state = {"connector": True, "knowledgebase": True, "link": True}

    def entity_count(_group, table, _entity_id):
        return int(state[table])

    def link_count(_group, _connector_id, _dataset_id):
        return int(state["link"])

    class Common:
        @staticmethod
        def http_request(_case_id, _group, label, method, path, **kwargs):
            calls.append((label, method, path, kwargs.get("payload")))
            if method == "PUT":
                state["link"] = False
            elif method == "DELETE" and path.startswith("/connectors/"):
                state["connector"] = False
            elif method == "DELETE" and path == "/datasets":
                state["knowledgebase"] = False
            return {"code": 0}

    module.COMMON = Common
    module._entity_count = entity_count
    module._connector_link_count = link_count

    assert module._cleanup_linked_fixture(
        "TC-CONN-015",
        "control",
        {"auth": "redacted"},
        {"connector_id": "connector", "dataset_id": "dataset", "preclean": True},
    )
    assert calls == [
        (
            "unlink_connector",
            "PUT",
            "/datasets/dataset",
            {"connectors": []},
        ),
        ("cleanup_connector", "DELETE", "/connectors/connector", None),
        ("cleanup_dataset", "DELETE", "/datasets", {"ids": ["dataset"]}),
    ]
