import json
import os
import time

import requests


started = time.monotonic()
read_timeout_s = float(os.getenv("GAUSSDB_E2E_MODEL_PREFLIGHT_TIMEOUT_S", "1200"))
response = requests.post(
    "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    headers={
        "Authorization": f"Bearer {os.environ['ZHIPU_AI_API_KEY']}",
        "Content-Type": "application/json",
    },
    json={
        "model": "glm-4.5-air",
        "messages": [{"role": "user", "content": "Reply with exactly: E2E model ready"}],
        "temperature": 0,
    },
    timeout=(30, read_timeout_s),
)
payload = response.json()
choice = (payload.get("choices") or [{}])[0]
message = choice.get("message") or {}
print(
    json.dumps(
        {
            "status_code": response.status_code,
            "model": payload.get("model"),
            "has_request_id": bool(payload.get("id")),
            "answer_length": len(str(message.get("content") or "")),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error_code": (payload.get("error") or {}).get("code"),
        },
        ensure_ascii=False,
    ),
    flush=True,
)
response.raise_for_status()
