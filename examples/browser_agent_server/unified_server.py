"""Integrate the pinned format-v3 checkpoint into the existing Laya service."""

from __future__ import annotations

import functools
import inspect
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from laya import router as laya_router
from laya import serve

from worker_supervisor import ProfileWorkerSupervisor, WorkerProcessError


BROWSER_MODEL = "laya-browser-v10s"
BROWSER_REPO = "cklxx/laya-browser"
BROWSER_REVISION = "adf912be85ff9221ee171551778456b133c1af75"
BROWSER_SUBFOLDER = "v10s"
BROWSER_SOURCE = "/opt/laya-browser-checkpoint"
LAYA_SOURCE_COMMIT = "fa9a2a7070b1789912a49ae24603bbfb1a78b001"
BASE_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
MODEL_TTL_SECONDS = 600
ACTIVE_MODELS = ("english", "multilingual", BROWSER_MODEL)
UNSUPPORTED_MODEL_ALIASES = frozenset({
    "typed", "typed-decisions", "typed_decisions", "laya-typed-decisions", "decisions",
})

_LOGGER = logging.getLogger("laya.browser_profile")
_STOCK_ROUTER = laya_router.Router
_STOCK_BUILD_ROUTER = serve.build_router
_STOCK_CREATE_APP = serve.create_app
_STOCK_RESOLVE_MODEL = serve._resolve_model


def register_browser_profile() -> None:
    """Register exactly the two active base profiles and the pinned browser profile."""
    laya_router.DEFAULT_MODELS.clear()
    laya_router.DEFAULT_MODELS.update({
        "english": ("convaiinnovations/laya", None),
        "multilingual": ("convaiinnovations/laya", "multilingual"),
        BROWSER_MODEL: (BROWSER_SOURCE, BROWSER_SUBFOLDER),
    })
    laya_router._ALIASES.clear()
    laya_router._ALIASES.update({
        "en": "english",
        "laya": "english",
        "default": "english",
        "multi": "multilingual",
        "ml": "multilingual",
        "laya-multilingual": "multilingual",
        BROWSER_MODEL: BROWSER_MODEL,
    })


class WorkerAgentProxy:
    """Expose a worker as the native agent interface without parent model state."""

    def __init__(self, workers, profile, metadata):
        self._workers = workers
        self.profile = profile
        self.device = metadata["device"]
        self.revision = metadata["revision"]

    def system_one(self, state, questions, **kwargs):
        # AICODE-NOTE: The bearer key stays in this HTTP process. Only the full
        # native request crosses the private socketpair to its profile worker.
        return self._workers.request(self.profile, {
            "state": state,
            "questions": questions,
            "kwargs": kwargs,
        })


class CheckpointLifecycle:
    """Track per-profile inference completion and unload only idle checkpoints."""

    def __init__(self, router, clock=time.monotonic, wall_clock=None,
                 ttl_seconds=MODEL_TTL_SECONDS):
        self.router = router
        self.clock = clock
        self.wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self.ttl_seconds = ttl_seconds
        self.condition = threading.Condition(threading.RLock())
        self._stopping = False
        self._thread = None
        self._profiles = {
            name: {
                "in_flight": 0,
                "last_used_at": None,
                "expires_at": None,
                "expires_mono": None,
                "last_load_seconds": None,
            }
            for name in ACTIVE_MODELS
        }

    def begin(self, name):
        with self.condition:
            self._profiles[name]["in_flight"] += 1
            self.condition.notify_all()

    def completed(self, name):
        with self.condition:
            profile = self._profiles[name]
            profile["in_flight"] -= 1
            now = self.clock()
            completed_at = self.wall_clock()
            profile["last_used_at"] = completed_at.isoformat()
            profile["expires_at"] = (
                completed_at + timedelta(seconds=self.ttl_seconds)
            ).isoformat()
            profile["expires_mono"] = now + self.ttl_seconds
            self.condition.notify_all()

    def loaded(self, name, duration):
        with self.condition:
            self._profiles[name]["last_load_seconds"] = duration
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            now = self.clock()
            loaded = set(self.router.loaded)
            worker_states = self.router._workers.snapshot()
            result = {}
            for name in ACTIVE_MODELS:
                internal = self._profiles[name]
                expires = internal["expires_mono"]
                remaining = None if expires is None else max(0.0, expires - now)
                worker = worker_states[name]
                agent = self.router._agents.get(name)
                worker_backed = isinstance(agent, WorkerAgentProxy)
                worker_managed = worker_backed or worker["state"] != "absent"
                revision = BROWSER_REVISION if name == BROWSER_MODEL else BASE_REVISION
                if worker_backed and worker["revision"]:
                    revision = worker["revision"]
                result[name] = {
                    "loaded": (
                        worker["state"] in {"ready", "busy", "idle"}
                        if worker_managed else name in loaded
                    ),
                    "in_flight": internal["in_flight"],
                    "last_used_at": internal["last_used_at"],
                    "expires_at": internal["expires_at"],
                    "expires_in_seconds": remaining,
                    "last_load_seconds": internal["last_load_seconds"],
                    "revision": revision,
                    "worker_state": worker["state"] if worker_managed else "absent",
                    "worker_pid": worker["pid"] if worker_managed else None,
                }
                if name == BROWSER_MODEL:
                    result[name].update({
                        "repository": BROWSER_REPO,
                        "subfolder": BROWSER_SUBFOLDER,
                        "format": "v3",
                        "effective_context": {"max_len": 1024, "head_max_len": 768},
                    })
            return result

    def sweep_expired(self):
        """Unload every expired, idle profile while excluding new in-flight requests."""
        unloaded = []
        with self.condition:
            now = self.clock()
            loaded = set(self.router.loaded)
            for name, profile in self._profiles.items():
                expires = profile["expires_mono"]
                if profile["in_flight"] or expires is None or now < expires:
                    continue
                if name in loaded:
                    self.router.unload(name)
                    unloaded.append(name)
                profile["expires_mono"] = None
                profile["expires_at"] = None
            self.condition.notify_all()
        return unloaded

    def _next_wait(self):
        deadlines = [
            profile["expires_mono"]
            for profile in self._profiles.values()
            if profile["in_flight"] == 0 and profile["expires_mono"] is not None
        ]
        return None if not deadlines else max(0.0, min(deadlines) - self.clock())

    def _run(self):
        while True:
            with self.condition:
                if self._stopping:
                    return
                delay = self._next_wait()
                self.condition.wait(timeout=delay)
                if self._stopping:
                    return
            try:
                self.sweep_expired()
            except Exception:
                _LOGGER.exception("idle checkpoint unload failed")

    def start(self):
        with self.condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping = False
            self._thread = threading.Thread(
                target=self._run, name="laya-checkpoint-ttl", daemon=True
            )
            self._thread.start()

    def stop(self):
        with self.condition:
            self._stopping = True
            thread = self._thread
            self.condition.notify_all()
        if thread is not None:
            thread.join()
        with self.condition:
            self._thread = None


class LifecycleRouter(_STOCK_ROUTER):
    """Existing Router with browser-v3 dispatch and per-profile idle unloading."""

    def __init__(self, *args, clock=time.monotonic, wall_clock=None,
                 ttl_seconds=MODEL_TTL_SECONDS, start_sweeper=False,
                 worker_command=None, **kwargs):
        kwargs["preload"] = False
        super().__init__(*args, **kwargs)
        self.revisions[BROWSER_MODEL] = BROWSER_REVISION
        self._workers = ProfileWorkerSupervisor(
            ACTIVE_MODELS,
            idle_ttl_seconds=ttl_seconds,
            clock=clock,
            wall_clock=wall_clock,
            worker_command=worker_command,
        )
        self._lifecycle = CheckpointLifecycle(
            self, clock=clock, wall_clock=wall_clock, ttl_seconds=ttl_seconds
        )
        self._request_profile = threading.local()
        if start_sweeper:
            self.start_sweeper()

    def _evict_locked(self):
        # AICODE-NOTE: The caller-selected 600-second profile TTL is the only
        # unload policy; Router's internal max_loaded LRU must not discard active weights.
        return []

    def _build(self, name):
        metadata = self._workers.start(name)
        return WorkerAgentProxy(self._workers, name, metadata)

    def unload(self, name=None):
        if name is None:
            self._workers.shutdown()
        else:
            key = laya_router.normalise_name(name)
            if key in ACTIVE_MODELS:
                self._workers.stop(key)
        return super().unload(name)

    def load(self, name):
        key = laya_router.normalise_name(name)
        was_loaded = key in self.loaded
        started = self._lifecycle.clock()
        agent = super().load(key)
        if not was_loaded:
            self._lifecycle.loaded(key, self._lifecycle.clock() - started)
        return agent

    def route(self, *args, **kwargs):
        decision = super().route(*args, **kwargs)
        if getattr(self._request_profile, "active", False):
            name = decision["model"]
            self._lifecycle.begin(name)
            self._request_profile.name = name
        return decision

    def predict(self, *args, **kwargs):
        self._request_profile.active = True
        self._request_profile.name = None
        try:
            try:
                return super().predict(*args, **kwargs)
            except WorkerProcessError:
                name = self._request_profile.name
                if name is not None:
                    self.unload(name)
                raise
        finally:
            name = self._request_profile.name
            self._request_profile.active = False
            self._request_profile.name = None
            if name is not None:
                self._lifecycle.completed(name)

    def lifecycle_snapshot(self):
        return {
            "ttl_seconds": self._lifecycle.ttl_seconds,
            "profiles": self._lifecycle.snapshot(),
        }

    def sweep_expired(self):
        return self._lifecycle.sweep_expired()

    def start_sweeper(self):
        self._lifecycle.start()

    def stop_sweeper(self):
        self._lifecycle.stop()
        self._workers.shutdown()


def build_runtime_router():
    register_browser_profile()
    if os.environ.get("LAYA_PRELOAD") != "0":
        raise RuntimeError("unified Laya requires LAYA_PRELOAD=0")
    laya_router.Router = LifecycleRouter
    try:
        router = _STOCK_BUILD_ROUTER()
    finally:
        laya_router.Router = _STOCK_ROUTER
    if not isinstance(router, LifecycleRouter):
        raise RuntimeError("unified Laya router construction failed")
    router.revisions[BROWSER_MODEL] = BROWSER_REVISION
    return router


def _enrich_health(app, router):
    route = next((item for item in app.routes
                  if getattr(item, "path", None) == "/health" and "GET" in item.methods), None)
    if route is None:
        raise RuntimeError("pinned Laya health route is missing")
    original = route.endpoint

    @functools.wraps(original)
    def health_with_profile_metadata(*args, **kwargs):
        result = original(*args, **kwargs)
        if isinstance(result, dict) and "loaded" in result:
            snapshot = router.lifecycle_snapshot()
            active = {
                name: profile
                for name, profile in snapshot["profiles"].items()
                if profile["loaded"]
            }
            result.update({
                "loaded": list(active),
                "revisions": {
                    name: profile["revision"] for name, profile in active.items()
                },
                "laya_source_commit": LAYA_SOURCE_COMMIT,
                "profiles": snapshot["profiles"],
                "idle_ttl_seconds": snapshot["ttl_seconds"],
            })
        return result

    health_with_profile_metadata.__signature__ = inspect.signature(original)
    route.endpoint = health_with_profile_metadata
    route.dependant.call = health_with_profile_metadata


def create_integrated_app(router=None):
    if router is None:
        router = build_runtime_router()
    app = _STOCK_CREATE_APP(router=router)
    _enrich_health(app, router)
    stock_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifecycle_lifespan(application):
        router.start_sweeper()
        try:
            async with stock_lifespan(application):
                yield
        finally:
            router.stop_sweeper()

    app.router.lifespan_context = lifecycle_lifespan
    return app


def _resolve_active_model(model):
    if model is not None and str(model).strip().lower() in UNSUPPORTED_MODEL_ALIASES:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="model selector is not installed")
    return _STOCK_RESOLVE_MODEL(model)


def install_runtime():
    register_browser_profile()
    serve.build_router = build_runtime_router
    serve._resolve_model = _resolve_active_model
    serve.create_app = create_integrated_app


def main():
    install_runtime()
    serve.main()


if __name__ == "__main__":
    main()
