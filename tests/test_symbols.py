"""Symbol table: definitions found, cross-file references resolved."""
from indexer.symbols import SymbolTable

FILE_A = """\
def process_refund(order_id):
    return order_id
"""

FILE_B = """\
from payments import process_refund

def handler(evt):
    return process_refund(evt.id)
"""


def build():
    table = SymbolTable()
    table.add_file(FILE_A, "payments.py", "python")
    table.add_file(FILE_B, "handlers.py", "python")
    table.resolve_references()
    return table


def test_definition_location():
    result = build().lookup("process_refund")
    assert result["definitions"] == [{"path": "payments.py", "line": 1}]


def test_cross_file_references():
    result = build().lookup("process_refund")
    ref_paths = {r["path"] for r in result["references"]}
    assert "handlers.py" in ref_paths            # the import + the call site
    assert len(result["references"]) == 2


def test_definition_site_not_counted_as_reference():
    result = build().lookup("process_refund")
    assert {"path": "payments.py", "line": 1} not in result["references"]


def test_unknown_symbol():
    result = build().lookup("does_not_exist")
    assert result["definitions"] == [] and result["references"] == []


def test_save_load_roundtrip(tmp_path):
    build().save(tmp_path / "symbols.json")
    loaded = SymbolTable.load(tmp_path / "symbols.json")
    assert loaded.lookup("process_refund") == build().lookup("process_refund")
