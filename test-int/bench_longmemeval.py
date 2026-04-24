"""LongMemEval retrieval benchmark for Basic Memory FTS.

Mirrors MemPalace's raw-mode protocol (session granularity):
- For each question: ingest all haystack_sessions[] as entities, indexed via FTS.
- Query with entry["question"], limit=50.
- Score: does any answer_session_id appear in top-K?

Matches MemPalace's longmemeval_bench.py so numbers are directly comparable.

Invoke:
    BASIC_MEMORY_ENV=test LME_LIMIT=20 uv run pytest -p pytest_mock -v --no-cov \
      test-int/bench_longmemeval.py -s
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchRetrievalMode


DATASET = Path("/Users/tyler/code/personal/mempalace/data/longmemeval_s_cleaned.json")


def _ndcg(ranked_session_ids: list[str], truth: set[str], k: int) -> float:
    rels = [1.0 if sid in truth else 0.0 for sid in ranked_session_ids[:k]]
    ideal = sorted(rels, reverse=True)
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rels))
    idcg = sum(r / math.log2(i + 2) for i, r in enumerate(ideal))
    return (dcg / idcg) if idcg > 0 else 0.0


async def _truncate_entities(session_maker) -> None:
    async with db.scoped_session(session_maker) as s:
        # Clear entities and search index; leave project/config intact
        await s.execute(text("DELETE FROM observation"))
        await s.execute(text("DELETE FROM relation"))
        await s.execute(text("DELETE FROM entity"))
        await s.execute(text("DELETE FROM search_index"))


@pytest.mark.asyncio
@pytest.mark.benchmark
async def test_bench_longmemeval_basic_memory_fts(search_service, engine_factory, app_config):
    """Run LongMemEval raw-mode protocol through Basic Memory FTS."""
    if not DATASET.exists():
        pytest.skip(f"LongMemEval dataset not found at {DATASET}")

    _engine, session_maker = engine_factory
    data = json.loads(DATASET.read_text())
    limit = int(os.environ.get("LME_LIMIT", "20"))
    questions = data[:limit]

    rows = []
    t_start = time.perf_counter()

    for q_idx, q in enumerate(questions):
        # Fresh corpus per question
        await _truncate_entities(session_maker)
        await search_service.init_search_index()

        permalink_to_session_id: dict[str, str] = {}

        # Ingest each haystack session as an entity (user turns concatenated)
        for s_idx, (session_turns, session_id) in enumerate(
            zip(q["haystack_sessions"], q["haystack_session_ids"])
        ):
            user_turns = [t["content"] for t in session_turns if t["role"] == "user"]
            if not user_turns:
                continue
            content = "\n".join(user_turns)
            permalink = f"lme/{q['question_id']}/{s_idx:04d}"
            permalink_to_session_id[permalink] = session_id

            entity = await search_service.entity_repository.create(
                {
                    "title": f"Session {s_idx}",
                    "note_type": "benchmark",
                    "entity_metadata": {"tags": ["lme"], "status": "active"},
                    "content_type": "text/markdown",
                    "permalink": permalink,
                    "file_path": f"{permalink}.md",
                }
            )
            await search_service.index_entity_data(entity, content=content)

        # Query with the question text
        results = await search_service.search(
            SearchQuery(
                text=q["question"],
                entity_types=[SearchItemType.ENTITY],
                retrieval_mode=SearchRetrievalMode.FTS,
            ),
            limit=50,
        )

        ranked_session_ids: list[str] = []
        seen = set()
        for r in results:
            sid = permalink_to_session_id.get(r.permalink or "")
            if sid and sid not in seen:
                ranked_session_ids.append(sid)
                seen.add(sid)

        truth = set(q["answer_session_ids"])
        r1 = 1.0 if any(sid in truth for sid in ranked_session_ids[:1]) else 0.0
        r3 = 1.0 if any(sid in truth for sid in ranked_session_ids[:3]) else 0.0
        r5 = 1.0 if any(sid in truth for sid in ranked_session_ids[:5]) else 0.0
        r10 = 1.0 if any(sid in truth for sid in ranked_session_ids[:10]) else 0.0
        r30 = 1.0 if any(sid in truth for sid in ranked_session_ids[:30]) else 0.0
        ndcg5 = _ndcg(ranked_session_ids, truth, 5)
        ndcg10 = _ndcg(ranked_session_ids, truth, 10)

        tag = "HIT " if r5 else "miss"
        print(
            f"  [{q_idx+1:3d}/{limit}] {q['question_id']:10s} "
            f"R@1={int(r1)} R@5={int(r5)} R@10={int(r10)}  {tag}  "
            f"(corpus={len(permalink_to_session_id)}, hits={len(ranked_session_ids)})"
        )

        rows.append(
            {
                "qid": q["question_id"],
                "qtype": q.get("question_type"),
                "r1": r1,
                "r3": r3,
                "r5": r5,
                "r10": r10,
                "r30": r30,
                "ndcg5": ndcg5,
                "ndcg10": ndcg10,
            }
        )

    elapsed = time.perf_counter() - t_start
    n = len(rows)

    def avg(k):
        return sum(r[k] for r in rows) / n

    print()
    print("=" * 60)
    print(f"  RESULTS — Basic Memory (FTS, session granularity)")
    print("=" * 60)
    print(f"  Time: {elapsed:.1f}s ({elapsed/n:.2f}s per question)")
    print()
    print(f"  Recall@1:  {avg('r1'):.3f}")
    print(f"  Recall@3:  {avg('r3'):.3f}")
    print(f"  Recall@5:  {avg('r5'):.3f}    NDCG@5:  {avg('ndcg5'):.3f}")
    print(f"  Recall@10: {avg('r10'):.3f}   NDCG@10: {avg('ndcg10'):.3f}")
    print(f"  Recall@30: {avg('r30'):.3f}")
    print("=" * 60)

    # Write artifact
    ts = time.strftime("%Y%m%d_%H%M")
    artifact = Path(f"/tmp/basic_memory_lme_{n}q_{ts}.jsonl")
    with artifact.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"  Results saved to: {artifact}")
