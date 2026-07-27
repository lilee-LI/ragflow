import json
import os
import time

import requests


base_url = os.getenv("GPU_PROOF_BASE_URL", "http://127.0.0.1:18080").rstrip("/")
expected_model = "BAAI/bge-large-en-v1.5"
expected_dimensions = 1024
health_response = requests.get(f"{base_url}/health", timeout=30)
health_response.raise_for_status()
health = health_response.json()
if health.get("provider") != "CUDAExecutionProvider":
    raise RuntimeError(f"Unexpected embedding provider: {health!r}")
if health.get("model") != expected_model or health.get("dimensions") != expected_dimensions:
    raise RuntimeError(f"Unexpected embedding model contract: {health!r}")

rows = int(os.getenv("GPU_PROOF_ROWS", "1"))
texts = [(f"GaussDB E2E CUDA verification record {index}. Northstar Logistics validates cold-chain telemetry, retrieval grounding, and durable vector indexing. ") * 80 for index in range(rows)]
started = time.monotonic()
response = requests.post(f"{base_url}/embed", json={"inputs": texts}, timeout=600)
response.raise_for_status()
vectors = response.json()
if len(vectors) != rows or any(len(vector) != expected_dimensions for vector in vectors):
    raise RuntimeError(f"Unexpected embedding shape: rows={len(vectors)} dimensions={[len(vector) for vector in vectors]}")
print(
    json.dumps(
        {
            "base_url": base_url,
            "provider": health["provider"],
            "model": health["model"],
            "device": health.get("device"),
            "rows": len(vectors),
            "dimensions": len(vectors[0]),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    ),
    flush=True,
)
