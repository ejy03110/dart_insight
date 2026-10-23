"""Bounded real OpenDART/BGE-M3/GMS verification; never fabricates a passing run.

Run from repository root, with securely injected credentials. Reports only presence,
counts and sanitized error types; never prints keys, prompts or provider responses.
Uses an isolated NEW DB/data directory so the operational DB stays untouched.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
from time import monotonic
from unittest.mock import patch
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dart_insight.config import load_settings
from dart_insight.data.models import COMPANIES
from dart_insight.opendart.client import DartClient
from dart_insight.rag.prompts.answer import ANALYSIS_QUESTION
from dart_insight.services.runtime import Runtime
from dart_insight.services.original_documents import OpenDartSource
from dart_insight.vectorstore.documents import index_documents
from dart_insight.services.disclosure_search import DisclosureSearchService, SearchQuery


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stock',choices=[c.stock_code for c in COMPANIES],default='005930')
    parser.add_argument('--receipt',help='Optional actual receipt present in the last 7 days')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    settings=load_settings()
    report={'mode':'real-opendart-bge-gms','status':'blocked','runs':[],
            'credential_presence':{'DART_API_KEY':bool(settings.dart_api_key),'GMS_API_KEY_or_alias':bool(settings.openai_api_key)},
            'embedding_model':settings.embedding_model,'embedding_device':settings.embedding_device}
    def save():
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'status':report['status'],'report_path':str(args.output)},ensure_ascii=False))
    if not all(report['credential_presence'].values()):
        report['blocker']='missing_credentials';save();return 2
    if settings.embedding_model!='BAAI/bge-m3':
        report['blocker']='unexpected_embedding_model';save();return 2
    selected=None
    started=monotonic()
    try:
        # Resolve official corporate identity; never infer it from names/memory.
        company=next(c for c in COMPANIES if c.stock_code==args.stock)
        today=datetime.now(ZoneInfo('Asia/Seoul')).date()
        with DartClient(settings) as client:
            corp=client.resolve_company(company)
            # At most two metadata pages; the test needs one actual original, not
            # an unbounded data collection. No paid LLM calls if no item is found.
        page=DisclosureSearchService(settings).search(SearchQuery(corp['corp_code'],today-timedelta(days=6),today))
        if page.state=='error':
            report['blocker']='opendart_metadata_search_failed';save();return 2
        rows=page.items
        ordered=sorted(rows,key=lambda d:(d.rcept_dt,d.rcept_no),reverse=True)
        selected=next((d for d in ordered if not args.receipt or d.rcept_no==args.receipt),None)
        if selected is None:
            report['blocker']='no_matching_recent_disclosure';save();return 2
        report['receipt']=selected.rcept_no
        # Notify resource/model scope before the first BGE load. Never include keys.
        print(f'Live validation: selected original 1; BAAI/bge-m3; device={settings.embedding_device}; real GMS calls enabled.')
        with tempfile.TemporaryDirectory(prefix='dart-ux05-live-',dir='/workspace') as directory:
            isolated=replace(settings,data_dir=Path(directory),db_dir=Path(directory)/'chroma',document_bundle=None,embedding_cache_dir=settings.embedding_cache_dir or settings.data_dir/'models')
            with patch.object(OpenDartSource,'collect',autospec=True,side_effect=OpenDartSource.collect) as collected,patch('dart_insight.services.rag_service.index_documents',wraps=index_documents) as indexed:
                with Runtime(isolated,None,None) as runtime:
                    # First run must prepare real original + real vectors, then generate.
                    first=runtime.ask(selected,ANALYSIS_QUESTION)
                    own=runtime.service.documents.get(where={'$and':[{'item_rcept_no':selected.rcept_no},{'document_rcept_no':selected.rcept_no}]},include=[])
                    if not own['ids'] or not any(c['source_type']=='opendart_document' for c in first['citations']):
                        raise AssertionError('missing_original_evidence')
                    counts=(collected.call_count,indexed.call_count,runtime.service.documents.count())
                    report['runs'].append({'kind':'initially_unindexed','status':'passed','original_chunks':len(own['ids']),'citations':len(first['citations']),'collect_calls':counts[0],'index_calls':counts[1]})
                    second=runtime.ask(selected,ANALYSIS_QUESTION)
                    after=(collected.call_count,indexed.call_count,runtime.service.documents.count())
                    if after!=counts or not second['audit'].get('reused_original_index'):
                        raise AssertionError('indexed_run_recollected_or_reindexed')
                    report['runs'].append({'kind':'already_indexed','status':'passed','no_recollection_or_reindex':True,'citations':len(second['citations'])})
        report['status']='passed';report['seconds']=round(monotonic()-started,3)
    except Exception as exc:
        report['status']='failed'
        report['error_type']=type(exc).__name__
        report['error_category']=getattr(exc,'category','processing')
        # Raw provider/error strings can contain credentials or private content.
    save()
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
