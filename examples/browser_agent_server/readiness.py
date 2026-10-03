"""Check pinned profile registration without requiring weights to stay unloaded."""

from pathlib import Path

EXPECTED_PROFILES = {
    "english": "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851",
    "multilingual": "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851",
    "laya-browser-v10s": "adf912be85ff9221ee171551778456b133c1af75",
}
WORKER_STATES = {"absent", "loading", "ready", "busy", "idle"}
SOURCE_COMMIT = "fa9a2a7070b1789912a49ae24603bbfb1a78b001"


def preload_is_disabled(path):
    """Read the host runtime file; ExecStartPost lacks the container env."""
    try:
        settings = [
            line.strip()
            for line in Path(path).read_text().splitlines()
            if line.partition("=")[0].strip() == "LAYA_PRELOAD"
        ]
    except OSError:
        return False
    return settings == ["LAYA_PRELOAD=0"]


def health_is_ready(health, preload_disabled):
    """Accept healthy profiles regardless of legitimate loads or active requests."""
    if not isinstance(health, dict) or preload_disabled is not True:
        return False
    profiles = health.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != set(EXPECTED_PROFILES):
        return False
    if (
        health.get("status") != "ok"
        or health.get("laya_source_commit") != SOURCE_COMMIT
        or health.get("idle_ttl_seconds") != 600
        or health.get("device") != "cpu"
    ):
        return False
    for name, revision in EXPECTED_PROFILES.items():
        profile = profiles[name]
        if not isinstance(profile, dict):
            return False
        in_flight = profile.get("in_flight")
        if (
            profile.get("revision") != revision
            or type(profile.get("loaded")) is not bool
            or not isinstance(in_flight, int)
            or isinstance(in_flight, bool)
            or in_flight < 0
        ):
            return False
        worker_state = profile.get("worker_state")
        worker_pid = profile.get("worker_pid")
        if worker_state not in WORKER_STATES:
            return False
        if worker_state in {"ready", "busy", "idle"}:
            if profile["loaded"] is not True or type(worker_pid) is not int or worker_pid <= 0:
                return False
        elif profile["loaded"] is not False or (
            worker_pid is not None
            and (type(worker_pid) is not int or worker_pid <= 0)
        ):
            return False
    browser = profiles["laya-browser-v10s"]
    return (
        browser.get("repository") == "cklxx/laya-browser"
        and browser.get("subfolder") == "v10s"
        and browser.get("format") == "v3"
    )
