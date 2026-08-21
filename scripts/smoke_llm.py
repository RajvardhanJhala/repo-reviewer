"""Phase 0 milestone: prove every lane + local embeddings work.

Run:  python -m scripts.smoke_llm
"""
import time

from sentence_transformers import SentenceTransformer

from llm.router import router

PROMPT = [{"role": "user", "content": "Reply with exactly one word: pong"}]

for lane in ("fast", "quality"):
    r = router.chat(PROMPT, lane=lane, max_tokens=300)
    print(f"[{lane:7}] model={r.model_used:45} fell_back={r.fell_back} "
          f"tokens={r.prompt_tokens}+{r.completion_tokens} latency={r.latency_s:.2f}s -> {r.text.strip()!r}")

print("\nEmbedding smoke test (bge-m3, local, first run downloads ~2GB)...")

t0 = time.perf_counter()
model = SentenceTransformer("BAAI/bge-m3")
vecs = model.encode(["def process_refund(order_id):", "how are refunds handled?"], normalize_embeddings=True)
sim = float(vecs[0] @ vecs[1])
print(f"dim={vecs.shape[1]}  cosine(code, question)={sim:.3f}  load+encode={time.perf_counter()-t0:.1f}s")

print("\nRouter stats:", router.stats.summary())
