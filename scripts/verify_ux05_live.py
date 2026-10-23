
"""Real OpenDART/BGE-M3/GMS verification with per-analysis timings.

Runs in an isolated temporary directory.
The operational ChromaDB and SQLite data are not modified.
Sensitive credentials, prompts, and provider responses are not logged.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dart_insight.config import load_settings
from dart_insight.data.models import COMPANIES
from dart_insight.opendart.client import DartClient
from dart_insight.rag.prompts.answer import ANALYSIS_QUESTION
from dart_insight.services.runtime import Runtime
from dart_insight.services.original_documents import OpenDartSource
from dart_insight.vectorstore.documents import index_documents
from dart_insight.services.disclosure_search import (
    DisclosureSearchService,
    SearchQuery,
)


def worker_main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stock",
        choices=[c.stock_code for c in COMPANIES],
        default="005930",
    )
    parser.add_argument("--receipt")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker-directory", type=Path, required=True)
    args = parser.parse_args()

    settings = load_settings()

    report = {
        "mode": "real-opendart-bge-gms",
        "status": "blocked",
        "runs": [],
        "credential_presence": {
            "DART_API_KEY": bool(settings.dart_api_key),
            "GMS_API_KEY_or_alias": bool(settings.openai_api_key),
        },
        "embedding_model": settings.embedding_model,
        "embedding_device": settings.embedding_device,
    }

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if not all(report["credential_presence"].values()):
        report["blocker"] = "missing_credentials"
        save()
        return 2

    if settings.embedding_model != "BAAI/bge-m3":
        report["blocker"] = "unexpected_embedding_model"
        save()
        return 2

    started = monotonic()
    report["error_stage"] = "metadata_search"

    try:
        company = next(
            c for c in COMPANIES
            if c.stock_code == args.stock
        )

        today = datetime.now(
            ZoneInfo("Asia/Seoul")
        ).date()

        with DartClient(settings) as client:
            corp = client.resolve_company(company)

        page = DisclosureSearchService(settings).search(
            SearchQuery(
                corp["corp_code"],
                today - timedelta(days=6),
                today,
            )
        )

        if page.state == "error":
            report["blocker"] = "opendart_metadata_search_failed"
            save()
            return 2

        ordered = sorted(
            page.items,
            key=lambda d: (d.rcept_dt, d.rcept_no),
            reverse=True,
        )

        selected = next(
            (
                d for d in ordered
                if not args.receipt
                or d.rcept_no == args.receipt
            ),
            None,
        )

        if selected is None:
            report["blocker"] = "no_matching_recent_disclosure"
            save()
            return 2

        report["receipt"] = selected.rcept_no
        report["error_stage"] = "analysis"

        isolated = replace(
            settings,
            data_dir=args.worker_directory,
            db_dir=args.worker_directory / "chroma",
            document_bundle=None,
            embedding_cache_dir=(
                settings.embedding_cache_dir
                or settings.data_dir / "models"
            ),
        )

        with (
            patch.object(
                OpenDartSource,
                "collect",
                autospec=True,
                side_effect=OpenDartSource.collect,
            ) as collected,
            patch(
                "dart_insight.services.rag_service.index_documents",
                wraps=index_documents,
            ) as indexed,
        ):
            with Runtime(isolated, None, None) as runtime:

                # 1. 최초 분석 시간
                first_started = monotonic()

                first = runtime.ask(
                    selected,
                    ANALYSIS_QUESTION,
                )

                first_seconds = round(
                    monotonic() - first_started,
                    3,
                )

                own = runtime.service.documents.get(
                    where={
                        "$and": [
                            {
                                "item_rcept_no":
                                selected.rcept_no
                            },
                            {
                                "document_rcept_no":
                                selected.rcept_no
                            },
                        ]
                    },
                    include=[],
                )

                if (
                    not own["ids"]
                    or not any(
                        c["source_type"] == "opendart_document"
                        for c in first["citations"]
                    )
                ):
                    raise AssertionError(
                        "missing_original_evidence"
                    )

                counts = (
                    collected.call_count,
                    indexed.call_count,
                    runtime.service.documents.count(),
                )

                report["runs"].append({
                    "kind": "initially_unindexed",
                    "status": "passed",
                    "seconds": first_seconds,
                    "original_chunks": len(own["ids"]),
                    "citations": len(first["citations"]),
                    "collect_calls": counts[0],
                    "index_calls": counts[1],
                })

                # 2. 기존 색인 재사용 분석 시간
                second_started = monotonic()

                second = runtime.ask(
                    selected,
                    ANALYSIS_QUESTION,
                )

                second_seconds = round(
                    monotonic() - second_started,
                    3,
                )

                after = (
                    collected.call_count,
                    indexed.call_count,
                    runtime.service.documents.count(),
                )

                if (
                    after != counts
                    or not second["audit"].get(
                        "reused_original_index"
                    )
                ):
                    raise AssertionError(
                        "indexed_run_recollected_or_reindexed"
                    )

                report["runs"].append({
                    "kind": "already_indexed",
                    "status": "passed",
                    "seconds": second_seconds,
                    "no_recollection_or_reindex": True,
                    "citations": len(second["citations"]),
                })

        report["error_stage"] = "runtime_close"

        # 3. 개별 시간 및 전체 시간 기록
        report["timings"] = {
            "first_analysis_seconds": first_seconds,
            "repeat_analysis_seconds": second_seconds,
        }

        report.pop("error_stage", None)
        report["status"] = "verified"
        report["seconds"] = round(
            monotonic() - started,
            3,
        )

    except Exception as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        report["error_category"] = getattr(
            exc,
            "category",
            "processing",
        )

        frame = exc.__traceback__
        if frame is not None:
            while frame.tb_next is not None:
                frame = frame.tb_next

            report["error_location"] = {
                "file": Path(
                    frame.tb_frame.f_code.co_filename
                ).name,
                "function": frame.tb_frame.f_code.co_name,
                "line": frame.tb_lineno,
            }

    save()
    return 0 if report["status"] == "verified" else 1


def run_isolated(arguments, output):
    """Run Chroma in a child process for Windows-safe cleanup."""

    report = {
        "status": "failed",
        "runs": [],
    }

    directory = None
    stage = "temporary_directory_create"

    try:
        directory = Path(
            tempfile.mkdtemp(
                prefix="dart-ux05-live-"
            )
        )

        child_report = directory / "result.json"
        stage = "worker_exit"

        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                *arguments,
                "--output",
                str(child_report),
                "--worker-directory",
                str(directory),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

        stage = "worker_report"

        report = json.loads(
            child_report.read_text(encoding="utf-8")
        )

        if result.returncode == 0:
            expected = [
                "initially_unindexed",
                "already_indexed",
            ]

            if (
                report.get("status") != "verified"
                or [
                    r.get("kind")
                    for r in report.get("runs", [])
                ] != expected
                or any(
                    r.get("status") != "passed"
                    for r in report["runs"]
                )
                or not all(
                    isinstance(
                        report.get("timings", {}).get(key),
                        (int, float),
                    )
                    for key in (
                        "first_analysis_seconds",
                        "repeat_analysis_seconds",
                    )
                )
            ):
                raise ValueError(
                    "incomplete_verification"
                )

        elif not (
            result.returncode == 2
            and report.get("status") == "blocked"
        ):
            if report.get("status") != "failed":
                report["status"] = "failed"
                report["error_type"] = "ChildProcessError"
                report["error_stage"] = "worker_exit"

    except Exception as exc:
        report.update(
            status="failed",
            error_type=type(exc).__name__,
            error_stage=stage,
        )

    finally:
        if directory is not None:
            try:
                shutil.rmtree(directory)
            except Exception as exc:
                report.update(
                    status="failed",
                    error_type=type(exc).__name__,
                    error_stage="temporary_directory_cleanup",
                )

    if report.get("status") == "verified":
        report["status"] = "passed"

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "status": report["status"],
                "report_path": str(output),
                "timings": report.get("timings"),
            },
            ensure_ascii=False,
        )
    )

    return {
        "passed": 0,
        "blocked": 2,
    }.get(report["status"], 1)


def main():
    if "--worker-directory" in sys.argv:
        return worker_main()

    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--stock",
        choices=[c.stock_code for c in COMPANIES],
        default="005930",
    )
    parser.add_argument("--receipt")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    args = parser.parse_args()

    arguments = [
        "--stock",
        args.stock,
    ]

    if args.receipt:
        arguments.extend([
            "--receipt",
            args.receipt,
        ])

    print(
        "Live validation: real OpenDART/BGE-M3/GMS; "
        "isolated Windows-safe worker."
    )

    return run_isolated(
        arguments,
        args.output,
    )


if __name__ == "__main__":
    raise SystemExit(main())
