"""Server-run tests for lossless v10s format-v3 request adaptation."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from browser_adapter import predict_browser_request


class AgentRecorder:
    def __init__(self):
        self.cfg = {"laya_fmt": "v3", "max_len": 1024, "head_max_len_train": 768}
        self.calls = []

    def predict(self, state, questions, max_len=None, head_max_len=None):
        self.calls.append((state, questions, max_len, head_max_len))
        return {"answers": {"click_target": {"choice": next(iter(questions["click_target"]["criteria"]))}}}


def full_request():
    state = {
        "page": {
            "url": "https://books.toscrape.com/",
            "title": "Books to Scrape",
            "text": "PAGE " + ("readable noninteractive text " * 350),
        },
        "recent_actions": [{"operation": "WAIT", "target": None}],
    }
    criteria = {
        str(index): "Category number {} with a full semantic label, link role, and current value {}".format(index, "x" * 90)
        for index in range(1, 52)
    }
    return {
        "state": json.dumps(state, ensure_ascii=False),
        "questions": {
            "click_target": {
                "type": "choice",
                "instructions": "Goal: Open the requested category. Operation: CLICK. Choose only one offered key.",
                "criteria": criteria,
            }
        },
    }, state, criteria


class BrowserAdapterTests(unittest.TestCase):
    def test_format_v3_preserves_complete_state_instructions_and_all_51_choices(self):
        payload, state, criteria = full_request()
        agent = AgentRecorder()

        result, context = predict_browser_request(agent, payload)

        self.assertEqual(result["answers"]["click_target"]["choice"], "1")
        self.assertEqual(len(agent.calls), 1)
        called_state, called_questions, max_len, head_max_len = agent.calls[0]
        self.assertEqual(called_state, state)
        self.assertEqual(called_questions["click_target"]["instructions"], payload["questions"]["click_target"]["instructions"])
        self.assertEqual(called_questions["click_target"]["criteria"], criteria)
        self.assertEqual(list(called_questions["click_target"]["criteria"]), [str(index) for index in range(1, 52)])
        self.assertEqual(max_len, None)
        self.assertEqual(head_max_len, None)
        self.assertEqual(context["requested"], {"max_len": None, "head_max_len": None})
        self.assertEqual(context["effective"], {"max_len": 1024, "head_max_len": 768})
        self.assertEqual(agent.cfg["head_max_len"], 768)

    def test_explicit_native_context_overrides_are_forwarded_without_ceiling(self):
        payload, _, _ = full_request()
        payload["max_len"] = 8192
        payload["head_max_len"] = 8192
        agent = AgentRecorder()

        _, context = predict_browser_request(agent, payload)

        self.assertEqual(agent.calls[0][2:], (8192, 8192))
        self.assertEqual(context["effective"], {"max_len": 8192, "head_max_len": 8192})

    def test_adapter_rejects_non_v3_checkpoint(self):
        payload, _, _ = full_request()
        agent = AgentRecorder()
        agent.cfg["laya_fmt"] = "v5"

        with self.assertRaises(ValueError):
            predict_browser_request(agent, payload)
        self.assertEqual(agent.calls, [])

    def test_adapter_rejects_gold_metadata_at_request_root(self):
        payload, _, _ = full_request()
        payload["expected_url"] = "https://example.invalid/gold"

        with self.assertRaises(ValueError):
            predict_browser_request(AgentRecorder(), payload)


if __name__ == "__main__":
    unittest.main()
