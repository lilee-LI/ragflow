import importlib.util
import socket
import socketserver
import sys
import threading
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_10_connection_recovery.py")
EXPECTED_IDS = [f"TC-FR-{number:03d}" for number in range(18, 25)]


def load_module():
    name = "fresh_10_connection_recovery_contract"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while payload := self.request.recv(65536):
            self.request.sendall(payload)


def test_maintenance_work_mem_retries_only_expected_error():
    module = load_module()

    recovered = module.maintenance_retry_observation(failures=2)
    unrelated = module.maintenance_retry_observation(failures=1, unrelated_error=True)

    assert recovered["attempts"] == 3
    assert recovered["work_mem"] == ["1GB", "2GB", "4GB"]
    assert recovered["created"] is True
    assert recovered["raised"] is None
    assert unrelated["attempts"] == 1
    assert unrelated["created"] is False
    assert unrelated["raised"] == "GaussDBConnectionError"


def test_isolated_proxy_down_then_recovers_and_closes():
    module = load_module()
    upstream = socketserver.ThreadingTCPServer(("127.0.0.1", 0), EchoHandler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    proxy = module.IsolatedFaultProxy("127.0.0.1", upstream.server_address[1])
    try:
        with proxy:
            proxy.configure("down")
            with socket.create_connection(("127.0.0.1", proxy.port), timeout=1) as client:
                client.sendall(b"must-not-arrive")
                try:
                    closed_payload = client.recv(1)
                except ConnectionResetError:
                    closed_payload = b""
                assert closed_payload == b""
            assert proxy.status()["fault_hits"] == 1

            proxy.configure("normal")
            with socket.create_connection(("127.0.0.1", proxy.port), timeout=1) as client:
                client.sendall(b"recovered")
                assert client.recv(9) == b"recovered"
        assert proxy.closed is True
        assert not proxy.thread_alive
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=2)


def test_bad_connection_is_discarded_and_validation_is_bounded():
    module = load_module()

    recovered = module.stale_pool_observation(double_bad=False)
    exhausted = module.stale_pool_observation(double_bad=True)

    assert recovered == {
        "get_calls": 2,
        "validation_calls": 2,
        "discard_close_flags": [True],
        "returned_valid": True,
        "raised": None,
    }
    assert exhausted["get_calls"] == 2
    assert exhausted["validation_calls"] == 2
    assert exhausted["discard_close_flags"] == [True, True]
    assert exhausted["returned_valid"] is False
    assert exhausted["raised"] == "GaussDBConnectionError"


def test_control_pool_uses_positive_bounded_wait():
    module = load_module()

    assert 0 < module.CONTROL_POOL_TIMEOUT_SECONDS <= 1


def test_tls_detection_uses_current_driver_surfaces():
    module = load_module()

    control = type("ControlConnection", (), {"_secure": True})()
    info = type("Info", (), {"ssl_in_use": True})()
    experiment = type("ExperimentConnection", (), {"info": info})()

    assert module.connection_tls_in_use("control", control) is True
    assert module.connection_tls_in_use("experiment", experiment) is True


def test_connection_contract_requires_fault_hit_and_new_success():
    module = load_module()

    assert module.recovery_contract_ok(
        {
            "fault_hits": 1,
            "fault_error_class": "OperationalError",
            "recovery_value": 1,
            "proxy_closed": True,
        }
    )
    assert not module.recovery_contract_ok(
        {
            "fault_hits": 0,
            "fault_error_class": None,
            "recovery_value": 1,
            "proxy_closed": True,
        }
    )


def test_reset_contract_distinguishes_transparent_transport_replacement():
    module = load_module()
    base = {
        "fault_hits": 1,
        "fault_error_class": "OperationalError",
        "recovery_value": 1,
        "proxy_closed": True,
    }

    assert module.metadata_reset_contract_ok("control", {**base, "fault_errno": 2013})
    assert module.metadata_reset_contract_ok("experiment", {**base, "connection_classified": True})
    assert not module.metadata_reset_contract_ok("experiment", {**base, "connection_classified": False})
    assert module.docstore_reset_contract_ok(
        "control",
        {
            "reset_connections": 1,
            "old_connection_failed": False,
            "replacement_transport_opened": True,
            "new_call_recovered": True,
            "proxy_closed": True,
        },
    )
    assert not module.docstore_reset_contract_ok(
        "control",
        {
            "reset_connections": 1,
            "old_connection_failed": False,
            "replacement_transport_opened": False,
            "new_call_recovered": True,
            "proxy_closed": True,
        },
    )


def test_domain_registers_only_connection_recovery_cases():
    module = load_module()
    titles = {case_id: case_id for case_id in EXPECTED_IDS}

    runners = module.get_runners(titles)

    assert list(runners) == EXPECTED_IDS
    assert all(callable(item) for item in runners.values())


def test_connection_recovery_uses_namespaced_runtime_directory():
    module = load_module()

    assert module.RUNTIME_DIR.name == module.COMMON.BATCH_ID
