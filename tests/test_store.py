"""Hybrid index: exact-identifier wins via BM25, incremental re-embedding, persistence.

Uses a fake embedder (deterministic bag-of-token-hash vectors) so no 2GB model
download is needed — which is exactly why everything takes embed_fn injection.
"""
import numpy as np

from indexer.chunker import Chunk
from indexer.store import HybridIndex, tokenize

DIM = 64


class FakeEmbedder:
    """Deterministic: hash each token into one of DIM buckets, L2-normalize.
    Crude, but 'similar text -> similar vector' holds well enough to test with."""

    def __init__(self):
        self.calls = 0
        self.texts_embedded = []

    def __call__(self, texts):
        self.calls += 1
        self.texts_embedded.extend(texts)
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for i, text in enumerate(texts):
            for tok in tokenize(text):
                out[i, hash(tok) % DIM] += 1.0
            norm = np.linalg.norm(out[i])
            if norm:
                out[i] /= norm
        return out


def make_chunk(path, symbol, text, start=1):
    return Chunk(repo="r", path=path, language="python", symbol=symbol,
                 symbol_type="function", start_line=start,
                 end_line=start + text.count("\n"), text=text)


FILES_V1 = {
    "payments.py": ("hash-pay-1", [
        make_chunk("payments.py", "process_refund",
                   "def process_refund(order_id):\n    return refund(order_id)"),
        make_chunk("payments.py", "charge",
                   "def charge(card, amount):\n    return gateway.charge(card, amount)", start=10),
    ]),
    "auth.py": ("hash-auth-1", [
        make_chunk("auth.py", "verify_token",
                   "def verify_token(jwt):\n    return decode(jwt)"),
    ]),
}


def test_tokenize_splits_identifiers():
    assert "process_refund" in tokenize("process_refund")   # exact identifier kept
    assert "refund" in tokenize("process_refund")           # sub-word kept
    assert "payment" in tokenize("PaymentService")          # camelCase split


def test_exact_identifier_query_ranks_right_chunk_first(tmp_path):
    index = HybridIndex(tmp_path, FakeEmbedder())
    index.update(FILES_V1)
    results = index.search("process_refund", k=3)
    assert results[0][0].symbol == "process_refund"


def test_incremental_update_only_reembeds_changed_files(tmp_path):
    embedder = FakeEmbedder()
    index = HybridIndex(tmp_path, embedder)
    index.update(FILES_V1)
    embedder.texts_embedded.clear()

    files_v2 = dict(FILES_V1)
    files_v2["auth.py"] = ("hash-auth-2", [
        make_chunk("auth.py", "verify_token",
                   "def verify_token(jwt):\n    return decode_v2(jwt)"),
    ])
    stats = index.update(files_v2)

    assert stats["changed"] == 1 and stats["unchanged"] == 1
    assert all("auth.py" in t or "verify_token" in t for t in embedder.texts_embedded)
    assert len(embedder.texts_embedded) == 1        # payments.py chunks NOT re-embedded


def test_noop_update_embeds_nothing(tmp_path):
    embedder = FakeEmbedder()
    index = HybridIndex(tmp_path, embedder)
    index.update(FILES_V1)
    embedder.texts_embedded.clear()
    stats = index.update(FILES_V1)
    assert stats["changed"] == 0
    assert embedder.texts_embedded == []


def test_deleted_file_leaves_index(tmp_path):
    index = HybridIndex(tmp_path, FakeEmbedder())
    index.update(FILES_V1)
    files_v2 = {"payments.py": FILES_V1["payments.py"]}
    stats = index.update(files_v2)
    assert stats["removed"] == 1
    assert all(c.path != "auth.py" for c, _ in index.search("verify_token", k=5))


def test_persistence_roundtrip(tmp_path):
    HybridIndex(tmp_path, FakeEmbedder()).update(FILES_V1)
    reopened = HybridIndex(tmp_path, FakeEmbedder())   # loads from disk
    assert len(reopened.chunks) == 3
    assert reopened.search("process_refund", k=1)[0][0].symbol == "process_refund"


def test_prose_query_routes_to_dense_only(tmp_path):
    index = HybridIndex(tmp_path, FakeEmbedder())
    index.update(FILES_V1)
    prose = index.search("how are refunds processed for an order?", k=3)
    dense = index.search_dense_only("how are refunds processed for an order?", k=3)
    assert [c.chunk_id for c, _ in prose] == [c.chunk_id for c, _ in dense]


def test_identifier_query_routes_to_fusion(tmp_path):
    index = HybridIndex(tmp_path, FakeEmbedder())
    index.update(FILES_V1)
    assert index.search("verify_token", k=1)[0][0].symbol == "verify_token"
