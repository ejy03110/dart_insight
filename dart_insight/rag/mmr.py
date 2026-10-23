"""Optional cosine MMR comparison; does not change production retrieval or index data."""
import math
from dart_insight.vectorstore.embeddings import LOCAL_MODEL, DIMENSION

from dart_insight.vectorstore.metadata import check_collection_model, check_embedding_model


def _unit(vector):
    values = [float(value) for value in vector]
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError('finite_nonempty_vector_required')
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('nonzero_vector_required')
    return [value / norm for value in values]


def _cosine(left, right):
    if len(left) != len(right):
        raise ValueError('vector_dimension_mismatch')
    return max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(left, right))))


def mmr_indices(query, vectors, *, k=5, lambda_mult=0.5):
    """Greedy MMR: lambda*cos(q,d) - (1-lambda)*max cos(d,selected).

    Start with the most relevant candidate; ties retain input order. lambda=1
    gives cosine relevance ranking. No embedding/model/index writes occur here.
    """
    if type(k) is not int or k < 1 or not math.isfinite(lambda_mult) or not 0 <= lambda_mult <= 1:
        raise ValueError('invalid_mmr_parameters')
    query = _unit(query)
    normalized = [_unit(vector) for vector in vectors]
    relevance = [_cosine(query, vector) for vector in normalized]
    selected = []
    remaining = list(range(len(normalized)))
    while remaining and len(selected) < k:
        def score(index):
            if not selected:
                return relevance[index]
            redundancy = max(_cosine(normalized[index], normalized[other]) for other in selected)
            return lambda_mult * relevance[index] - (1 - lambda_mult) * redundancy
        chosen = max(remaining, key=score)
        selected.append(chosen)
        remaining.remove(chosen)
    return selected


def compare_search(collection, embeddings, question, *, k=5, fetch_k=20, lambda_mult=0.5, where=None):
    """One BGE query + one existing Chroma candidate pool for both rankings."""
    if not question.strip() or type(fetch_k) is not int or type(k) is not int or not 1 <= k <= fetch_k <= 1000:
        raise ValueError('invalid_search_parameters')
    if not math.isfinite(lambda_mult) or not 0 <= lambda_mult <= 1:
        raise ValueError('invalid_mmr_parameters')
    check_collection_model(collection, LOCAL_MODEL)
    check_embedding_model(embeddings, LOCAL_MODEL)
    if (collection.metadata or {}).get('hnsw:space') != 'cosine':
        raise ValueError('cosine_collection_required')
    count = collection.count()
    if count == 0:
        raise ValueError('empty_index')
    vector = embeddings.embed_query(question)
    if len(vector) != DIMENSION:
        raise ValueError('bge_dimension_required')
    result = collection.query(query_embeddings=[vector], n_results=min(fetch_k, count),
                              include=['documents','metadatas','embeddings'],
                              **({'where':where} if where else {}))
    ids = result['ids'][0]
    vectors = result['embeddings'][0]
    mmr = mmr_indices(vector, vectors, k=k, lambda_mult=lambda_mult)
    query = _unit(vector)
    normalized = [_unit(value) for value in vectors]
    relevance = [_cosine(query,value) for value in normalized]
    similarity = sorted(range(len(ids)), key=lambda i: relevance[i], reverse=True)[:k]

    def rows(indices):
        return [{'id':ids[i], 'metadata':result['metadatas'][0][i],
                 'query_cosine':relevance[i]} for i in indices]

    def metrics(indices):
        pairs = [_cosine(normalized[a],normalized[b]) for i,a in enumerate(indices) for b in indices[i+1:]]
        documents = {result['metadatas'][0][i].get('document_rcept_no',
                     result['metadatas'][0][i].get('rcept_no',ids[i])) for i in indices}
        return {'unique_documents':len(documents),
                'mean_pairwise_cosine':math.fsum(pairs)/len(pairs) if pairs else None}
    return {'candidate_count':len(ids), 'k':k, 'fetch_k':fetch_k, 'lambda_mult':lambda_mult,
            'similarity':rows(similarity), 'mmr':rows(mmr),
            'similarity_metrics':metrics(similarity), 'mmr_metrics':metrics(mmr)}
