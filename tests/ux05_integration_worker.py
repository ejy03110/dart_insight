"""Actual Runtime/ZIP parser/Chroma/SQLite/LangGraph; provider and BGE boundaries mocked."""
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from test_rag_ui import FIRST, SECOND
from rag_integration_worker import Encoder, LLM
from dart_insight.config import Settings
from dart_insight.services.runtime import Runtime
from dart_insight.services.original_documents import OpenDartSource
from dart_insight.rag.prompts.answer import ANALYSIS_QUESTION
from dart_insight.vectorstore.documents import document_chunks
from dart_insight.services.analysis_preparation import original_index_state
from dart_insight.opendart.client import DartError
from dart_insight.ui.analysis import ensure_analysis, followup_history


def zipped(*members):
    output=BytesIO()
    with ZipFile(output,'w') as archive:
        for i,body in enumerate(members):archive.writestr(f'{i}.xml',body)
    return output.getvalue()


def main(root):
    check=unittest.TestCase()
    settings=Settings(data_dir=root,db_dir=root/'chroma',dart_api_key='test-only',openai_api_key='test-only',chunk_size=100,chunk_overlap=20)
    encoder=Encoder();llm=LLM()
    client=Mock();client.list_disclosures.return_value=[replace(SECOND,report_nm='사업 위험 보고서')]
    client._request.return_value=Mock(content=zipped(('<DOCUMENT><P>' + '사업 위험: 환율 변동 위험이 있습니다.\n'*30 + '</P></DOCUMENT>').encode()))
    source=OpenDartSource(settings,client)
    saved={};attempts={}
    with patch('dart_insight.services.runtime.create_embeddings',return_value=encoder),patch('dart_insight.services.runtime.GmsClient',return_value=llm),patch('dart_insight.services.runtime.OpenDartSource',return_value=source):
        # Real entry starts with no metadata or body database.
        runtime=Runtime(settings,None,None)
        # Avoid closing test LLM, whose only methods are inference boundaries.
        llm.close=lambda:None
        result=ensure_analysis(FIRST.rcept_no,saved,attempts,lambda:runtime.ask(FIRST,ANALYSIS_QUESTION))
        check.assertIn('[근거',result['content'])
        check.assertTrue(any(c['source_type']=='opendart_document' for c in result['citations']))
        check.assertEqual(client._request.call_count,1)
        plan=runtime.service.ledger.plan(FIRST.rcept_no,'위험')
        check.assertIn(SECOND.rcept_no,[c['document_rcept_no'] for c in plan['candidates']])
        check.assertEqual(plan['selected_ids'],[FIRST.rcept_no])
        check.assertIn(SECOND.rcept_no,plan['budget_excluded_ids'])
        check.assertEqual(runtime.metadata.count(),1)
        check.assertEqual(original_index_state(runtime.service.documents,runtime.service.ledger,FIRST,settings),'complete')
        before=runtime.service.documents.get(include=['documents','metadatas','embeddings'])
        from dart_insight.vectorstore.metadata import MetadataIndexError
        with check.assertRaises(MetadataIndexError):
            original_index_state(runtime.service.documents,runtime.service.ledger,FIRST,replace(settings,chunk_size=70))
        # UI rerun neither calls the service nor the providers again.
        no_run=Mock(side_effect=AssertionError('duplicate analysis'))
        ensure_analysis(FIRST.rcept_no,saved,attempts,no_run);no_run.assert_not_called()
        # A new analysis session uses the complete existing index without extraction,
        # selection, collection or document reindexing; only query embedding/generation.
        with patch('dart_insight.services.rag_service.index_documents',side_effect=AssertionError('unexpected reindex')),patch.object(source,'search',side_effect=AssertionError('unexpected search')),patch.object(source,'collect',side_effect=AssertionError('unexpected recollection')):
            reused=runtime.ask(FIRST,ANALYSIS_QUESTION)
            check.assertTrue(reused['audit']['reused_original_index'])
        after=runtime.service.documents.get(include=['documents','metadatas','embeddings'])
        check.assertEqual(before['ids'],after['ids'])
        check.assertEqual(before['documents'],after['documents'])
        check.assertEqual(before['metadatas'],after['metadatas'])
        check.assertEqual(client._request.call_count,1)
        # An invalid second XML member cannot yield partial success or generate an answer.
        client._request.return_value=Mock(content=zipped(b'<DOCUMENT>valid part</DOCUMENT>',b'<DOCUMENT>broken'))
        with patch.object(llm,'json',wraps=llm.json) as generated:
            with check.assertRaises(DartError):runtime.ask(SECOND,ANALYSIS_QUESTION)
            check.assertFalse(any('evidence' in call.args[1] for call in generated.call_args_list))
        check.assertNotIn(SECOND.rcept_no,runtime.service.ledger.documents_for_item(SECOND.rcept_no))
        check.assertFalse(runtime.service.documents.get(where={'item_rcept_no':SECOND.rcept_no},include=[])['ids'])
        # Same receipt can recover after full download succeeds; first item's DB stays intact.
        client._request.return_value=Mock(content=zipped('<DOCUMENT><P>환율 위험이 있습니다.</P></DOCUMENT>'.encode()))
        recovered=runtime.ask(SECOND,ANALYSIS_QUESTION)
        check.assertIn('[근거',recovered['answer'])
        check.assertEqual(runtime.metadata.count(),2)
        check.assertEqual(before['ids'],runtime.service.documents.get(where={'item_rcept_no':FIRST.rcept_no},include=[])['ids'])
        # Missing one chunk is incomplete, not a successful ready state. It can resume
        # from the full ledger original without another HTTP download.
        doc=runtime.service.ledger.documents_for_item(FIRST.rcept_no)[FIRST.rcept_no]
        # Simulate interruption by removing a test chunk in this temporary DB only.
        runtime.service.documents.delete(ids=[before['ids'][0]])
        check.assertEqual(original_index_state(runtime.service.documents,runtime.service.ledger,FIRST,settings),'partial')
        requests=client._request.call_count
        resumed=runtime.ask(FIRST,ANALYSIS_QUESTION)
        check.assertIn('[근거',resumed['answer'])
        check.assertEqual(client._request.call_count,requests)
        check.assertEqual(original_index_state(runtime.service.documents,runtime.service.ledger,FIRST,settings),'complete')
        print('UX05: new item, full parser, actual DB/graph, repeat/session reuse, atomic parse failure, recovery, resume PASS')

if __name__=='__main__':main(Path(sys.argv[1]))
