"""F2011: semantic candidate retrieval followed by strict condition selection."""
from dart_insight.rag.prompts.selection import DISCOVERY_PROMPT, selected_ids
from dart_insight.vectorstore.metadata import query_metadata


def discover(collection, condition, embeddings, settings, llm, corp_code=None):
    if not condition.strip():
        raise ValueError('검색할 자연어 조건을 입력해 주세요.')
    rows = query_metadata(collection, condition, embeddings, model=settings.embedding_model,
                          corp_code=corp_code, top_k=settings.candidate_top_k)
    if not rows:
        return []
    candidates = {row['metadata']['rcept_no']: row for row in rows}
    chosen = llm.json(DISCOVERY_PROMPT,
                      {'condition': condition, 'candidates': [r['metadata'] for r in rows]},
                      lambda value: selected_ids(value, candidates))
    return [candidates[receipt] for receipt in chosen]
