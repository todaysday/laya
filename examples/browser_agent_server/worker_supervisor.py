"""Supervise one private-IPC inference process per registered Laya profile."""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping


class WorkerProcessError(RuntimeError):
    """A worker exited or violated the private IPC contract."""


class WorkerRemoteError(RuntimeError):
    """A native error that is preserved as structured parent-side state."""

    def __init__(self, error_type: str, message: str):
        super().__init__(f"profile worker reported {error_type}")
        self.error_type = error_type
        self.remote_message = message


class _WorkerProtocolError(Exception):
    pass


@dataclass
class _WorkerRecord:
    profile: str
    state: str = "loading"
    process: subprocess.Popen | None = None
    channel: socket.socket | None = None
    metadata: dict = field(default_factory=dict)
    in_flight: int = 0
    last_used_at: str | None = None
    expires_at: str | None = None
    expires_mono: float | None = None
    last_completed: bool = False
    startup_event: threading.Event = field(default_factory=threading.Event)
    startup_error: BaseException | None = None
    ipc_lock: threading.Lock = field(default_factory=threading.Lock)
    cleanup_started: bool = False


def _read_exact(channel: socket.socket, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = channel.recv(remaining)
        if not chunk:
            raise EOFError("worker IPC closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _send_frame(channel: socket.socket, value: Mapping) -> None:
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    channel.sendall(struct.pack("!Q", len(payload)) + payload)


def _receive_frame(channel: socket.socket) -> dict:
    size = struct.unpack("!Q", _read_exact(channel, 8))[0]
    try:
        value = json.loads(_read_exact(channel, size).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _WorkerProtocolError("worker sent invalid JSON") from error
    if not isinstance(value, dict):
        raise _WorkerProtocolError("worker sent a non-object frame")
    return value


def _worker_environment() -> dict[str, str]:
    """Keep auth material and its mounted-file pointer in the HTTP parent."""
    # AICODE-NOTE: Workers get no bearer value or key-file pointer; private IPC
    # carries only complete native inference frames.
    environment = dict(os.environ)
    environment.pop("LAYA_API_KEY", None)
    environment.pop("LAYA_API_KEY_FILE", None)
    return environment


def _exception_from_frame(frame: Mapping) -> BaseException:
    error = frame.get("error")
    if not isinstance(error, dict):
        return WorkerProcessError("profile worker returned an invalid error frame")
    error_type = error.get("type")
    message = error.get("message")
    if not isinstance(error_type, str) or not isinstance(message, str):
        return WorkerProcessError("profile worker returned an invalid error frame")
    builtin_errors = {
        "builtins.AttributeError": AttributeError,
        "builtins.ImportError": ImportError,
        "builtins.IndexError": IndexError,
        "builtins.KeyError": KeyError,
        "builtins.MemoryError": MemoryError,
        "builtins.NotImplementedError": NotImplementedError,
        "builtins.OverflowError": OverflowError,
        "builtins.RuntimeError": RuntimeError,
        "builtins.TypeError": TypeError,
        "builtins.ValueError": ValueError,
        "builtins.ZeroDivisionError": ZeroDivisionError,
    }
    exception_type = builtin_errors.get(error_type)
    if exception_type is not None:
        return exception_type(message)
    return WorkerRemoteError(error_type, message)


class ProfileWorkerSupervisor:
    """Start workers on selection; serialize each profile's native IPC calls."""

    def __init__(
        self,
        profiles,
        *,
        idle_ttl_seconds: int = 600,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
        worker_command: Callable[[str, int], list[str]] | None = None,
        environment_factory: Callable[[], dict[str, str]] = _worker_environment,
    ):
        self.profiles = tuple(profiles)
        self.idle_ttl_seconds = idle_ttl_seconds
        self.clock = clock
        self.wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._worker_command = worker_command or self._default_command
        self._environment_factory = environment_factory
        self._condition = threading.Condition(threading.RLock())
        self._build_lock = threading.Lock()
        self._records: dict[str, _WorkerRecord] = {}
        self._request_id = 0

    @staticmethod
    def _default_command(profile: str, ipc_fd: int) -> list[str]:
        script = Path(__file__).with_name("profile_worker.py")
        return [sys.executable, str(script), profile, str(ipc_fd)]

    def _require_profile(self, profile: str) -> None:
        if profile not in self.profiles:
            raise ValueError(f"unknown Laya profile: {profile}")

    @staticmethod
    def _record_alive(record: _WorkerRecord) -> bool:
        return record.process is not None and record.process.poll() is None

    def _state_locked(self, record: _WorkerRecord) -> str:
        if record.state == "loading":
            return "loading"
        if record.state in {"failed", "stopping"} or not self._record_alive(record):
            return "absent"
        if record.in_flight:
            return "busy"
        return "idle" if record.last_completed else "ready"

    def snapshot(self) -> dict[str, dict]:
        """Read worker state without spawning a process or touching its deadline."""
        with self._condition:
            result = {}
            for profile in self.profiles:
                record = self._records.get(profile)
                state = "absent" if record is None else self._state_locked(record)
                if state == "absent":
                    result[profile] = {
                        "state": "absent",
                        "pid": None,
                        "in_flight": 0,
                        "last_used_at": None if record is None else record.last_used_at,
                        "expires_at": None if record is None else record.expires_at,
                        "expires_in_seconds": None,
                        "revision": None,
                        "device": None,
                    }
                    continue
                remaining = (
                    None if record.expires_mono is None
                    else max(0.0, record.expires_mono - self.clock())
                )
                result[profile] = {
                    "state": state,
                    "pid": None if record.process is None else record.process.pid,
                    "in_flight": record.in_flight,
                    "last_used_at": record.last_used_at,
                    "expires_at": record.expires_at,
                    "expires_in_seconds": remaining,
                    "revision": record.metadata.get("revision"),
                    "device": record.metadata.get("device"),
                }
            return result

    def start(self, profile: str) -> dict:
        """Ensure only ``profile`` has a live, model-ready worker."""
        self._require_profile(profile)
        while True:
            spawning = None
            stale = None
            waiting = None
            with self._condition:
                record = self._records.get(profile)
                if record is not None and record.state in {"failed", "stopping"}:
                    self._condition.wait_for(
                        lambda: self._records.get(profile) is not record
                    )
                    continue
                if record is not None and record.state == "loading":
                    waiting = record
                elif record is not None and self._record_alive(record):
                    return dict(record.metadata)
                elif record is not None:
                    stale = record
                    self._records.pop(profile, None)
                else:
                    record = _WorkerRecord(profile)
                    self._records[profile] = record
                    spawning = record
            if waiting is not None:
                waiting.startup_event.wait()
                if waiting.startup_error is not None:
                    raise waiting.startup_error
                continue
            if stale is not None:
                self._reap(stale, terminate=False)
                continue
            if spawning is not None:
                # Preserve native Router behavior: expensive profile loads stay serialized,
                # while already-running workers continue handling their own requests.
                with self._build_lock:
                    return self._spawn(profile, spawning)

    def _spawn(self, profile: str, record: _WorkerRecord) -> dict:
        parent_channel = None
        child_channel = None
        process = None
        try:
            parent_channel, child_channel = socket.socketpair()
            command = self._worker_command(profile, child_channel.fileno())
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=None,
                stderr=None,
                close_fds=True,
                pass_fds=(child_channel.fileno(),),
                env=self._environment_factory(),
            )
            child_channel.close()
            child_channel = None
            with self._condition:
                record.process = process
                record.channel = parent_channel
                self._condition.notify_all()

            frame = _receive_frame(parent_channel)
            if frame.get("type") == "error" and frame.get("phase") == "startup":
                raise _exception_from_frame(frame)
            metadata = frame.get("metadata")
            if frame.get("type") != "ready" or not isinstance(metadata, dict):
                raise _WorkerProtocolError("worker did not send a ready frame")
            if not isinstance(metadata.get("device"), str) or not isinstance(
                metadata.get("revision"), str
            ):
                raise _WorkerProtocolError("worker ready metadata is incomplete")
            with self._condition:
                record.metadata = dict(metadata)
                record.state = "ready"
                record.startup_event.set()
                self._condition.notify_all()
            return dict(metadata)
        except BaseException as error:
            if child_channel is not None:
                child_channel.close()
            if process is not None:
                if parent_channel is not None:
                    parent_channel.close()
                self._reap(_WorkerRecord(profile, process=process), terminate=True)
            elif parent_channel is not None:
                parent_channel.close()
            with self._condition:
                record.startup_error = error
                record.state = "failed"
                record.startup_event.set()
                if self._records.get(profile) is record:
                    self._records.pop(profile, None)
                self._condition.notify_all()
            if isinstance(error, (ValueError, TypeError, RuntimeError)):
                raise
            raise WorkerProcessError(f"{profile} worker failed during startup") from None

    def request(self, profile: str, request: dict) -> dict:
        """Send one complete native request; raise on worker/protocol failure."""
        self._require_profile(profile)
        with self._condition:
            record = self._records.get(profile)
            if record is None or self._state_locked(record) not in {"ready", "busy", "idle"}:
                raise WorkerProcessError(f"{profile} worker is unavailable")
            if not self._record_alive(record):
                dead = True
                request_id = None
            else:
                dead = False
                record.in_flight += 1
                self._request_id += 1
                request_id = self._request_id
                self._condition.notify_all()
        if dead:
            self._discard_failed(record)
            raise WorkerProcessError(f"{profile} worker exited")

        failure = None
        remote_error = None
        try:
            with record.ipc_lock:
                with self._condition:
                    if record.state == "failed" or not self._record_alive(record):
                        raise WorkerProcessError(f"{profile} worker exited")
                    channel = record.channel
                if channel is None:
                    raise WorkerProcessError(f"{profile} worker IPC is unavailable")
                _send_frame(channel, {
                    "type": "request",
                    "id": request_id,
                    "request": request,
                })
                frame = _receive_frame(channel)
                if frame.get("id") != request_id:
                    raise _WorkerProtocolError("worker response id does not match request")
                if frame.get("type") == "error":
                    remote_error = _exception_from_frame(frame)
                elif frame.get("type") != "response" or "result" not in frame:
                    raise _WorkerProtocolError("worker response frame is incomplete")
                else:
                    result = frame["result"]
                    if not isinstance(result, dict):
                        raise _WorkerProtocolError("worker response is not an object")
        except (WorkerProcessError, _WorkerProtocolError, EOFError, OSError) as error:
            failure = error
        finally:
            with self._condition:
                record.in_flight -= 1
                if record.in_flight < 0:
                    record.in_flight = 0
                    failure = _WorkerProtocolError("worker in-flight count underflow")
                if failure is None and self._record_alive(record):
                    completed_at = self.wall_clock()
                    record.last_used_at = completed_at.isoformat()
                    record.expires_at = (
                        completed_at + timedelta(seconds=self.idle_ttl_seconds)
                    ).isoformat()
                    record.expires_mono = self.clock() + self.idle_ttl_seconds
                    record.last_completed = True
                self._condition.notify_all()

        if failure is not None:
            self._discard_failed(record)
            raise WorkerProcessError(f"{profile} worker IPC failed") from None
        if remote_error is not None:
            raise remote_error
        return result

    def sweep_expired(self) -> tuple[str, ...]:
        """Stop each independently expired worker, with request registration serialized."""
        stopping = []
        with self._condition:
            now = self.clock()
            for profile in self.profiles:
                record = self._records.get(profile)
                if (
                    record is None
                    or record.state in {"failed", "loading", "stopping"}
                    or record.in_flight
                    or record.expires_mono is None
                    or now < record.expires_mono
                ):
                    continue
                # AICODE-NOTE: Mark idle workers stopping under the condition,
                # then reap outside it so unrelated profiles keep progressing.
                record.state = "stopping"
                stopping.append(record)
        for record in stopping:
            self._stop_record(record)
        return tuple(record.profile for record in stopping)

    def stop(self, profile: str) -> None:
        """Gracefully stop and reap one idle profile process."""
        self._require_profile(profile)
        while True:
            waiting = None
            stopping = None
            with self._condition:
                record = self._records.get(profile)
                if record is None:
                    return
                if record.state in {"failed", "stopping"}:
                    self._condition.wait_for(
                        lambda: self._records.get(profile) is not record
                    )
                    continue
                if record.state == "loading":
                    waiting = record
                elif record.in_flight:
                    self._condition.wait_for(
                        lambda: self._records.get(profile) is not record
                        or record.in_flight == 0
                    )
                else:
                    record.state = "stopping"
                    stopping = record
            if stopping is not None:
                self._stop_record(stopping)
                return
            if waiting is not None:
                waiting.startup_event.wait()

    def _stop_record(self, record: _WorkerRecord) -> None:
        process = record.process
        channel = record.channel
        graceful = process is not None and process.poll() is None and channel is not None
        if graceful:
            try:
                _send_frame(channel, {"type": "shutdown"})
                frame = _receive_frame(channel)
                graceful = frame.get("type") == "stopped"
            except (EOFError, OSError, _WorkerProtocolError):
                graceful = False
        if channel is not None:
            channel.close()
        if process is not None:
            if not graceful and process.poll() is None:
                process.terminate()
            process.wait()
        with self._condition:
            if self._records.get(record.profile) is record:
                self._records.pop(record.profile, None)
            self._condition.notify_all()

    def _reap(self, record: _WorkerRecord, *, terminate: bool) -> None:
        if record.channel is not None:
            record.channel.close()
        process = record.process
        if process is not None:
            if terminate and process.poll() is None:
                process.terminate()
            process.wait()

    def _discard_failed(self, record: _WorkerRecord) -> None:
        with self._condition:
            if self._records.get(record.profile) is not record:
                return
            if record.cleanup_started:
                self._condition.wait_for(
                    lambda: self._records.get(record.profile) is not record
                )
                return
            record.cleanup_started = True
            record.state = "failed"
            self._condition.wait_for(lambda: record.in_flight == 0)
            channel = record.channel
            process = record.process
        if channel is not None:
            channel.close()
        if process is not None:
            if process.poll() is None:
                process.terminate()
            process.wait()
        with self._condition:
            if self._records.get(record.profile) is record:
                self._records.pop(record.profile, None)
            self._condition.notify_all()

    def shutdown(self) -> None:
        """Stop workers after the HTTP inference pool has drained."""
        for profile in self.profiles:
            self.stop(profile)
