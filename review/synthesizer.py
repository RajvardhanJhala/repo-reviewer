"""Synthesizer: three reviewers' raw findings -> few high-signal comments.

Nobody reads 40 bot comments. The product decision, in order:
  1. drop findings below the confidence floor,
  2. dedupe exact collisions (same file, same line) keeping the strongest —
     proximity-based dedupe was tried and wrongly merged DISTINCT issues on
     adjacent lines (see changelog entry 12),
  3. rank by (severity, confidence),
  4. cap the total per PR.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from review.reviewers import Finding

CONFIDENCE_FLOOR = 0.5
MAX_COMMENTS_PER_PR = 10
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass
class ReviewSummary:
    kept: list[Finding] = field(default_factory=list)
    dropped_low_confidence: int = 0
    dropped_duplicates: int = 0
    dropped_over_cap: int = 0

    def verdict(self) -> str:
        highs = sum(1 for f in self.kept if f.severity == "high")
        if highs:
            return f"{highs} high-severity finding(s) — please address before merging."
        if self.kept:
            return "Minor findings only — see inline comments."
        return "No significant issues found by automated review."


def synthesize(findings: list[Finding]) -> ReviewSummary:
    out = ReviewSummary()

    confident = []
    for f in findings:
        if f.confidence < CONFIDENCE_FLOOR:
            out.dropped_low_confidence += 1
        else:
            confident.append(f)

    # Strongest first, so dedupe naturally keeps the best of each collision.
    confident.sort(key=lambda f: (_SEVERITY_RANK[f.severity], -f.confidence))
    kept: list[Finding] = []
    for f in confident:
        dup = any(k.path == f.path and k.line == f.line for k in kept)
        if dup:
            out.dropped_duplicates += 1
        elif len(kept) < MAX_COMMENTS_PER_PR:
            kept.append(f)
        else:
            out.dropped_over_cap += 1

    out.kept = kept
    return out


def summary_comment(s: ReviewSummary) -> str:
    lines = [f"## Automated review — {s.verdict()}", ""]
    if s.kept:
        lines.append("| file:line | severity | category | reviewer |")
        lines.append("|---|---|---|---|")
        for f in s.kept:
            lines.append(f"| {f.path}:{f.line} | {f.severity} | {f.category} | {f.reviewer} |")
    dropped = s.dropped_low_confidence + s.dropped_duplicates + s.dropped_over_cap
    if dropped:
        lines.append(f"\n_{dropped} lower-signal finding(s) suppressed "
                     f"({s.dropped_low_confidence} low-confidence, {s.dropped_duplicates} duplicate, "
                     f"{s.dropped_over_cap} over cap)._")
    lines.append("\n_Advisory only — this bot never approves, blocks, or merges._")
    return "\n".join(lines)
