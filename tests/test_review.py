"""Reviewer validation gates + synthesizer policy — scripted LLM, no API."""
import json

from review.diff import commentable_lines, parse_diff
from review.reviewers import Finding, _extract_json, run_reviewer
from review.synthesizer import CONFIDENCE_FLOOR, MAX_COMMENTS_PER_PR, summary_comment, synthesize
from tests.test_qa_agent import FakeResponse

DIFF = """\
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 import os
+import pickle
 import json
 import re
"""


def finding(path="app.py", line=2, severity="high", confidence=0.9, **kw):
    return Finding(path=path, line=line, severity=severity, category="test",
                   comment="This is a sufficiently long test comment.",
                   confidence=confidence, **kw)


class OneShotLLM:
    def __init__(self, payload):
        self.payload = payload

    def __call__(self, messages, **kwargs):
        return FakeResponse(text=self.payload)


def run(payload):
    files = parse_diff(DIFF)
    return run_reviewer("security", [], OneShotLLM(payload), commentable_lines(files))


def test_valid_finding_survives():
    payload = json.dumps({"findings": [{
        "path": "app.py", "line": 2, "severity": "high", "category": "unsafe-import",
        "comment": "pickle enables arbitrary code execution on load.",
        "confidence": 0.9, "suggested_fix": None}]})
    found = run(payload)
    assert len(found) == 1 and found[0].reviewer == "security"


def test_unanchored_finding_dropped():
    for bad_line in (99, 1_000):     # not lines present in the diff
        payload = json.dumps({"findings": [{
            "path": "app.py", "line": bad_line, "severity": "high", "category": "x",
            "comment": "Long enough comment to pass schema.", "confidence": 0.9}]})
        assert run(payload) == []


def test_malformed_findings_dropped_not_fatal():
    payload = json.dumps({"findings": [
        {"path": "app.py", "line": 2, "severity": "catastrophic",  # invalid enum
         "comment": "Long enough comment here.", "category": "x", "confidence": 0.9},
        {"path": "app.py", "line": 2, "severity": "low", "category": "x",
         "comment": "Valid one survives alongside the invalid.", "confidence": 0.8},
    ]})
    found = run(payload)
    assert len(found) == 1 and found[0].severity == "low"


def test_json_dug_out_of_prose_and_fences():
    wrapped = "Here is my review:\n```json\n" + json.dumps(
        {"findings": []}) + "\n```\nHope this helps!"
    assert _extract_json(wrapped) == {"findings": []}
    assert _extract_json("no json at all") is None


def test_unparseable_output_returns_empty_not_crash():
    assert run("I refuse to produce JSON today.") == []


def test_synthesizer_confidence_floor():
    s = synthesize([finding(confidence=CONFIDENCE_FLOOR - 0.1),
                    finding(line=3, confidence=0.9)])
    assert len(s.kept) == 1 and s.dropped_low_confidence == 1


def test_synthesizer_dedupes_same_line_keeping_strongest():
    s = synthesize([finding(line=2, severity="low", confidence=0.6),
                    finding(line=2, severity="high", confidence=0.95)])
    assert len(s.kept) == 1
    assert s.kept[0].severity == "high" and s.dropped_duplicates == 1


def test_distinct_issues_on_adjacent_lines_both_survive():
    s = synthesize([finding(line=2, severity="low"),
                    finding(line=3, severity="high")])
    assert len(s.kept) == 2 and s.dropped_duplicates == 0


def test_synthesizer_caps_total():
    many = [finding(line=1 + i * 10, path=f"f{i}.py") for i in range(MAX_COMMENTS_PER_PR + 5)]
    s = synthesize(many)
    assert len(s.kept) == MAX_COMMENTS_PER_PR and s.dropped_over_cap == 5


def test_summary_comment_never_sounds_like_approval():
    text = summary_comment(synthesize([]))
    assert "never approves" in text
    assert "No significant issues" in text
