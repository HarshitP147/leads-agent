# Confidence Scoring (`scoring.py`)

The score is a weighted blend of hard signals plus the model's self-rating. It is
deterministic given the state, and every component is written to output so a reviewer
can see why a domain scored what it did.

| Component | Weight | Value in [0,1] |
|---|---|---|
| `field_coverage` | 0.30 | fraction of {overview, target_audience, ≥1 email, ≥1 leader, industries} present |
| `leader_quality` | 0.20 | mean over leaders of (verified 0.5 + title 0.25 + linkedin 0.25); 0 if none |
| `source_coverage` | 0.20 | fraction of target kinds {about/company, team/leadership, contact, pricing} fetched `ok` |
| `fetch_health` | 0.10 | ok pages / attempted pages |
| `llm_self` | 0.20 | `extraction.self_confidence` |

```
score = round(sum(w_i * c_i), 2)
```

Penalties (applied after, floor at 0):
- `-0.10` if any page was `blocked`.
- `-0.05` per dropped unverified person (max `-0.15`) — the model tried to invent people.

Hard rules:
- No extraction → score `0.0`.
- Homepage failed → score ≤ `0.2`.

Weights live as constants at the top of `scoring.py`. Unit-test with hand-built states:
a "perfect" state scores ≥ 0.9, an empty one scores 0.0.

**Why not just ask the LLM?** Self-reported confidence is poorly calibrated and can't see
what the scraper failed to fetch. The blend keeps the model's judgement on content
quality but anchors the score in observable facts.
