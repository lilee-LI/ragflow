#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import base64
import hmac
import json
import secrets
import signal
import ssl
import time
from contextlib import suppress
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


MAX_HTTP_BODY = 1024 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "::1"}


def append_message_record(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    path.chmod(0o600)


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("http_host") not in LOOPBACK_HOSTS or config.get("smtp_host") not in LOOPBACK_HOSTS:
        raise ValueError("listeners must use loopback")
    for key in ("http_port", "smtp_port"):
        value = int(config.get(key, 0))
        if not 1 <= value <= 65535:
            raise ValueError(f"invalid {key}")
    for key in (
        "control_token",
        "oauth_client_secret",
        "smtp_password",
    ):
        if len(str(config.get(key, ""))) < 16:
            raise ValueError(f"{key} is too short")
    for key in (
        "oauth_client_id",
        "smtp_username",
        "tls_cert",
        "tls_key",
        "messages_path",
    ):
        if not str(config.get(key, "")):
            raise ValueError(f"missing {key}")
    return config


class OAuthStore:
    def __init__(self):
        self._codes: dict[str, dict[str, Any]] = {}
        self._tokens: dict[str, dict[str, Any]] = {}

    def issue_code(self, profile: dict[str, Any]) -> str:
        code = secrets.token_urlsafe(24)
        self._codes[code] = dict(profile)
        return code

    def exchange_code(self, code: str) -> str | None:
        profile = self._codes.pop(code, None)
        if profile is None:
            return None
        token = secrets.token_urlsafe(32)
        self._tokens[token] = profile
        return token

    def profile_for_token(self, token: str) -> dict[str, Any] | None:
        profile = self._tokens.get(token)
        return dict(profile) if profile is not None else None

    def clear(self) -> None:
        self._codes.clear()
        self._tokens.clear()


def extract_message_text(raw_message: bytes) -> str:
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    if message.is_multipart():
        parts = []
        for part in message.walk():
            if part.get_content_type() == "text/plain" and part.get_content_disposition() != "attachment":
                parts.append(part.get_content())
        return "".join(parts)
    content = message.get_content()
    return content if isinstance(content, str) else content.decode("utf-8", errors="replace")


class ProtocolStub:
    def __init__(self, config: dict[str, Any]):
        self.config = validate_config(config)
        self.oauth = OAuthStore()
        self.messages: list[dict[str, Any]] = []
        self.http_server: asyncio.Server | None = None
        self.smtp_server: asyncio.Server | None = None
        self.http_requests = 0
        self.smtp_sessions = 0

    async def start(self) -> None:
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.load_cert_chain(self.config["tls_cert"], self.config["tls_key"])
        self.http_server = await asyncio.start_server(
            self._handle_http,
            self.config["http_host"],
            int(self.config["http_port"]),
        )
        self.smtp_server = await asyncio.start_server(
            self._handle_smtp,
            self.config["smtp_host"],
            int(self.config["smtp_port"]),
            ssl=tls_context,
        )

    async def close(self) -> None:
        for server in (self.http_server, self.smtp_server):
            if server is not None:
                server.close()
                await server.wait_closed()

    def _authorized(self, headers: dict[str, str]) -> bool:
        supplied = headers.get("authorization", "").removeprefix("Bearer ")
        return hmac.compare_digest(supplied, str(self.config["control_token"]))

    async def _json_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        payload: Any,
        *,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8") if content_type == "application/json" else str(payload).encode("utf-8")
        reason = {200: "OK", 201: "Created", 302: "Found", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found"}.get(status, "Error")
        lines = [
            f"HTTP/1.1 {status} {reason}",
            f"Content-Type: {content_type}",
            f"Content-Length: {len(body)}",
            "Connection: close",
        ]
        for key, value in (headers or {}).items():
            lines.append(f"{key}: {value}")
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body)
        await writer.drain()

    async def _handle_http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.http_requests += 1
        try:
            raw_headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            if len(raw_headers) > 64 * 1024:
                raise ValueError("headers too large")
            lines = raw_headers.decode("iso-8859-1").split("\r\n")
            method, target, _version = lines[0].split(" ", 2)
            headers: dict[str, str] = {}
            for line in lines[1:]:
                if not line:
                    continue
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
            length = int(headers.get("content-length", "0"))
            if length > MAX_HTTP_BODY:
                await self._json_response(writer, 400, {"error": "body too large"})
                return
            body = await reader.readexactly(length) if length else b""
            parsed = urlparse(target)
            path = parsed.path
            query = parse_qs(parsed.query)

            if method == "GET" and path == "/status":
                await self._json_response(
                    writer,
                    200,
                    {
                        "status": "running",
                        "http_requests": self.http_requests,
                        "smtp_sessions": self.smtp_sessions,
                        "message_count": len(self.messages),
                    },
                )
                return

            if path.startswith("/control/"):
                if not self._authorized(headers):
                    await self._json_response(writer, 401, {"error": "unauthorized"})
                    return
                if method == "POST" and path == "/control/oauth-codes":
                    profile = json.loads(body or b"{}")
                    if not profile.get("email") or not profile.get("nickname"):
                        await self._json_response(writer, 400, {"error": "profile required"})
                        return
                    code = self.oauth.issue_code(profile)
                    await self._json_response(writer, 201, {"code": code})
                    return
                if method == "GET" and path == "/control/messages":
                    email = (query.get("email") or [""])[0]
                    selected = [item for item in self.messages if not email or email in item["recipients"]]
                    await self._json_response(writer, 200, {"messages": selected})
                    return
                if method == "POST" and path == "/control/clear":
                    self.oauth.clear()
                    self.messages.clear()
                    message_path = Path(self.config["messages_path"])
                    message_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    message_path.write_text("", encoding="utf-8")
                    message_path.chmod(0o600)
                    await self._json_response(writer, 200, {"cleared": True})
                    return
                await self._json_response(writer, 404, {"error": "not found"})
                return

            if method == "GET" and path == "/oauth/authorize":
                await self._json_response(
                    writer,
                    200,
                    "Fresh OAuth stub authorization endpoint",
                    content_type="text/plain; charset=utf-8",
                )
                return

            if method == "POST" and path == "/oauth/token":
                form = parse_qs(body.decode("utf-8", errors="replace"))
                client_id = (form.get("client_id") or [""])[0]
                client_secret = (form.get("client_secret") or [""])[0]
                code = (form.get("code") or [""])[0]
                if not (hmac.compare_digest(client_id, str(self.config["oauth_client_id"])) and hmac.compare_digest(client_secret, str(self.config["oauth_client_secret"]))):
                    await self._json_response(writer, 401, {"error": "invalid_client"})
                    return
                token = self.oauth.exchange_code(code)
                if token is None:
                    await self._json_response(writer, 400, {"error": "invalid_grant"})
                    return
                await self._json_response(writer, 200, {"access_token": token, "token_type": "Bearer"})
                return

            if method == "GET" and path == "/oauth/userinfo":
                token = headers.get("authorization", "").removeprefix("Bearer ")
                profile = self.oauth.profile_for_token(token)
                if profile is None:
                    await self._json_response(writer, 401, {"error": "invalid_token"})
                    return
                await self._json_response(writer, 200, profile)
                return

            await self._json_response(writer, 404, {"error": "not found"})
        except Exception:
            with suppress(Exception):
                await self._json_response(writer, 400, {"error": "bad request"})
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def _smtp_write(self, writer: asyncio.StreamWriter, line: str) -> None:
        writer.write((line + "\r\n").encode("utf-8"))
        await writer.drain()

    async def _append_message(self, raw_message: bytes, recipients: list[str]) -> None:
        parsed = BytesParser(policy=policy.default).parsebytes(raw_message)
        item = {
            "captured_at": time.time(),
            "recipients": recipients,
            "subject": str(parsed.get("Subject", "")),
            "body": extract_message_text(raw_message),
        }
        self.messages.append(item)
        path = Path(self.config["messages_path"])
        await asyncio.to_thread(append_message_record, path, item)

    async def _handle_smtp(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.smtp_sessions += 1
        authenticated = False
        recipients: list[str] = []
        try:
            await self._smtp_write(writer, "220 fresh-smtp ESMTP ready")
            while True:
                raw = await asyncio.wait_for(reader.readline(), 30)
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                command, _, argument = line.partition(" ")
                upper = command.upper()
                if upper in {"EHLO", "HELO"}:
                    writer.write(b"250-fresh-smtp\r\n250-AUTH PLAIN LOGIN\r\n250 SIZE 1048576\r\n")
                    await writer.drain()
                elif upper == "AUTH":
                    mechanism, _, initial = argument.partition(" ")
                    if mechanism.upper() == "PLAIN":
                        if not initial:
                            await self._smtp_write(writer, "334 ")
                            initial = (await reader.readline()).decode().strip()
                        decoded = base64.b64decode(initial).decode("utf-8", errors="replace").split("\x00")
                        username = decoded[-2] if len(decoded) >= 2 else ""
                        password = decoded[-1] if decoded else ""
                    elif mechanism.upper() == "LOGIN":
                        await self._smtp_write(writer, "334 VXNlcm5hbWU6")
                        username = base64.b64decode((await reader.readline()).strip()).decode("utf-8", errors="replace")
                        await self._smtp_write(writer, "334 UGFzc3dvcmQ6")
                        password = base64.b64decode((await reader.readline()).strip()).decode("utf-8", errors="replace")
                    else:
                        await self._smtp_write(writer, "504 unsupported authentication mechanism")
                        continue
                    authenticated = hmac.compare_digest(username, str(self.config["smtp_username"])) and hmac.compare_digest(password, str(self.config["smtp_password"]))
                    await self._smtp_write(writer, "235 2.7.0 Authentication successful" if authenticated else "535 5.7.8 Authentication failed")
                elif upper == "MAIL":
                    await self._smtp_write(writer, "250 2.1.0 OK" if authenticated else "530 5.7.0 Authentication required")
                elif upper == "RCPT":
                    if not authenticated:
                        await self._smtp_write(writer, "530 5.7.0 Authentication required")
                    else:
                        recipient = argument.split(":", 1)[-1].strip().strip("<>")
                        recipients.append(recipient)
                        await self._smtp_write(writer, "250 2.1.5 OK")
                elif upper == "DATA":
                    if not authenticated or not recipients:
                        await self._smtp_write(writer, "503 5.5.1 Bad sequence")
                        continue
                    await self._smtp_write(writer, "354 End data with <CR><LF>.<CR><LF>")
                    chunks = []
                    while True:
                        chunk = await asyncio.wait_for(reader.readline(), 30)
                        if chunk in {b".\r\n", b".\n", b""}:
                            break
                        if chunk.startswith(b".."):
                            chunk = chunk[1:]
                        chunks.append(chunk)
                    await self._append_message(b"".join(chunks), recipients[:])
                    recipients.clear()
                    await self._smtp_write(writer, "250 2.0.0 queued")
                elif upper == "RSET":
                    recipients.clear()
                    await self._smtp_write(writer, "250 2.0.0 reset")
                elif upper == "NOOP":
                    await self._smtp_write(writer, "250 2.0.0 OK")
                elif upper == "QUIT":
                    await self._smtp_write(writer, "221 2.0.0 bye")
                    break
                else:
                    await self._smtp_write(writer, "502 5.5.2 command not implemented")
        except Exception:
            pass
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()


async def run(config: dict[str, Any]) -> None:
    stub = ProtocolStub(config)
    await stub.start()
    print(
        json.dumps(
            {
                "status": "ready",
                "http": [config["http_host"], config["http_port"]],
                "smtp": [config["smtp_host"], config["smtp_port"]],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopped.set)
    await stopped.wait()
    await stub.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fresh OAuth and SMTPS protocol stub")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
