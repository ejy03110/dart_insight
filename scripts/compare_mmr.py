"""Compare existing BGE/Chroma Top 5 on a disposable copy, without GMS or reindexing."""
import argparse
from datetime import datetime, timezone
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dart_insight.config import load_settings
from dart_insight.rag.mmr import compare_search
from dart_insight.vectorstore.embeddings import create_embeddings


def worker(args):
    import chromadb
    from chromadb.config import Settings
    settings = replace(load_settings(),embedding_cache_dir=args.model_cache)
    client = chromadb.PersistentClient(path=str(args.snapshot),settings=Settings(anonymized_telemetry=False))
    collection = client.get_collection(args.collection,embedding_function=None)
    result = compare_search(collection,create_embeddings(settings),args.query,fetch_k=args.fetch_k,
                            lambda_mult=args.lambda_mult,
                            where={'corp_code':args.corp_code} if args.corp_code else None)
    if len(result['similarity']) != 5 or len(result['mmr']) != 5:
        raise ValueError('at_least_five_matching_chunks_required')
    args.output.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    return 0


def cell(value):
    return str(value).replace('|','\\|').replace('\n',' ').replace('\r',' ')


def markdown(args, result):
    lines = ['# F2014 유사도/MMR 실제 색인 비교', '',
             f'- 실행 시각(UTC): {datetime.now(timezone.utc).isoformat()}',
             '- 임베딩: 기존 BAAI/bge-m3, dense 1024, 정규화. GMS/재색인 없음.',
             f'- 동일 질문: {cell(args.query)}',
             f'- 컬렉션: {args.collection}; 기업 필터: {cell(args.corp_code or "전체")}',
             f'- Top 5; fetch_k={args.fetch_k}; 실제 후보={result["candidate_count"]}; lambda={args.lambda_mult}',
             '- 운영 DB를 읽어 만든 임시 복사본만 열었으며, 두 방식은 동일한 후보/질문 벡터를 사용했다.', '']
    for method in ('similarity','mmr'):
        lines += [f'## {method}', '', '| 순위 | 청크 ID | 공시 제목 | 문서 접수번호 | 질문 cosine |',
                  '|---|---|---|---|---|']
        for rank,row in enumerate(result[method],1):
            meta = row['metadata']
            lines.append(f'| {rank} | {cell(row["id"])} | {cell(meta.get("title",meta.get("report_nm","")))} | '
                         f'{cell(meta.get("document_rcept_no",meta.get("rcept_no","")))} | {row["query_cosine"]:.6f} |')
        metrics = result[method+'_metrics']
        lines += ['',f'- 고유 문서 수: {metrics["unique_documents"]}; 평균 쌍별 cosine: {metrics["mean_pairwise_cosine"]:.6f}', '']
    overlap = len({r['id'] for r in result['similarity']} & {r['id'] for r in result['mmr']})
    lines += [f'- Top 5 청크 교집합: {overlap}/5.',
              '- 쌍별 cosine이 낮을수록 후보 벡터의 중복 유사도가 낮다. 고유 문서 수와 함께 해석한다.',
              '- MMR의 관련성/다양성 절충은 lambda에 의존한다. 다양성이나 답변 정확성 개선을 보장하지 않는다.',
              '- 후보 밖 문서는 평가하지 않는다. 실제 운영 RAG의 메타 고정/공시 범위/근거 검증은 변경하지 않았다.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query',required=True)
    parser.add_argument('--db-dir',type=Path)
    parser.add_argument('--collection',choices=['dart_documents','dart_metadata'],default='dart_documents')
    parser.add_argument('--fetch-k',type=int,default=20)
    parser.add_argument('--lambda-mult',type=float,default=0.5)
    parser.add_argument('--corp-code')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--snapshot',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--model-cache',type=Path,help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.snapshot:
            return worker(args)
        settings = load_settings()
        source = (args.db_dir or settings.chroma_dir).resolve()
        if not (source/'chroma.sqlite3').is_file():
            print(json.dumps({'status':'blocked','reason':'existing_index_missing'})); return 2
        if args.output.resolve().is_relative_to(source):
            raise ValueError('output_must_be_outside_operational_index')
        cache = (settings.embedding_cache_dir or settings.data_dir/'models').resolve()
        if args.output.resolve().is_relative_to(cache):
            raise ValueError('output_must_be_outside_model_cache')
        if not cache.is_dir():
            print(json.dumps({'status':'blocked','reason':'existing_model_cache_missing'})); return 2
        print('Existing BGE-M3; one query; Top 5; no GMS. Stop index writers before taking the copy.')
        # The child owns Chroma handles; OS exit precedes Windows directory removal.
        with tempfile.TemporaryDirectory(prefix='dart-mmr-') as directory:
            snapshot = Path(directory)/'index'
            shutil.copytree(source,snapshot)
            model_cache = Path(directory)/'models'
            shutil.copytree(cache,model_cache)
            result_file = Path(directory)/'result.json'
            command = [sys.executable,str(Path(__file__).resolve()),'--snapshot',str(snapshot),
                       '--model-cache',str(model_cache),'--query',args.query,'--collection',args.collection,'--fetch-k',str(args.fetch_k),
                       '--lambda-mult',str(args.lambda_mult),'--output',str(result_file)]
            if args.corp_code:
                command += ['--corp-code',args.corp_code]
            env = {**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
            completed = subprocess.run(command,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if completed.returncode:
                failure = json.loads(result_file.read_text(encoding='utf-8')) if result_file.is_file() else {}
                print(json.dumps({'status':'failed','reason':'snapshot_search_failed',
                                  'error_type':failure.get('error_type'),'exit_code':completed.returncode})); return 1
            result = json.loads(result_file.read_text(encoding='utf-8'))
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(markdown(args,result),encoding='utf-8')
        print(json.dumps({'status':'passed','output':str(args.output)},ensure_ascii=False))
        return 0
    except Exception as exc:
        failure = {'status':'failed','error_type':type(exc).__name__}
        if args.snapshot:
            args.output.write_text(json.dumps(failure),encoding='utf-8')
        print(json.dumps(failure))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
