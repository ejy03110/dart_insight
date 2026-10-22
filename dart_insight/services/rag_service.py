"""F2003-F2008 orchestration with injectable external boundaries and durable resume."""
import hashlib

from dart_insight.data.collection_ledger import normalize_query
from dart_insight.data.documents import DocumentCandidate
from dart_insight.rag.pipeline import build_graph
from dart_insight.rag.prompts.selection import SELECTION_PROMPT, selected_ids
from dart_insight.rag.retrieval import retrieve
from dart_insight.services.candidate_search import search_candidates
from dart_insight.services.original_documents import selected_candidate
from dart_insight.vectorstore.documents import index_documents


class RagService:
    def __init__(self, settings, metadata, documents, embeddings, ledger, source, llm):
        self.settings, self.metadata, self.documents = settings, metadata, documents
        self.embeddings, self.ledger, self.source, self.llm = embeddings, ledger, source, llm

    def prepare(self, selected, question, progress=lambda _: None):
        progress('외부 문서 검색어 생성 중')
        queries = self.ledger.question_queries(selected.rcept_no, question)
        if queries is None:
            queries = list(dict.fromkeys([*self.llm.extract(selected, question), selected.rcept_no]))
            self.ledger.question_queries(selected.rcept_no, question, queries)
        cached_docs = self.ledger.documents_for_item(selected.rcept_no)
        audits = []
        normalized_question = normalize_query(question)
        question_id = hashlib.sha256(normalized_question.encode('utf-8')).hexdigest()
        for query in queries:
            search_plan = self.ledger.plan(selected.rcept_no, query)
            # Query search results are reusable; relevance selection depends on
            # the question. Keep old completed rows immutable and add a scoped row.
            record_query = query
            if search_plan is not None and search_plan.get('question') != normalized_question:
                record_query = f'{query} [질문별 선별:{question_id}]'
            row = self.ledger.get(selected.rcept_no, record_query)
            plan = self.ledger.plan(selected.rcept_no, record_query)
            if row is None or row.state == 'pending':
                self.ledger.ensure_pending(selected.rcept_no, record_query)
                if plan is None:
                    if search_plan is not None:
                        candidates = [DocumentCandidate(**c) for c in search_plan['candidates']]
                    else:
                        progress('외부 출처 및 메타데이터에서 문서 후보 검색 중')
                        external = self.source.search(query, selected)
                        local = search_candidates(self.metadata, [query], selected, self.embeddings,
                                                  model=self.settings.embedding_model,
                                                  top_k=self.settings.candidate_top_k, corp_code=selected.corp_code)
                        found = {c.document_rcept_no: c for c in external}
                        found.update({h.candidate.document_rcept_no: h.candidate for h in local.candidates})
                        if hasattr(self.source, 'documents'):
                            found = {key: c for key, c in found.items() if key in self.source.documents}
                        found[selected.rcept_no] = selected_candidate(selected)
                        # Bound prompt size; selected item always wins truncation as well as LLM exclusion.
                        candidates = [found[selected.rcept_no]] + [c for key, c in found.items()
                                                                  if key != selected.rcept_no][:self.settings.candidate_top_k * 2]
                    progress('LLM으로 관련 후보 문서 선별 중')
                    chosen = self.llm.json(SELECTION_PROMPT,
                        {'question': question, 'selected': selected.to_dict(),
                         'candidates': [c.to_dict() for c in candidates]},
                        lambda value: selected_ids(value, {c.document_rcept_no for c in candidates}))
                    chosen = list(dict.fromkeys([selected.rcept_no, *chosen]))
                    plan = {'query': query, 'question': normalized_question, 'candidates': [c.to_dict() for c in candidates], 'selected_ids': chosen,
                            'rejected_ids': [c.document_rcept_no for c in candidates if c.document_rcept_no not in chosen]}
                    self.ledger.plan(selected.rcept_no, record_query, plan)
                chosen = set(plan['selected_ids']) | {selected.rcept_no}
                docs = []
                for candidate_data in plan['candidates']:
                    candidate = DocumentCandidate(**candidate_data)
                    if candidate.document_rcept_no not in chosen:
                        continue
                    progress('선별 공시 원문 수집 중')
                    doc = cached_docs.get(candidate.document_rcept_no)
                    if doc is None:
                        doc = self.source.collect(candidate)
                        if doc.item_rcept_no != selected.rcept_no or doc.document_rcept_no != candidate.document_rcept_no:
                            raise ValueError('수집한 공시 원문의 식별자가 일치하지 않습니다.')
                        self.ledger.save_document(doc)
                        cached_docs[candidate.document_rcept_no] = doc
                    docs.append(doc)
                if not any(d.document_rcept_no == selected.rcept_no for d in docs):
                    raise ValueError('선택 공시 원문이 없습니다. 실제 원문을 준비해 주세요.')
                self.ledger.mark_collected(selected.rcept_no, record_query, docs)
                row = self.ledger.get(selected.rcept_no, record_query)
            progress('공시 원문 청킹·색인 및 저장 검증 중')
            # Verify even indexed records: settings/DB changes must not trust a stale ledger flag.
            index_documents(list(row.documents), self.documents, self.embeddings, self.settings)
            self.ledger.mark_indexed(selected.rcept_no, record_query)
            audits.append(plan or {'query': query, 'selected_ids': [d.document_rcept_no for d in row.documents],
                                   'rejected_ids': []})
        notes = list(dict.fromkeys(note for doc in cached_docs.values() for note in doc.parse_notes))
        return {'queries': queries, 'selection': audits, 'parse_notes': notes}

    def ask(self, selected, question, progress=lambda _: None):
        audit = self.prepare(selected, question, progress)
        graph = build_graph(lambda q: retrieve(self.metadata, self.documents, selected, q,
                                               self.embeddings, self.settings), self.llm, progress)
        result = graph.invoke({'question': question})
        return {**result, 'audit': audit}
