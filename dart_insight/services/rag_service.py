"""F2003-F2008 orchestration with injectable external boundaries and durable resume."""
import hashlib
from dart_insight.diagnostics import measure, logger

from dart_insight.data.collection_ledger import normalize_query
from dart_insight.data.documents import DocumentCandidate
from dart_insight.opendart.client import DartError
from dart_insight.rag.pipeline import build_graph
from dart_insight.rag.conversation import recent_history, context_id, contextual_search, recall_answer, response_style
from dart_insight.rag.query_extraction import QueryExtractionError
from dart_insight.rag.prompts.selection import SELECTION_PROMPT, selected_ids
from dart_insight.rag.retrieval import retrieve
from dart_insight.services.candidate_search import search_candidates
from dart_insight.services.original_documents import selected_candidate
from dart_insight.vectorstore.documents import index_documents


class RagService:
    def __init__(self, settings, metadata, documents, embeddings, ledger, source, llm):
        self.settings, self.metadata, self.documents = settings, metadata, documents
        self.embeddings, self.ledger, self.source, self.llm = embeddings, ledger, source, llm

    def prepare(self, selected, question, progress=lambda _: None, *, history=None):
        recent = recent_history(history, selected.rcept_no)
        scope = context_id(recent) if recent else None
        cache_question = question + (f" [대화문맥:{scope}]" if scope else "")
        progress('외부 문서 검색어 생성 중')
        queries = self.ledger.question_queries(selected.rcept_no, cache_question)
        query_fallback = None
        if queries is None:
            try:
                extracted = self.llm.extract(selected, question, history=recent) if recent else self.llm.extract(selected, question)
                queries = list(dict.fromkeys([*extracted, selected.rcept_no]))
            except QueryExtractionError as exc:
                query_fallback = {'category': exc.category, 'http_status': exc.status}
                queries = list(dict.fromkeys([question.strip(), selected.rcept_no]))
                progress('검색어 추출 실패로 원래 질문과 접수번호로 검색을 계속합니다. ' + str(exc))
                logger.warning('stage=query_fallback category=%s http_status=%s', exc.category, exc.status)
                # Retry extraction next run; fallback is not a successful LLM result.
            else:
                self.ledger.question_queries(selected.rcept_no, cache_question, queries)
        cached_docs = self.ledger.documents_for_item(selected.rcept_no)
        logger.info('stage=document_cache reusable_documents=%s', len(cached_docs))
        audits = []
        verified_documents = set()
        normalized_question = normalize_query(question)
        question_id = hashlib.sha256(normalized_question.encode('utf-8')).hexdigest()
        for query in queries:
            search_plan = self.ledger.plan(selected.rcept_no, query)
            # Query search results are reusable; relevance selection depends on
            # the question. Keep old completed rows immutable and add a scoped row.
            record_query = f"{query} [대화문맥:{scope}]" if scope else query
            if not scope and search_plan is not None and search_plan.get('question') != normalized_question:
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
                        {'question': question, 'history': [t for t in recent if t['role'] == 'user'],
                         'selected': selected.to_dict(),
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
                    if candidate.item_rcept_no != selected.rcept_no or candidate.corp_code != selected.corp_code:
                        raise DartError('문서 후보의 공시·기업 식별자가 선택 항목과 일치하지 않습니다.', 'document_identity')
                    if candidate.document_rcept_no not in chosen:
                        continue
                    doc = cached_docs.get(candidate.document_rcept_no)
                    reused = doc is not None
                    if doc is None:
                        progress('원문 다운로드 중')
                        # The production source emits XML stage updates too.
                        if hasattr(self.source, 'set_progress'):
                            self.source.set_progress(progress)
                        with measure('document_collection'):
                            doc = self.source.collect(candidate)
                    if (doc.item_rcept_no != selected.rcept_no or doc.document_rcept_no != candidate.document_rcept_no
                            or doc.corp_code != candidate.corp_code):
                        raise DartError('수집한 공시 원문의 식별자가 일치하지 않습니다.', 'document_identity')
                    if not reused:
                        self.ledger.save_document(doc)
                        cached_docs[candidate.document_rcept_no] = doc
                    logger.info('stage=document_reuse reused=%s', reused)
                    docs.append(doc)
                if not any(d.document_rcept_no == selected.rcept_no for d in docs):
                    raise ValueError('선택 공시 원문이 없습니다. 실제 원문을 준비해 주세요.')
                self.ledger.mark_collected(selected.rcept_no, record_query, docs)
                row = self.ledger.get(selected.rcept_no, record_query)
            progress('임베딩 및 색인 중')
            # Verify even indexed records: settings/DB changes must not trust a stale ledger flag.
            if any(d.item_rcept_no != selected.rcept_no or d.corp_code != selected.corp_code for d in row.documents):
                raise DartError('저장된 원문의 공시·기업 식별자가 선택 항목과 다릅니다. 기존 데이터는 보존합니다.', 'document_identity')
            pending_docs = [d for d in row.documents if d.document_rcept_no not in verified_documents]
            if pending_docs:
                with measure('index_and_verify', documents=len(pending_docs)):
                    index_documents(pending_docs, self.documents, self.embeddings, self.settings)
                verified_documents.update(d.document_rcept_no for d in pending_docs)
            self.ledger.mark_indexed(selected.rcept_no, record_query)
            audits.append(plan or {'query': query, 'selected_ids': [d.document_rcept_no for d in row.documents],
                                   'rejected_ids': []})
        notes = list(dict.fromkeys(note for doc in cached_docs.values() for note in doc.parse_notes))
        return {'queries': queries, 'selection': audits, 'parse_notes': notes, 'query_fallback': query_fallback}

    def ask(self, selected, question, progress=lambda _: None, *, history=None):
        recent = recent_history(history, selected.rcept_no)
        recalled = recall_answer(question, recent)
        if recalled is not None:
            progress('이전 대화 확인 완료')
            return recalled
        audit = self.prepare(selected, question, progress, history=recent)
        audit.update(history_messages=len(recent), response_style=response_style(question), answer_basis='disclosure')
        graph = build_graph(lambda q: retrieve(self.metadata, self.documents, selected, contextual_search(q, recent),
                                               self.embeddings, self.settings), self.llm, progress, history=recent, receipt=selected.rcept_no)
        result = graph.invoke({'question': question, 'history': recent, 'response_style': response_style(question)})
        progress('완료')
        return {**result, 'audit': audit}
