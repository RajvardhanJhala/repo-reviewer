"""Agent loop + tools, driven by a scripted fake LLM — no API, no model download."""
import json
from dataclasses import dataclass, field

import pytest

from indexer.store import HybridIndex
from indexer.symbols import SymbolTable
from qa.agent import QAAgent
from qa.tools import QATools
from tests.test_store import FILES_V1, FakeEmbedder


@dataclass
class FakeResponse:
    text: str = ""
    tool_calls: list = field(default_factory=list)


class ScriptedLLM:
    """Returns queued responses in order; records every messages list it saw."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.seen = []

    def __call__(self, messages, **kwargs):
        self.seen.append([dict(m) for m in messages])
        return self.responses.pop(0)


def call(id_, name, **args):
    return {"id": id_, "name": name, "arguments": json.dumps(args)}


@pytest.fixture
def tools(tmp_path):
    (tmp_path / "payments.py").write_text(
        "def process_refund(order_id):\n    return refund(order_id)\n", encoding="utf-8")
    index = HybridIndex(tmp_path / "data", FakeEmbedder())
    index.update(FILES_V1)
    symbols = SymbolTable()
    symbols.add_file((tmp_path / "payments.py").read_text(encoding="utf-8"),
                     "payments.py", "python")
    symbols.resolve_references()
    return QATools(tmp_path, index, symbols)


def test_multi_hop_loop_executes_tools_and_validates_citations(tools):
    llm = ScriptedLLM([
        FakeResponse(tool_calls=[call("c1", "search_code", query="refunds")]),
        FakeResponse(tool_calls=[call("c2", "read_file", path="payments.py", start=1, end=2)]),
        FakeResponse(text="Refunds go through process_refund (payments.py:1-2)."),
    ])
    result = QAAgent(tools, chat_fn=llm).ask("How are refunds handled?")

    assert result.steps == 3
    assert [t["tool"] for t in result.tool_trace] == ["search_code", "read_file"]
    assert result.valid_citations and result.valid_citations[0].path == "payments.py"
    # Tool results must have been fed back into the conversation:
    final_messages = llm.seen[-1]
    assert any(m.get("role") == "tool" for m in final_messages)


def test_invalid_citations_are_flagged_not_dropped(tools):
    llm = ScriptedLLM([FakeResponse(
        text="See ghost.py:10-20 and payments.py:1-999 and payments.py:1-2.")])
    result = QAAgent(tools, chat_fn=llm).ask("q")
    by_path = {(c.path, c.valid) for c in result.citations}
    assert ("ghost.py", False) in by_path          # nonexistent file
    assert ("payments.py", True) in by_path        # the good one survives
    assert sum(not c.valid for c in result.citations) == 2


def test_step_limit_forces_final_answer(tools):
    looping = [FakeResponse(tool_calls=[call(f"c{i}", "list_directory")]) for i in range(3)]
    llm = ScriptedLLM([*looping, FakeResponse(text="Best guess.")])
    result = QAAgent(tools, chat_fn=llm, max_steps=3).ask("q")
    assert result.ran_out_of_steps
    assert result.answer == "Best guess."
    assert "Stop searching" in llm.seen[-1][-1]["content"]


def test_read_file_blocks_path_traversal(tools):
    out = tools.read_file("../../../etc/passwd")
    assert out.startswith("Error") and "outside the repository" in out


def test_read_file_clamps_range(tools):
    out = tools.read_file("payments.py", start=1, end=99999)
    assert "lines 1-2 of 2" in out


def test_unknown_tool_and_bad_args_return_errors(tools):
    assert tools.dispatch("rm_rf", {}).startswith("Error")
    assert tools.dispatch("read_file", {"nope": 1}).startswith("Error")


def test_search_code_formats_locations(tools):
    out = tools.search_code("process_refund")
    assert "payments.py:" in out and "[function] process_refund" in out
