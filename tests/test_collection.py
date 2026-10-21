"""F2001 synthetic fixtures: no network and no production-data substitution."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from dart_insight.collect import main
from dart_insight.config import ConfigurationError, Settings
from dart_insight.data.collection import collect_metadata, save_collection
from dart_insight.data.models import COMPANIES, Disclosure
from dart_insight.opendart.client import DartClient, DartError

BEGIN, END = date(2025, 1, 1), date(2025, 12, 31)
CORPS = {company.stock_code: {"corp_code": f"{10000001 + index:08d}",
                             "corp_name": company.name, "stock_code": company.stock_code}
         for index, company in enumerate(COMPANIES)}


def records(corp_code, count, at=BEGIN):
    corp = next(c for c in CORPS.values() if c["corp_code"] == corp_code)
    return [Disclosure.from_api(
        {**corp, "report_nm": f"단위 테스트 공시 {index}",
         "rcept_no": f"{at.year:04d}{int(corp_code) - 10000000:02d}{index:08d}",
         "rcept_dt": at.strftime("%Y%m%d")}, "정기공시", "2026-01-01T00:00:00+00:00")
        for index in range(count)]


def fake_client():
    client = Mock(spec=DartClient)
    client.resolve_company.side_effect = lambda company, **_: CORPS[company.stock_code]
    client.list_disclosures.side_effect = lambda code, begin, end, **_: records(
        code, 34 if code == "10000001" else 33, begin)
    return client


class CollectionTests(unittest.TestCase):
    def test_three_official_resolutions_all_types_dedup_and_text(self):
        client = fake_client()
        result = collect_metadata(client, BEGIN, END, disclosure_types=["A", "I", "A"], refresh=True)
        summary = result["summary"]
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["total_unique_count"], 100)
        self.assertEqual(summary["company_counts"], {"삼성전자": 34, "현대자동차": 33, "금양": 33})
        self.assertEqual(len(summary["queries"]), 6)
        self.assertEqual(len({d["rcept_no"] for d in result["disclosures"]}), 100)
        for index, call in enumerate(client.resolve_company.call_args_list):
            self.assertEqual(call.args[0], COMPANIES[index])
            self.assertEqual(call.kwargs["refresh"], index == 0)
        for call in client.list_disclosures.call_args_list:
            self.assertIsNone(call.kwargs["max_pages"])
            self.assertTrue(call.kwargs["refresh"])
        for doc in result["disclosures"]:
            self.assertIn("정기공시", doc["disclosure_type"])
            self.assertIn("거래소공시", doc["disclosure_type"])
            for field in ("corp_name", "report_nm", "rcept_no", "rcept_dt", "disclosure_type", "source_url"):
                self.assertIn(doc[field], doc["index_text"])
        self.assertEqual(summary["sample_index_text"], result["disclosures"][0]["index_text"])

    def test_expand_until_100_without_requerying_completed_window(self):
        client = fake_client()
        client.list_disclosures.side_effect = lambda code, begin, end, **_: records(
            code, 5 if begin.year == 2025 else 30, begin)
        result = collect_metadata(client, BEGIN, END, disclosure_types=["A"])
        self.assertEqual(result["summary"]["total_unique_count"], 105)
        self.assertEqual(result["summary"]["queried_windows"], [
            {"begin": "2025-01-01", "end": "2025-12-31"},
            {"begin": "2024-01-01", "end": "2024-12-31"}])
        self.assertEqual(result["summary"]["queried_period"]["begin"], "2024-01-01")
        self.assertEqual(client.list_disclosures.call_count, 6)

    def test_dont_stop_at_100_before_finishing_every_company_and_type(self):
        client = fake_client()
        client.list_disclosures.side_effect = lambda code, begin, end, **_: records(code, 100, begin)
        result = collect_metadata(client, BEGIN, END, disclosure_types=["A", "I"])
        self.assertEqual(result["summary"]["total_unique_count"], 300)
        self.assertEqual(client.list_disclosures.call_count, 6)

    def test_expand_when_one_company_is_empty_even_if_total_exceeds_100(self):
        client = fake_client()
        client.list_disclosures.side_effect = lambda code, begin, end, **_: records(
            code, (100 if code == "10000001" else 0) if begin.year == 2025 else 1, begin)
        result = collect_metadata(client, BEGIN, END, disclosure_types=["A"])
        self.assertEqual(result["summary"]["status"], "complete")
        self.assertEqual(result["summary"]["total_unique_count"], 103)
        self.assertEqual(client.list_disclosures.call_count, 6)

    def test_empty_results_are_bounded_and_never_fabricated(self):
        client = fake_client()
        client.list_disclosures.return_value = []
        client.list_disclosures.side_effect = None
        result = collect_metadata(client, BEGIN, END, earliest=date(2024, 7, 1), disclosure_types=["A"])
        self.assertEqual(result["disclosures"], [])
        self.assertEqual(result["summary"]["status"], "insufficient_data")
        self.assertIsNone(result["summary"]["sample_index_text"])
        self.assertEqual(result["summary"]["queried_period"]["begin"], "2024-07-01")
        self.assertEqual(client.list_disclosures.call_count, 6)

    def test_nonempty_but_less_than_100_is_insufficient(self):
        client = fake_client()
        client.list_disclosures.side_effect = lambda code, begin, end, **_: records(code, 1, begin)
        result = collect_metadata(client, BEGIN, END, earliest=BEGIN, disclosure_types=["A"])
        self.assertEqual(result["summary"]["status"], "insufficient_data")
        self.assertEqual(result["summary"]["total_unique_count"], 3)

    def test_leap_day_expansion_has_no_gap_or_overlap(self):
        client = fake_client()
        client.list_disclosures.side_effect = lambda code, begin, end, **_: (
            [] if begin.year == 2024 else records(code, 34, begin))
        result = collect_metadata(client, date(2024, 2, 29), date(2024, 3, 1), disclosure_types=["A"])
        self.assertEqual(result["summary"]["queried_windows"][1],
                         {"begin": "2023-02-28", "end": "2024-02-28"})

    def test_invalid_arguments_fail_before_any_api_call(self):
        for kwargs in ({"minimum_count": 99}, {"earliest": date(2026, 1, 1)},
                       {"disclosure_types": []}, {"disclosure_types": ["X"]}):
            client = fake_client()
            with self.subTest(kwargs=kwargs), self.assertRaises(ConfigurationError):
                collect_metadata(client, BEGIN, END, **kwargs)
            client.resolve_company.assert_not_called()
        with self.assertRaises(ConfigurationError):
            collect_metadata(fake_client(), END, BEGIN)

    def test_auth_quota_or_transport_failure_aborts(self):
        for code in ("010", "020", "network"):
            client = fake_client()
            client.list_disclosures.side_effect = [records("10000001", 100), DartError("호출 실패", code)]
            with self.subTest(code=code), self.assertRaises(DartError):
                collect_metadata(client, BEGIN, END, disclosure_types=["A"])
            self.assertEqual(client.list_disclosures.call_count, 2)

    def test_missing_company_aborts_without_list_queries(self):
        client = fake_client()
        client.resolve_company.side_effect = DartError("기업코드 누락", "company")
        with self.assertRaises(DartError):
            collect_metadata(client, BEGIN, END)
        client.list_disclosures.assert_not_called()

    def test_response_outside_requested_period_is_rejected(self):
        client = fake_client()
        client.list_disclosures.return_value = records("10000001", 1, date(2020, 1, 1))
        client.list_disclosures.side_effect = None
        with self.assertRaises(DartError):
            collect_metadata(client, BEGIN, END)

    def test_atomic_snapshots_keep_previous_run_and_roundtrip_text(self):
        result = collect_metadata(fake_client(), BEGIN, END, disclosure_types=["A"])
        with tempfile.TemporaryDirectory() as directory:
            first = save_collection(result, Path(directory))
            original = first.read_bytes()
            second = save_collection(result, Path(directory))
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), original)
            self.assertEqual(json.loads(second.read_text()), result)
            self.assertEqual(list(Path(directory).rglob("*.tmp")), [])


class CollectionIntegrationTests(unittest.TestCase):
    def test_real_client_pagination_corporate_lookup_and_cache_without_network(self):
        session = Mock()
        buffer = BytesIO()
        xml = "<result>" + "".join("<list>" + "".join(f"<{k}>{v}</{k}>" for k, v in corp.items())
                                    + "</list>" for corp in CORPS.values()) + "</result>"
        with ZipFile(buffer, "w") as archive:
            archive.writestr("CORPCODE.xml", xml)

        def get(url, **kwargs):
            response = Mock(status_code=200)
            if url.endswith("corpCode.xml"):
                response.content = buffer.getvalue()
            else:
                params = kwargs["params"]
                docs = records(params["corp_code"], 101 if params["corp_code"] == "10000001" else 1)
                number = params["page_no"]
                rows = [{**doc.to_dict(), "rcept_dt": doc.rcept_dt.replace("-", "")}
                        for doc in docs[(number - 1) * 100:number * 100]]
                response.json.return_value = {"status": "000", "total_page": 2 if len(docs) > 100 else 1,
                                              "list": rows}
            return response

        session.get.side_effect = get
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(dart_api_key="unit-test-secret", data_dir=Path(directory), request_interval_seconds=0)
            with DartClient(settings, session=session) as client:
                result = collect_metadata(client, BEGIN, END, disclosure_types=["A"])
                self.assertEqual(result["summary"]["total_unique_count"], 103)
                self.assertEqual(session.get.call_count, 5)  # one ZIP + four list pages
                cached = collect_metadata(client, BEGIN, END, disclosure_types=["A"])
                self.assertEqual(session.get.call_count, 5)
                self.assertEqual(cached["disclosures"], result["disclosures"])
                output = save_collection(cached, Path(directory) / "metadata")
                self.assertNotIn("unit-test-secret", output.read_text())
                for cache in (Path(directory) / "cache").rglob("*.json"):
                    self.assertNotIn("unit-test-secret", cache.read_text())


class CollectionCliTests(unittest.TestCase):
    def run_cli(self, client, settings, *args):
        stdout, stderr = StringIO(), StringIO()
        with patch("dart_insight.collect.load_settings", return_value=settings), \
             patch("dart_insight.collect.DartClient") as factory, \
             redirect_stdout(stdout), redirect_stderr(stderr):
            factory.return_value.__enter__.return_value = client
            status = main(["--begin", "2025-01-01", "--end", "2025-12-31", "--types", "A", *args])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_cli_success_outputs_counts_period_example_and_saved_path(self):
        with tempfile.TemporaryDirectory() as directory:
            status, stdout, _ = self.run_cli(fake_client(), Settings(data_dir=Path(directory)))
            self.assertEqual(status, 0)
            summary = json.loads(stdout)
            self.assertEqual(summary["status"], "complete")
            self.assertEqual(sum(summary["company_counts"].values()), 100)
            self.assertEqual(summary["queried_period"], {"begin": "2025-01-01", "end": "2025-12-31"})
            self.assertIn("공시명:", summary["sample_index_text"])
            self.assertTrue(Path(summary["output_path"]).is_file())

    def test_cli_insufficient_saves_explicit_status_and_returns_2(self):
        client = fake_client()
        client.list_disclosures.side_effect = None
        client.list_disclosures.return_value = []
        with tempfile.TemporaryDirectory() as directory:
            status, stdout, stderr = self.run_cli(client, Settings(data_dir=Path(directory)),
                                                  "--earliest", "2025-01-01")
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(stdout)["status"], "insufficient_data")
            self.assertIn("미완료", stderr)

    def test_cli_api_failure_does_not_save_success_or_overwrite_old_file(self):
        client = fake_client()
        client.list_disclosures.side_effect = DartError("요청 한도 초과", "020")
        with tempfile.TemporaryDirectory() as directory:
            previous = Path(directory) / "previous.json"
            previous.write_text("previous evidence")
            status, stdout, stderr = self.run_cli(client, Settings(data_dir=Path(directory)))
            self.assertEqual(status, 1)
            self.assertEqual(stdout, "")
            self.assertIn("저장하지 않았습니다", stderr)
            self.assertEqual(previous.read_text(), "previous evidence")
            self.assertFalse((Path(directory) / "metadata").exists())

    def test_missing_key_blocks_before_collection(self):
        with patch("dart_insight.collect.load_settings", return_value=Settings()), \
             patch("dart_insight.collect.collect_metadata") as collect, \
             redirect_stdout(StringIO()), redirect_stderr(StringIO()) as stderr:
            self.assertEqual(main(["--begin", "2025-01-01", "--end", "2025-12-31"]), 1)
        collect.assert_not_called()
        self.assertIn("DART_API_KEY", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
