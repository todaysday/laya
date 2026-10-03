"""Private IPC entry point for one lazily loaded Laya checkpoint profile."""

from __future__ import annotations

import json
import socket
import struct
import sys


def _read_exact(channel: socket.socket, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = channel.recv(remaining)
        if not chunk:
            raise EOFError("parent closed worker IPC")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _send(channel: socket.socket, value: dict) -> None:
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    channel.sendall(struct.pack("!Q", len(payload)) + payload)


def _receive(channel: socket.socket) -> dict:
    size = struct.unpack("!Q", _read_exact(channel, 8))[0]
    value = json.loads(_read_exact(channel, size).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("IPC frame must be an object")
    return value


def _error(error: BaseException) -> dict:
    return {
        "type": f"{type(error).__module__}.{type(error).__qualname__}",
        "message": str(error),
    }


def _load_profile(profile: str):
    from laya import serve as laya_serve
    from laya.mcp.device import agent_device, env_device

    from browser_adapter import BrowserAgentAdapter
    from unified_server import (
        BASE_REVISION,
        BROWSER_MODEL,
        BROWSER_REVISION,
        register_browser_profile,
        _STOCK_ROUTER,
    )

    if profile not in {"english", "multilingual", BROWSER_MODEL}:
        raise ValueError("worker profile is not registered")
    register_browser_profile()
    laya_serve._apply_thread_limit()
    router = _STOCK_ROUTER(device=env_device(), preload=False)
    if profile == BROWSER_MODEL:
        router.revisions[profile] = BROWSER_REVISION
    native_agent = router.load(profile)
    agent = BrowserAgentAdapter(native_agent) if profile == BROWSER_MODEL else native_agent
    expected_revision = BROWSER_REVISION if profile == BROWSER_MODEL else BASE_REVISION
    return router, agent, {
        "device": agent_device(native_agent) or "cpu",
        "revision": expected_revision,
    }


def run(profile: str, ipc_fd: int) -> int:
    # AICODE-NOTE: This process owns one profile for its lifetime; it receives
    # full native JSON over the private socketpair while HTTP auth stays parent-side.
    channel = socket.socket(fileno=ipc_fd)
    try:
        try:
            router, agent, metadata = _load_profile(profile)
        except Exception as error:
            _send(channel, {"type": "error", "phase": "startup", "error": _error(error)})
            return 1

        _send(channel, {"type": "ready", "metadata": metadata})
        while True:
            try:
                frame = _receive(channel)
            except EOFError:
                return 0
            request_id = frame.get("id")
            if frame.get("type") == "shutdown":
                _send(channel, {"type": "stopped"})
                return 0
            if frame.get("type") != "request" or type(request_id) is not int:
                _send(channel, {
                    "type": "error",
                    "id": request_id,
                    "error": _error(ValueError("invalid worker request frame")),
                })
                continue
            request = frame.get("request")
            if not isinstance(request, dict):
                _send(channel, {
                    "type": "error",
                    "id": request_id,
                    "error": _error(ValueError("worker request must be an object")),
                })
                continue
            try:
                result = agent.system_one(
                    request["state"], request["questions"], **request.get("kwargs", {})
                )
                _send(channel, {"type": "response", "id": request_id, "result": result})
            except Exception as error:
                _send(channel, {
                    "type": "error",
                    "id": request_id,
                    "error": _error(error),
                })
    finally:
        channel.close()


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    return run(sys.argv[1], int(sys.argv[2]))


if __name__ == "__main__":
    raise SystemExit(main())
