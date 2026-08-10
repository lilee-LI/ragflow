import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_02_cleanup.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_02_cleanup", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_target_users_are_limited_to_um_batch_and_baseline_owner_is_last():
    module = load_module()
    users = [
        {"email": "um001@fresh.invalid", "is_superuser": False},
        {"email": "sm001@fresh.invalid", "is_superuser": False},
        {"email": "um050member@fresh.invalid", "is_superuser": False},
        {"email": "someone@example.com", "is_superuser": False},
    ]

    targets = module.target_users_in_cleanup_order(users)

    assert [item["email"] for item in targets] == [
        "um050member@fresh.invalid",
        "um001@fresh.invalid",
    ]
