import json
import os

import requests

from test.playwright.conftest import _rsa_encrypt_password


base_url = os.environ["RAGFLOW_BASE_URL"].rstrip("/")
email = os.environ["SEEDED_USER_EMAIL"]
password = os.environ["SEEDED_USER_PASSWORD"]
encrypted_password = _rsa_encrypt_password(password)
session = requests.Session()


def request(method: str, path: str, payload: dict | None = None, headers: dict | None = None):
    response = session.request(method, f"{base_url}{path}", json=payload, headers=headers, timeout=120)
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} returned non-object JSON")
    return response, data


def response_data(payload: dict) -> dict:
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def response_list(payload: dict) -> list:
    data = payload.get("data")
    return data if isinstance(data, list) else []


def require_ok(payload: dict, action: str, *, allowed_message: str = "") -> None:
    if payload.get("code") == 0:
        return
    message = str(payload.get("message") or "")
    if allowed_message and allowed_message.lower() in message.lower():
        return
    raise RuntimeError(f"{action} failed: code={payload.get('code')} message={message}")


login_payload = {"email": email, "password": encrypted_password}
login_response, login = request("POST", "/api/v1/auth/login", login_payload)
if login.get("code") != 0:
    _, registration = request(
        "POST",
        "/api/v1/users",
        {
            **login_payload,
            "nickname": "gaussdb-e2e",
        },
    )
    if registration.get("code") != 0:
        login_response, login = request("POST", "/api/v1/auth/login", login_payload)
        if login.get("code") != 0:
            raise RuntimeError(f"Unable to log in fixed GaussDB E2E account after registration attempt: register_code={registration.get('code')} login_code={login.get('code')}")
    else:
        login_response, login = request("POST", "/api/v1/auth/login", login_payload)
        if login.get("code") != 0:
            raise RuntimeError(f"Fixed GaussDB E2E account registration succeeded but login code={login.get('code')}")

auth_header = login_response.headers.get("Authorization")
if not auth_header:
    raise RuntimeError("Fixed GaussDB E2E account login did not return Authorization header")
headers = {"Authorization": auth_header}

_, models_payload = request("GET", "/api/v1/users/me/models", headers=headers)
models = response_data(models_payload)
tenant_id = models.get("tenant_id")
if not tenant_id:
    raise RuntimeError("Fixed GaussDB E2E account model payload has no tenant_id")

target_models = {
    "tenant_id": tenant_id,
    "llm_id": models.get("llm_id") or "glm-4-flash@ZHIPU-AI",
    "embd_id": "BAAI/bge-large-en-v1.5@Builtin",
    "img2txt_id": models.get("img2txt_id") or "",
    "asr_id": models.get("asr_id") or "",
    "rerank_id": models.get("rerank_id") or "",
    "tts_id": models.get("tts_id") or "",
}
_, update_payload = request("PATCH", "/api/v1/users/me/models", target_models, headers)
if update_payload.get("code") != 0:
    raise RuntimeError(f"Unable to select CUDA TEI embedding model: code={update_payload.get('code')}")

# The current chat API persists the tenant_model UUID, while the legacy
# /v1/llm endpoint only populates the old provider table. Prepare the same
# provider/model through the current user-facing API so chat creation has a
# real default model in both fresh and upgraded test accounts.
chat_provider = "ZHIPU-AI"
chat_instance = "gaussdb-e2e"
chat_model = "glm-4.5-air"
_, add_provider_payload = request(
    "PUT",
    "/api/v1/providers",
    {"provider_name": chat_provider},
    headers,
)
require_ok(add_provider_payload, "Add current-model provider", allowed_message="already exists")

_, instances_payload = request(
    "GET",
    f"/api/v1/providers/{chat_provider}/instances",
    headers=headers,
)
require_ok(instances_payload, "List current-model provider instances")
instances = response_list(instances_payload)
instance = next((item for item in instances if item.get("instance_name") == chat_instance), None)
if instance is None:
    _, add_instance_payload = request(
        "POST",
        f"/api/v1/providers/{chat_provider}/instances",
        {
            "instance_name": chat_instance,
            "api_key": os.environ["ZHIPU_AI_API_KEY"],
            "base_url": "",
            "region": "default",
            "model_info": [
                {
                    "model_name": chat_model,
                    "model_type": ["chat"],
                    "max_tokens": 131072,
                }
            ],
        },
        headers,
    )
    require_ok(add_instance_payload, "Create current-model provider instance")
else:
    _, instance_models_payload = request(
        "GET",
        f"/api/v1/providers/{chat_provider}/instances/{chat_instance}/models",
        headers=headers,
    )
    require_ok(instance_models_payload, "List current-model provider models")
    if not any(item.get("name") == chat_model for item in response_list(instance_models_payload)):
        _, add_model_payload = request(
            "POST",
            f"/api/v1/providers/{chat_provider}/instances/{chat_instance}/models",
            {
                "model_name": chat_model,
                "model_type": ["chat"],
                "max_tokens": 131072,
            },
            headers,
        )
        require_ok(add_model_payload, "Add current chat model")

_, added_models_payload = request("GET", "/api/v1/models?type=chat", headers=headers)
require_ok(added_models_payload, "List current chat models")
chat_model_row = next(
    (item for item in response_list(added_models_payload) if item.get("provider_name") == chat_provider and item.get("instance_name") == chat_instance and item.get("name") == chat_model),
    None,
)
if not chat_model_row or not chat_model_row.get("model_id"):
    raise RuntimeError("Current chat model was not returned with a tenant_model id")

_, default_model_payload = request(
    "PATCH",
    "/api/v1/models/default",
    {"model_type": "chat", "model_id": chat_model_row["model_id"]},
    headers,
)
require_ok(default_model_payload, "Select current default chat model")

_, verified_payload = request("GET", "/api/v1/users/me/models", headers=headers)
verified_models = response_data(verified_payload)
if verified_models.get("embd_id") != target_models["embd_id"]:
    raise RuntimeError(f"Unexpected embedding model after update: {verified_models.get('embd_id')!r}")

_, verified_default_payload = request("GET", "/api/v1/models/default", headers=headers)
require_ok(verified_default_payload, "Verify current default chat model")
default_models = response_data(verified_default_payload).get("models") or []
if not any(item.get("model_id") == chat_model_row["model_id"] and item.get("model_name") == chat_model and item.get("model_type") == "chat" for item in default_models):
    raise RuntimeError("Current default chat model verification failed")

print(
    json.dumps(
        {
            "fixed_e2e_account": "ready",
            "llm_id": verified_models.get("llm_id"),
            "embd_id": verified_models.get("embd_id"),
            "chat_model": chat_model,
            "chat_model_id_set": True,
        }
    ),
    flush=True,
)
