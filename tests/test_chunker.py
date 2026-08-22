"""AST chunking: boundaries, headers, line numbers, class skeletons, oversized splits."""
import textwrap

from indexer.chunker import MAX_CHUNK_LINES, chunk_source

PY_SAMPLE = textwrap.dedent('''\
    """Payments module."""
    import logging

    TAX_RATE = 0.18


    def validate(order_id):
        return order_id > 0


    class PaymentService:
        """Handles refunds."""

        retries = 3

        def process_refund(self, order_id):
            if not validate(order_id):
                raise ValueError(order_id)
            return {"ok": True}

        def audit(self):
            return self.retries
''')


def chunks_of(source, language="python"):
    return chunk_source(source, repo="testrepo", rel_path="app/payments.py", language=language)


def test_boundaries_and_types():
    chunks = chunks_of(PY_SAMPLE)
    by_symbol = {c.symbol: c for c in chunks}
    assert by_symbol["(module)"].symbol_type == "module"
    assert by_symbol["validate"].symbol_type == "function"
    assert by_symbol["PaymentService"].symbol_type == "class"
    assert by_symbol["process_refund"].symbol_type == "method"
    assert by_symbol["process_refund"].parent_symbol == "PaymentService"


def test_line_numbers_are_exact():
    chunks = chunks_of(PY_SAMPLE)
    fn = next(c for c in chunks if c.symbol == "validate")
    lines = PY_SAMPLE.splitlines()
    assert lines[fn.start_line - 1].startswith("def validate")
    assert fn.text.splitlines()[0].startswith("def validate")


def test_context_header_format():
    chunks = chunks_of(PY_SAMPLE)
    method = next(c for c in chunks if c.symbol == "process_refund")
    assert method.header.startswith("# testrepo/app/payments.py :: PaymentService :: process_refund")
    assert f"(lines {method.start_line}-{method.end_line})" in method.header
    assert method.embed_text.startswith(method.header)


def test_class_skeleton_elides_method_bodies_but_keeps_signatures():
    chunks = chunks_of(PY_SAMPLE)
    skeleton = next(c for c in chunks if c.symbol == "PaymentService").text
    assert "def process_refund" in skeleton          # signature kept
    assert "raise ValueError" not in skeleton        # body elided
    assert "retries = 3" in skeleton                 # class-level code kept
    assert '"""Handles refunds."""' in skeleton      # docstring kept


def test_nothing_is_lost():
    """Every non-blank source line appears in at least one chunk."""
    chunks = chunks_of(PY_SAMPLE)
    covered = set()
    for c in chunks:
        covered.update(range(c.start_line, c.end_line + 1))
    for i, line in enumerate(PY_SAMPLE.splitlines(), start=1):
        if line.strip():
            assert i in covered, f"line {i} ({line!r}) not covered by any chunk"


def test_oversized_function_splits_and_repeats_signature():
    body = "\n".join(f"    x{i} = {i}" for i in range(MAX_CHUNK_LINES + 30))
    source = f"def huge(a, b):\n{body}\n"
    chunks = chunks_of(source)
    assert len(chunks) >= 2
    assert all(c.symbol == "huge" for c in chunks)
    assert chunks[0].part == 1 and chunks[0].parts == len(chunks)
    for cont in chunks[1:]:
        assert cont.text.splitlines()[0].startswith("def huge")  # signature repeated
        assert "[part" in cont.header


def test_javascript_functions_and_classes():
    js = textwrap.dedent('''\
        const RATE = 0.18;

        export function validate(id) {
          return id > 0;
        }

        const refund = (id) => {
          return { ok: true };
        };

        class PaymentService {
          processRefund(id) {
            return refund(id);
          }
        }
    ''')
    chunks = chunks_of(js, language="javascript")
    symbols = {c.symbol: c.symbol_type for c in chunks}
    assert symbols["validate"] == "function"
    assert symbols["refund"] == "function"          # arrow function via const
    assert symbols["PaymentService"] == "class"
    assert symbols["processRefund"] == "method"


def test_markdown_splits_on_headings():
    md = "intro line\n\n# Setup\npip install\n\n## Usage\nrun it\n"
    chunks = chunk_source(md, repo="r", rel_path="README.md", language="markdown")
    assert [c.symbol for c in chunks] == ["(preamble)", "Setup", "Usage"]


def test_config_kept_whole():
    chunks = chunk_source("a: 1\nb: 2\n", repo="r", rel_path="conf/app.yaml", language="config")
    assert len(chunks) == 1
    assert chunks[0].symbol_type == "config"
