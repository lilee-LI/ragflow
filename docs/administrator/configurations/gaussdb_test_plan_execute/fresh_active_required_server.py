#!/usr/bin/env python3
"""One-shot test server for the otherwise-unbound active_required decorator."""

from api.apps import app, login_required
from api.utils.api_utils import active_required, get_json_result
from common import settings


@app.get("/__test__/active-required")
@login_required
@active_required
async def active_required_probe():
    return get_json_result(data=True)


if __name__ == "__main__":
    app.run(host=settings.HOST_IP, port=settings.HOST_PORT, debug=False, use_reloader=False)
