import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_protocol_probe.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_protocol_probe", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_summarize_proxy_status_exposes_only_safe_aggregate_fields():
    module = load_module()
    status = {
        "status": "running",
        "proxies": {
            "metadata": {
                "mode": "normal",
                "accepted": 3,
                "active": 1,
                "upstream_errors": 1,
                "fault_hits": 0,
                "upstream_host": "must-not-leak.example",
            },
            "docstore": {
                "mode": "normal",
                "accepted": 2,
                "active": 0,
                "upstream_errors": 0,
                "fault_hits": 2,
                "upstream_host": "must-not-leak.example",
            },
        },
    }

    summary = module.summarize_proxy_status(status, expected_count=2)

    assert summary == {
        "running": True,
        "proxy_count": 2,
        "all_normal": True,
        "accepted_total": 5,
        "active_total": 1,
        "upstream_errors_total": 1,
        "fault_hits_total": 2,
    }
    assert "must-not-leak" not in str(summary)


def test_summarize_proxy_status_detects_missing_or_faulted_proxy():
    module = load_module()

    summary = module.summarize_proxy_status(
        {
            "status": "running",
            "proxies": {"only": {"mode": "down", "accepted": 0, "active": 0}},
        },
        expected_count=8,
    )

    assert summary["proxy_count"] == 1
    assert summary["running"] is False
    assert summary["all_normal"] is False


def test_summarize_proxy_details_omits_all_endpoint_fields():
    module = load_module()
    status = {
        "proxies": {
            "experiment_docstore": {
                "mode": "normal",
                "accepted": 4,
                "completed": 1,
                "active": 3,
                "upstream_errors": 1,
                "fault_hits": 0,
                "resets": 0,
                "listen_host": "127.0.0.1",
                "listen_port": 12345,
                "upstream_host": "must-not-leak.example",
            }
        }
    }

    details = module.summarize_proxy_details(status)

    assert details == {
        "experiment_docstore": {
            "mode": "normal",
            "accepted": 4,
            "completed": 1,
            "active": 3,
            "upstream_errors": 1,
            "fault_hits": 0,
            "resets": 0,
        }
    }
    assert "host" not in str(details)
    assert "port" not in str(details)
