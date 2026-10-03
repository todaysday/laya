"""Lossless request preparation for the pinned Laya v10s format-v3 model."""

import json


ROOT_FIELDS = {"state", "questions", "max_len", "head_max_len"}
STATE_FIELDS = {"page", "recent_actions"}
PAGE_FIELDS = {"url", "title", "text"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _positive_integer(value, name):
    if value is None:
        return None
    _require(type(value) is int and value > 0, f"{name} must be a positive integer")
    return value


def parse_v3_state(value):
    _require(isinstance(value, str), "state must be a JSON string")
    try:
        state = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("state must contain valid JSON") from error

    _require(isinstance(state, dict) and set(state) == STATE_FIELDS,
             "state must contain only page and recent_actions")
    page = state["page"]
    _require(isinstance(page, dict) and set(page) == PAGE_FIELDS,
             "page must contain only url, title, and text")
    _require(all(isinstance(page[key], str) for key in PAGE_FIELDS),
             "page url, title, and text must be strings")
    _require(isinstance(state["recent_actions"], list),
             "recent_actions must be a list")

    # Format v3 keeps page context separate from the option table. Deserialize
    # the canonical wire string without shortening any text or action history.
    return {"page": dict(page), "recent_actions": list(state["recent_actions"])}


def parse_v3_questions(value):
    _require(isinstance(value, dict) and set(value) == {"click_target"},
             "questions must contain the click_target decision")
    question = value["click_target"]
    _require(isinstance(question, dict) and
             set(question) == {"type", "instructions", "criteria"},
             "click_target must contain type, instructions, and criteria")
    _require(question["type"] == "choice", "click_target type must be choice")
    _require(isinstance(question["instructions"], str),
             "click_target instructions must be a string")
    criteria = question["criteria"]
    _require(isinstance(criteria, dict) and bool(criteria),
             "click_target criteria must be a non-empty choice map")
    _require(all(isinstance(key, str) and isinstance(description, str)
                 for key, description in criteria.items()),
             "choice keys and descriptions must be strings")

    # AICODE-NOTE: Preserve every v3 semantic choice; only native Laya
    # token accounting may report context truncation after full encoding.
    return {"click_target": {
        "type": "choice",
        "instructions": question["instructions"],
        "criteria": dict(criteria),
    }}


def prepare_v3_request(payload):
    _require(isinstance(payload, dict), "request body must be an object")
    _require(set(payload).issubset(ROOT_FIELDS) and {"state", "questions"}.issubset(payload),
             "request must contain only state, questions, and optional context overrides")
    state = parse_v3_state(payload["state"])
    questions = parse_v3_questions(payload["questions"])
    requested = {
        "max_len": _positive_integer(payload.get("max_len"), "max_len"),
        "head_max_len": _positive_integer(payload.get("head_max_len"), "head_max_len"),
    }
    return state, questions, requested


def predict_prepared_request(agent, state, questions, requested, *, native_options=None):
    config = agent.cfg
    _require(config.get("laya_fmt") == "v3", "checkpoint config must select format v3")
    native_max_len = _positive_integer(config.get("max_len"), "checkpoint max_len")
    trained_head_len = _positive_integer(config.get("head_max_len_train"),
                                         "checkpoint head_max_len_train")
    _require(native_max_len is not None and trained_head_len is not None,
             "checkpoint native context values are missing")
    config["head_max_len"] = trained_head_len

    effective = {
        "max_len": requested["max_len"] if requested["max_len"] is not None else native_max_len,
        "head_max_len": requested["head_max_len"] if requested["head_max_len"] is not None else trained_head_len,
    }
    predict_options = dict(native_options or {})
    predict_options.update(
        max_len=requested["max_len"],
        head_max_len=requested["head_max_len"],
    )
    result = agent.predict(state, questions, **predict_options)
    return result, {"requested": requested, "effective": effective}


def predict_browser_request(agent, payload, *, native_options=None):
    state, questions, requested = prepare_v3_request(payload)
    return predict_prepared_request(
        agent, state, questions, requested, native_options=native_options
    )


class BrowserAgentAdapter:
    """Apply the browser checkpoint's v3 transform inside its profile worker."""

    def __init__(self, native_agent):
        self.native_agent = native_agent

    def __getattr__(self, name):
        return getattr(self.native_agent, name)

    def system_one(self, state, questions, lang=None, hooks=None,
                   on_predict_start=None, on_predict_end=None, hooks_raise=None,
                   hooks_timeout=None, max_len=None, head_max_len=None,
                   min_confidence=None):
        payload = {"state": state, "questions": questions}
        if max_len is not None:
            payload["max_len"] = max_len
        if head_max_len is not None:
            payload["head_max_len"] = head_max_len
        native_options = {"lang": lang} if lang is not None else None
        result, context = predict_browser_request(
            self.native_agent, payload, native_options=native_options
        )
        result["requested_context"] = context["requested"]
        result["effective_context"] = context["effective"]
        return result
