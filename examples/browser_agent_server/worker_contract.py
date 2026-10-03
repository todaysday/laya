"""Process-boundary contracts for lazy per-profile Laya workers.

Run with the pinned CPU image; all worker commands here are deterministic fakes
and never load a model or access the network.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from worker_supervisor import ProfileWorkerSupervisor, WorkerProcessError


FAKE_WORKER = r'''import json, os, socket, struct, sys, time
from pathlib import Path

channel = socket.socket(fileno=int(sys.argv[1]))

def read_exact(size):
    chunks = []
    remaining = size
    while remaining:
        chunk = channel.recv(remaining)
        if not chunk:
            raise EOFError("parent closed IPC")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)

def receive():
    size = struct.unpack("!Q", read_exact(8))[0]
    return json.loads(read_exact(size).decode("utf-8"))

def send(value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    channel.sendall(struct.pack("!Q", len(payload)) + payload)

send({"type": "ready", "metadata": {"device": "cpu", "revision": "fake-pin"}})
while True:
    frame = receive()
    if frame.get("type") == "shutdown":
        send({"type": "stopped"})
        break
    request = frame["request"]
    request_id = frame["id"]
    if request.get("block_until"):
        Path(request["started_at"]).write_text("started")
        while not Path(request["block_until"]).exists():
            time.sleep(0.005)
    if request.get("crash"):
        os._exit(44)
    if request.get("fail"):
        send({"type": "error", "id": request_id,
              "error": {"type": "builtins.ValueError", "message": "bad request"}})
        continue
    if request.get("read_environment"):
        result = {name: os.environ.get(name) for name in request["read_environment"]}
    else:
        result = request
    send({"type": "response", "id": request_id, "result": result})
channel.close()
'''


class FakeClock:
    def __init__(self):
        self.value = 1_000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class ProfileWorkerContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.script = self.directory / "fake_worker.py"
        self.script.write_text(FAKE_WORKER)
        self.clock = FakeClock()
        self.wall_clock = lambda: datetime(2026, 10, 3, tzinfo=timezone.utc) + timedelta(
            seconds=self.clock()
        )
        self.workers = self.make_workers()

    def tearDown(self):
        self.workers.shutdown()
        self.temp.cleanup()

    def make_workers(self):
        return ProfileWorkerSupervisor(
            ("english", "multilingual", "laya-browser-v10s"),
            idle_ttl_seconds=600,
            clock=self.clock,
            wall_clock=self.wall_clock,
            worker_command=lambda _profile, fd: [sys.executable, str(self.script), str(fd)],
        )

    def test_start_is_lazy_and_starts_only_selected_profile(self):
        before = self.workers.snapshot()
        self.assertTrue(all(item["state"] == "absent" for item in before.values()))

        metadata = self.workers.start("english")

        after = self.workers.snapshot()
        self.assertEqual(metadata, {"device": "cpu", "revision": "fake-pin"})
        self.assertEqual(after["english"]["state"], "ready")
        self.assertTrue(all(after[name]["state"] == "absent"
                            for name in ("multilingual", "laya-browser-v10s")))

    def test_complete_json_request_and_response_cross_private_ipc(self):
        self.workers.start("laya-browser-v10s")
        request = {
            "state": json.dumps({"page": {"text": "complete " * 2000}, "recent_actions": []}),
            "questions": {"click_target": {"criteria": {str(i): f"choice {i}" for i in range(51)}}},
            "max_len": 2048,
            "head_max_len": 1024,
        }

        self.assertEqual(self.workers.request("laya-browser-v10s", request), request)

    def test_structured_native_validation_error_keeps_422_mapping(self):
        self.workers.start("english")

        with self.assertRaisesRegex(ValueError, "bad request"):
            self.workers.request("english", {"fail": True})

        profile = self.workers.snapshot()["english"]
        self.assertEqual(profile["in_flight"], 0)
        self.assertEqual(profile["state"], "idle")

    def test_parent_credentials_and_key_file_path_are_not_in_worker_environment(self):
        with patch.dict(os.environ, {
            "LAYA_API_KEY": "parent-only-test-value",
            "LAYA_API_KEY_FILE": "/run/secrets/parent-only-test-file",
        }, clear=False):
            self.workers.start("english")
            observed = self.workers.request("english", {
                "read_environment": ["LAYA_API_KEY", "LAYA_API_KEY_FILE"]
            })

        self.assertEqual(observed, {"LAYA_API_KEY": None, "LAYA_API_KEY_FILE": None})

    def test_expiry_reaps_only_profile_whose_own_idle_ttl_elapsed(self):
        self.workers.start("english")
        english_pid = self.workers.snapshot()["english"]["pid"]
        self.workers.request("english", {"profile": "english"})
        self.clock.advance(500)
        self.workers.start("multilingual")
        multilingual_pid = self.workers.snapshot()["multilingual"]["pid"]
        self.workers.request("multilingual", {"profile": "multilingual"})
        self.clock.advance(101)

        self.assertEqual(self.workers.sweep_expired(), ("english",))
        after = self.workers.snapshot()
        self.assertEqual(after["english"]["state"], "absent")
        self.assertEqual(after["multilingual"]["state"], "idle")
        self.assertEqual(after["multilingual"]["pid"], multilingual_pid)
        self.assertNotEqual(english_pid, multilingual_pid)

    def test_inflight_request_survives_expiry_and_resets_deadline_at_completion(self):
        self.workers.start("english")
        self.workers.request("english", {"first": True})
        started = self.directory / "started"
        release = self.directory / "release"
        errors = []

        def infer():
            try:
                self.workers.request("english", {
                    "block_until": str(release),
                    "started_at": str(started),
                    "result": "finished",
                })
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=infer)
        thread.start()
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(started.exists(), "fake worker did not receive request")
        self.clock.advance(601)

        self.assertEqual(self.workers.sweep_expired(), ())
        self.assertEqual(self.workers.snapshot()["english"]["state"], "busy")
        release.write_text("continue")
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.workers.request("english", {"health-check": True}),
                         {"health-check": True})
        profile = self.workers.snapshot()["english"]
        self.assertEqual(profile["in_flight"], 0)
        self.assertEqual(profile["expires_in_seconds"], 600)

    def test_slow_profile_does_not_block_an_independent_profile(self):
        self.workers.start("english")
        self.workers.start("multilingual")
        started = self.directory / "english-started"
        release = self.directory / "english-release"
        errors = []

        def infer_english():
            try:
                self.workers.request("english", {
                    "block_until": str(release),
                    "started_at": str(started),
                    "profile": "english",
                })
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=infer_english)
        thread.start()
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(started.exists(), "English worker did not receive request")

        self.assertEqual(
            self.workers.request("multilingual", {"profile": "multilingual"}),
            {"profile": "multilingual"},
        )
        self.assertEqual(self.workers.snapshot()["english"]["state"], "busy")
        self.assertEqual(self.workers.snapshot()["multilingual"]["state"], "idle")

        release.write_text("continue")
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_expiry_winning_race_reaps_worker_before_next_start(self):
        self.workers.start("english")
        original_pid = self.workers.snapshot()["english"]["pid"]
        self.workers.request("english", {"first": True})
        self.clock.advance(600)

        self.assertEqual(self.workers.sweep_expired(), ("english",))
        self.assertEqual(self.workers.snapshot()["english"]["state"], "absent")
        self.workers.start("english")

        self.assertNotEqual(self.workers.snapshot()["english"]["pid"], original_pid)
        self.assertEqual(self.workers.request("english", {"reloaded": True}),
                         {"reloaded": True})

    def test_crashed_worker_is_reaped_and_next_request_can_start_clean_process(self):
        self.workers.start("english")
        failed_pid = self.workers.snapshot()["english"]["pid"]

        with self.assertRaises(WorkerProcessError) as error:
            self.workers.request("english", {
                "crash": True,
                "secret_probe": "private-value-must-not-appear",
            })
        self.assertNotIn("private-value-must-not-appear", str(error.exception))

        self.assertEqual(self.workers.snapshot()["english"]["state"], "absent")
        self.workers.start("english")
        self.assertNotEqual(self.workers.snapshot()["english"]["pid"], failed_pid)
        self.assertEqual(self.workers.request("english", {"recovered": True}),
                         {"recovered": True})

    def test_health_snapshot_neither_spawns_workers_nor_refreshes_idle_deadline(self):
        self.workers.start("english")
        self.workers.request("english", {"one": 1})
        self.clock.advance(75)
        before = self.workers.snapshot()["english"]

        for _ in range(4):
            self.workers.snapshot()

        after = self.workers.snapshot()
        self.assertEqual(after["english"]["pid"], before["pid"])
        self.assertEqual(after["english"]["expires_at"], before["expires_at"])
        self.assertTrue(all(self.workers.snapshot()[name]["state"] == "absent"
                            for name in ("multilingual", "laya-browser-v10s")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
