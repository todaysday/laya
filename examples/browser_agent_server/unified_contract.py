"""Server-side contracts for the unified Laya router and idle-weight lifecycle."""

import json
import os
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from laya import router as laya_router
from unified_server import (
    BROWSER_MODEL,
    BROWSER_REVISION,
    BROWSER_SUBFOLDER,
    BROWSER_SOURCE,
    WorkerAgentProxy,
    MODEL_TTL_SECONDS,
    LifecycleRouter,
    _resolve_active_model,
    build_runtime_router,
    create_integrated_app,
    register_browser_profile,
)
from readiness import (
    EXPECTED_PROFILES,
    SOURCE_COMMIT,
    health_is_ready,
    preload_is_disabled,
)
from browser_adapter import BrowserAgentAdapter
from worker_contract import FAKE_WORKER


class FakeClock:
    def __init__(self):
        self.value = 1_000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class AgentRecorder:
    def __init__(self, result=None, block=None, fail=False):
        self.cfg = {"laya_fmt": "v3", "max_len": 1024, "head_max_len_train": 768}
        self.calls = []
        self.result = result or {
            "model": "laya-rl-agent",
            "answers": {"click_target": {"choice": "1"}},
            "usage": {"input_tokens": 20, "output_tokens": 0,
                      "state_tokens": 12, "state_tokens_dropped": 0,
                      "truncated": False, "truncated_questions": []},
        }
        self.block = block
        self.fail = fail
        self.device = "cpu"
        self.revision = None

    def predict(self, state, questions, max_len=None, head_max_len=None, lang=None):
        self.calls.append((state, questions, max_len, head_max_len, lang))
        if self.block is not None:
            self.block[0].set()
            self.block[1].wait()
        if self.fail:
            raise RuntimeError("native inference failed")
        return dict(self.result)

    system_one = predict


class WorkerRecorder:
    def __init__(self):
        self.calls = []

    def request(self, profile, payload):
        self.calls.append((profile, payload))
        return {"complete": True}


class TestLifecycleRouter(LifecycleRouter):
    def __init__(self, *args, fake_agents=None, **kwargs):
        self.fake_agents = fake_agents or {}
        super().__init__(*args, start_sweeper=False, **kwargs)

    def _build(self, name):
        agent = self.fake_agents.get(name, AgentRecorder())
        return BrowserAgentAdapter(agent) if name == BROWSER_MODEL else agent


def browser_request(text="long page context " * 400, choice_count=51):
    state = {
        "page": {"url": "https://books.toscrape.com/", "title": "Books", "text": text},
        "recent_actions": [],
    }
    criteria = {
        str(index): f"Category {index} with complete observed label and link role " + ("detail " * 15)
        for index in range(1, choice_count + 1)
    }
    questions = {"click_target": {
        "type": "choice",
        "instructions": "Goal: Open the requested category. Operation: CLICK. Choose one offered key.",
        "criteria": criteria,
    }}
    return json.dumps(state, ensure_ascii=False), questions, state, criteria


class UnifiedRouterContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        register_browser_profile()

    def make_router(self, clock=None, wall_clock=None, fake_agents=None, max_loaded=1):
        return TestLifecycleRouter(
            device="cpu",
            max_loaded=max_loaded,
            clock=clock or FakeClock(),
            wall_clock=wall_clock or (lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)),
            fake_agents=fake_agents,
        )

    def test_browser_profile_is_registered_with_exact_local_checkpoint_and_pin(self):
        self.assertEqual(laya_router.normalise_name(BROWSER_MODEL), BROWSER_MODEL)
        self.assertEqual(tuple(laya_router.DEFAULT_MODELS),
                         ("english", "multilingual", BROWSER_MODEL))
        self.assertEqual(laya_router.DEFAULT_MODELS[BROWSER_MODEL],
                         (BROWSER_SOURCE, BROWSER_SUBFOLDER))
        self.assertEqual(BROWSER_REVISION, "adf912be85ff9221ee171551778456b133c1af75")
        self.assertEqual(BROWSER_SUBFOLDER, "v10s")

    def test_lazy_loading_all_registered_profiles_has_no_lru_count_eviction(self):
        router = self.make_router(max_loaded=1)
        self.assertEqual(router.loaded, [])
        expected = tuple(router.models)
        self.assertEqual(expected, ("english", "multilingual", BROWSER_MODEL))
        for profile in expected:
            router.load(profile)
        self.assertEqual(tuple(router.loaded), expected)
        self.assertEqual(router.lifecycle_snapshot()["ttl_seconds"], MODEL_TTL_SECONDS)

    def test_removed_typed_profile_is_rejected_without_loading_another_model(self):
        router = self.make_router()
        state_json, questions, _state, _criteria = browser_request(choice_count=2)
        with self.assertRaises(ValueError):
            router.predict(state_json, questions, model="typed-decisions")
        self.assertEqual(router.loaded, [])
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as error:
            _resolve_active_model("typed-decisions")
        self.assertEqual(error.exception.status_code, 422)

    def test_runtime_builder_keeps_startup_lazy_and_only_registers_three_profiles(self):
        with patch.dict(os.environ, {
            "LAYA_PRELOAD": "0",
            "LAYA_MODELS": "english,multilingual",
            "LAYA_DEVICE": "cpu",
            "LAYA_DEFAULT_MODEL": "multilingual",
            "LAYA_THREADS": "4",
            "OMP_NUM_THREADS": "4",
        }, clear=False):
            router = build_runtime_router()
        self.assertEqual(router.loaded, [])
        self.assertEqual(tuple(router.models), ("english", "multilingual", BROWSER_MODEL))

    def test_browser_v3_branch_preserves_complete_state_instructions_and_all_choices(self):
        state_json, questions, state, criteria = browser_request()
        agent = AgentRecorder()
        router = self.make_router(fake_agents={BROWSER_MODEL: agent})

        result = router.predict(state_json, questions, model=BROWSER_MODEL)

        self.assertEqual(len(agent.calls), 1)
        passed_state, passed_questions, max_len, head_max_len, _lang = agent.calls[0]
        self.assertEqual(passed_state, state)
        self.assertEqual(passed_questions["click_target"]["instructions"],
                         questions["click_target"]["instructions"])
        self.assertEqual(passed_questions["click_target"]["criteria"], criteria)
        self.assertEqual(len(passed_questions["click_target"]["criteria"]), 51)
        self.assertIsNone(max_len)
        self.assertIsNone(head_max_len)
        self.assertEqual(result["routing"]["model"], BROWSER_MODEL)
        self.assertEqual(result["requested_context"],
                         {"max_len": None, "head_max_len": None})
        self.assertEqual(result["effective_context"],
                         {"max_len": 1024, "head_max_len": 768})
        self.assertFalse(result["usage"]["truncated"])

    def test_explicit_native_budgets_reach_browser_agent_without_adapter_ceiling(self):
        state_json, questions, _state, _criteria = browser_request()
        agent = AgentRecorder()
        router = self.make_router(fake_agents={BROWSER_MODEL: agent})

        router.predict(state_json, questions, model=BROWSER_MODEL,
                       max_len=8192, head_max_len=8192)

        self.assertEqual(agent.calls[0][2:4], (8192, 8192))

    def test_worker_proxy_transports_full_native_system_one_arguments(self):
        worker = WorkerRecorder()
        state, questions, _page, _criteria = browser_request()
        proxy = WorkerAgentProxy(
            worker,
            BROWSER_MODEL,
            {"device": "cpu", "revision": BROWSER_REVISION},
        )

        self.assertEqual(proxy.system_one(
            state, questions, lang="fr", max_len=4096, head_max_len=3072
        ), {"complete": True})
        self.assertEqual(worker.calls, [(
            BROWSER_MODEL,
            {
                "state": state,
                "questions": questions,
                "kwargs": {"lang": "fr", "max_len": 4096, "head_max_len": 3072},
            },
        )])
        self.assertEqual(proxy.device, "cpu")
        self.assertEqual(proxy.revision, BROWSER_REVISION)

    def test_router_runs_lazy_profile_in_child_and_releases_it_at_its_ttl(self):
        clock = FakeClock()
        with TemporaryDirectory() as directory:
            script = Path(directory) / "fake_worker.py"
            script.write_text(FAKE_WORKER)
            router = LifecycleRouter(
                device="cpu",
                clock=clock,
                worker_command=lambda _profile, fd: [sys.executable, str(script), str(fd)],
            )
            try:
                state, questions, _page, _criteria = browser_request(choice_count=2)
                result = router.predict(state, questions, model="english", max_len=2048)

                profile = router.lifecycle_snapshot()["profiles"]["english"]
                self.assertEqual(result["state"], state)
                self.assertEqual(result["questions"], questions)
                self.assertEqual(result["kwargs"], {"lang": None, "max_len": 2048})
                self.assertTrue(profile["loaded"])
                self.assertEqual(profile["worker_state"], "idle")
                self.assertNotEqual(profile["worker_pid"], os.getpid())
                self.assertTrue(all(
                    router.lifecycle_snapshot()["profiles"][name]["worker_state"] == "absent"
                    for name in ("multilingual", BROWSER_MODEL)
                ))

                clock.advance(MODEL_TTL_SECONDS)
                router.sweep_expired()
                profile = router.lifecycle_snapshot()["profiles"]["english"]
                self.assertFalse(profile["loaded"])
                self.assertEqual(profile["worker_state"], "absent")
            finally:
                router.unload()

    def test_english_and_multilingual_still_use_native_system_one(self):
        state_json, questions, _state, _criteria = browser_request(choice_count=2)
        for profile in ("english", "multilingual"):
            with self.subTest(profile=profile):
                agent = AgentRecorder()
                router = self.make_router(fake_agents={profile: agent})
                result = router.predict(state_json, questions, model=profile, max_len=2048)
                self.assertEqual(agent.calls[0][0], state_json)
                self.assertEqual(agent.calls[0][2], 2048)
                self.assertEqual(result["routing"]["model"], profile)
                self.assertNotIn("effective_context", result)

    def test_each_request_completion_starts_its_profile_ttl_and_health_is_read_only(self):
        clock = FakeClock()
        router = self.make_router(clock=clock)
        state_json, questions, _state, _criteria = browser_request(choice_count=2)
        router.predict(state_json, questions, model="english")
        before = router.lifecycle_snapshot()["profiles"]["english"]
        self.assertEqual(before["expires_in_seconds"], MODEL_TTL_SECONDS)

        clock.advance(240)
        health = router.lifecycle_snapshot()
        after = health["profiles"]["english"]
        self.assertEqual(after["expires_at"], before["expires_at"])
        self.assertEqual(after["expires_in_seconds"], 360)
        self.assertEqual(router.loaded, ["english"])

        router.sweep_expired()
        self.assertEqual(router.loaded, ["english"])
        clock.advance(360)
        router.sweep_expired()
        self.assertEqual(router.loaded, [])

    def test_inflight_inference_cannot_be_unloaded_and_completion_resets_ttl(self):
        clock = FakeClock()
        entered, release = threading.Event(), threading.Event()
        agent = AgentRecorder(block=(entered, release))
        router = self.make_router(clock=clock, fake_agents={"english": agent})
        state_json, questions, _state, _criteria = browser_request(choice_count=2)
        errors = []

        def infer():
            try:
                router.predict(state_json, questions, model="english")
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=infer)
        worker.start()
        self.assertTrue(entered.wait(2))
        clock.advance(MODEL_TTL_SECONDS + 1)
        self.assertEqual(router.lifecycle_snapshot()["profiles"]["english"]["in_flight"], 1)
        router.sweep_expired()
        self.assertEqual(router.loaded, ["english"])
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(router.lifecycle_snapshot()["profiles"]["english"]["expires_in_seconds"],
                         MODEL_TTL_SECONDS)
        router.sweep_expired()
        self.assertEqual(router.loaded, ["english"])
        clock.advance(MODEL_TTL_SECONDS)
        router.sweep_expired()
        self.assertEqual(router.loaded, [])

    def test_native_exception_releases_inflight_and_health_does_not_load_profiles(self):
        clock = FakeClock()
        agent = AgentRecorder(fail=True)
        router = self.make_router(clock=clock, fake_agents={"multilingual": agent})
        state_json, questions, _state, _criteria = browser_request(choice_count=2)
        with self.assertRaisesRegex(RuntimeError, "native inference failed"):
            router.predict(state_json, questions, model="multilingual")

        profile = router.lifecycle_snapshot()["profiles"]["multilingual"]
        self.assertEqual(profile["in_flight"], 0)
        self.assertEqual(profile["expires_in_seconds"], MODEL_TTL_SECONDS)
        snapshot = router.lifecycle_snapshot()
        self.assertTrue(all(item["loaded"] for name, item in snapshot["profiles"].items()
                            if name == "multilingual"))
        self.assertTrue(all(not item["loaded"] for name, item in snapshot["profiles"].items()
                            if name != "multilingual"))

    def test_health_auth_and_existing_systemone_route_are_preserved(self):
        router = self.make_router()
        with patch.dict(os.environ, {"LAYA_API_KEY": "contract-test-key"}, clear=False):
            app = create_integrated_app(router)

        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
            health = client.get("/health", headers={
                "Authorization": "Bearer contract-test-key"
            }).json()
            self.assertEqual(health["status"], "ok")
            self.assertIn("profiles", health)
            self.assertEqual(health["loaded"], [])
            self.assertEqual(health["revisions"], {})
            self.assertEqual(health["profiles"][BROWSER_MODEL]["revision"], BROWSER_REVISION)
            self.assertEqual(health["profiles"][BROWSER_MODEL]["subfolder"], "v10s")
            self.assertTrue(all(
                profile["worker_state"] == "absent" and profile["worker_pid"] is None
                for profile in health["profiles"].values()
            ))
            self.assertEqual(router.loaded, [])
            self.assertEqual(client.post("/v1/systemone", json={}).status_code, 401)
            self.assertTrue(any(getattr(route, "path", None) == "/v1/systemone"
                                for route in app.routes))
            self.assertFalse(any(getattr(route, "path", None) == "/v1/browser"
                                 for route in app.routes))

    def test_readiness_accepts_a_loaded_profile_with_inflight_request(self):
        profiles = {
            name: {
                "revision": revision,
                "loaded": False,
                "in_flight": 0,
                "worker_state": "absent",
                "worker_pid": None,
            }
            for name, revision in EXPECTED_PROFILES.items()
        }
        profiles[BROWSER_MODEL].update({
            "repository": "cklxx/laya-browser",
            "subfolder": "v10s",
            "format": "v3",
        })
        profiles["english"].update({
            "loaded": True,
            "in_flight": 1,
            "worker_state": "busy",
            "worker_pid": 12345,
        })
        health = {
            "status": "ok",
            "laya_source_commit": SOURCE_COMMIT,
            "idle_ttl_seconds": MODEL_TTL_SECONDS,
            "device": "cpu",
            "profiles": profiles,
        }

        self.assertTrue(health_is_ready(health, True))
        self.assertFalse(health_is_ready(health, False))

    def test_preload_setting_is_read_from_host_runtime_file_exactly(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.env"
            path.write_text("LAYA_PRELOAD=0\n")
            self.assertTrue(preload_is_disabled(path))
            path.write_text("LAYA_PRELOAD=0\nLAYA_PRELOAD=1\n")
            self.assertFalse(preload_is_disabled(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
