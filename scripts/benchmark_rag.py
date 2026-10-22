"""Manual Linux synthetic comparison, NOT a real BGE/GMS benchmark.
Run from repository root: python scripts/benchmark_rag.py
Uses separate temporary DBs; never opens the user's data directory.
"""
import sys,runpy,tempfile,subprocess,time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
w=runpy.run_path('tests/rag_integration_worker.py')
from dart_insight.services.rag_service import RagService
from dart_insight.data.documents import CollectedDocument
old={ '__name__':'baseline' }
exec(subprocess.check_output(['git','show','eed60e5:dart_insight/services/rag_service.py'],text=True),old)
class BigSource(w['Source']):
 def collect(self,c):
  self.collect_calls.append(c.document_rcept_no)
  return CollectedDocument(**c.to_dict(),text=('사업 위험: 환율 변동 위험. 연구개발 및 매출 공시 본문.\n'*10000),collected_at='2026-10-22T00:00:00+09:00')
for label,cls in [('baseline',old['RagService']),('optimized',RagService)]:
 with tempfile.TemporaryDirectory() as temp:
  root=Path(temp);settings=w['Settings'](data_dir=root);enc=w['Encoder']()
  metadata=w['open_metadata_collection'](root/'db',w['LOCAL_MODEL']);docs=w['open_document_collection'](root/'db',w['LOCAL_MODEL'])
  selected=w['disclosure']('20250101000001');other=w['disclosure']('20250101000002')
  w['index_metadata']([w['MetadataDocument']('meta:'+selected.rcept_no,selected.to_index_text(),selected.to_dict())],metadata,enc,model=w['LOCAL_MODEL'])
  source=BigSource(other);svc=cls(settings,metadata,docs,enc,w['CollectionLedger'](root/'ledger.sqlite3'),source,w['LLM']())
  for phase in ['first','repeat']:
   before=enc.calls;t=time.perf_counter();svc.ask(selected,'위험은?');elapsed=time.perf_counter()-t
   print(label,phase,'seconds',round(elapsed,4),'chunks',docs.count(),'embedding_texts',enc.calls-before,'downloads',len(source.collect_calls),flush=True)
