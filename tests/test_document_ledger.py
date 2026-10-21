"""F2004 models and real temporary SQLite persistence; no external services."""

from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from dart_insight.data.documents import DocumentCandidate, CollectedDocument
from dart_insight.data.collection_ledger import CollectionLedger, normalize_query

ITEM = "20250101000001"
OTHER = "20250102000002"


def document(item=ITEM, receipt=OTHER):
    return CollectedDocument(item_rcept_no=item, document_rcept_no=receipt,
                             title="사업보고서", corp_code="00126380",
                             source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
                             text="사업 위험 및 자금 조달 조건", collected_at="2026-10-21T10:00:00+09:00")


class DocumentTests(unittest.TestCase):
    def test_candidate_and_collected_roundtrip_preserve_item_and_original(self):
        collected = document()
        self.assertIsInstance(collected, DocumentCandidate)
        self.assertEqual(CollectedDocument(**collected.to_dict()), collected)
        self.assertNotEqual(collected.item_rcept_no, collected.document_rcept_no)
        with self.assertRaises(FrozenInstanceError):
            collected.title = "changed"

    def test_invalid_metadata_rejected(self):
        for changes in ({"item_rcept_no": "bad"}, {"document_rcept_no": "bad"},
                        {"corp_code": "1"}, {"title": " "}, {"source_type": "unknown"},
                        {"source_url": "https://opendart.fss.or.kr/api/document.xml?crtfc_key=secret"},
                        {"source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={ITEM}"},
                        {"text": " "}, {"collected_at": "bad"},
                        {"collected_at": "2026-10-21T10:00:00"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(document(), **changes)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "data" / "collection.sqlite3"
        self.ledger = CollectionLedger(self.path)

    def test_normalization_and_persistent_unique_query(self):
        self.assertEqual(normalize_query("  ＡＢＣ\n 사업   위험 "), "abc 사업 위험")
        self.ledger.ensure_pending(ITEM, "  ＡＢＣ\n 사업 위험")
        self.ledger.ensure_pending(ITEM, "abc 사업 위험")
        reopened = CollectionLedger(self.path)
        row = reopened.get(ITEM, "ABC 사업 위험")
        self.assertEqual(row.state, "pending")
        self.assertEqual(row.query, "abc 사업 위험")
        self.assertEqual(row.documents, ())

    def test_complete_lifecycle_and_preserved_source_after_reopen(self):
        self.assertIsNone(self.ledger.get(ITEM, "위험"))
        self.ledger.ensure_pending(ITEM, "위험")
        self.ledger.mark_collected(ITEM, "위험", [document()])
        row = CollectionLedger(self.path).get(ITEM, "위험")
        self.assertEqual(row.state, "collected")
        self.assertEqual(row.documents, (document(),))
        self.assertEqual(row.source, "opendart_document")
        self.ledger.mark_indexed(ITEM, "위험")
        self.assertEqual(CollectionLedger(self.path).get(ITEM, "위험").state, "indexed")

    def test_failure_keeps_pending_and_retry_succeeds(self):
        self.ledger.ensure_pending(ITEM, "위험")
        # A failed collector does not call mark_collected; a partial/empty batch is rejected.
        with self.assertRaises(ValueError):
            self.ledger.mark_collected(ITEM, "위험", [])
        retry = CollectionLedger(self.path)
        self.assertEqual(retry.get(ITEM, "위험").state, "pending")
        retry.ensure_pending(ITEM, "위험")
        retry.mark_collected(ITEM, "위험", [document()])
        self.assertEqual(retry.get(ITEM, "위험").state, "collected")

    def test_pending_cannot_be_indexed_and_failed_index_keeps_collected(self):
        with self.assertRaises(ValueError):
            self.ledger.mark_indexed(ITEM, "missing")
        self.ledger.ensure_pending(ITEM, "위험")
        with self.assertRaises(ValueError):
            self.ledger.mark_indexed(ITEM, "위험")
        self.ledger.mark_collected(ITEM, "위험", [document()])
        # An external indexing failure makes no completion call.
        self.assertEqual(CollectionLedger(self.path).get(ITEM, "위험").state, "collected")

    def test_idempotent_calls_never_downgrade_or_overwrite(self):
        self.ledger.ensure_pending(ITEM, "위험")
        self.ledger.mark_collected(ITEM, "위험", [document()])
        self.ledger.mark_indexed(ITEM, "위험")
        self.ledger.ensure_pending(ITEM, "위험")
        self.ledger.mark_collected(ITEM, "위험", [document()])
        self.ledger.mark_indexed(ITEM, "위험")
        with self.assertRaises(ValueError):
            self.ledger.mark_collected(ITEM, "위험", [replace(document(), text="변경 본문")])
        row = self.ledger.get(ITEM, "위험")
        self.assertEqual(row.state, "indexed")
        self.assertEqual(row.documents, (document(),))

    def test_isolation_between_items_and_queries(self):
        for item, query in ((ITEM, "위험"), (OTHER, "위험"), (ITEM, "조건")):
            self.ledger.ensure_pending(item, query)
        self.ledger.mark_collected(ITEM, "위험", [document()])
        self.assertEqual(self.ledger.get(OTHER, "위험").state, "pending")
        self.assertEqual(self.ledger.get(ITEM, "조건").state, "pending")

    def test_invalid_batch_does_not_advance_or_partially_save(self):
        self.ledger.ensure_pending(ITEM, "위험")
        for batch in ([document(), document()], [document(), document(item=OTHER)], [object()]):
            with self.subTest(batch=batch), self.assertRaises(ValueError):
                self.ledger.mark_collected(ITEM, "위험", batch)
        self.assertEqual(self.ledger.get(ITEM, "위험").documents, ())
        self.assertEqual(self.ledger.get(ITEM, "위험").state, "pending")
        with self.assertRaises(ValueError):
            self.ledger.mark_collected(ITEM, "unregistered", [document()])

    def test_multiple_documents_persist_as_one_complete_batch(self):
        self.ledger.ensure_pending(ITEM, "위험")
        self.ledger.mark_collected(ITEM, "위험", [document(), document(receipt=ITEM)])
        self.assertEqual(len(CollectionLedger(self.path).get(ITEM, "위험").documents), 2)

    def test_database_write_failure_rolls_back_and_can_retry(self):
        self.ledger.ensure_pending(ITEM, "위험")
        # Connection.__exit__ commits/rolls back but does not close the handle.
        with closing(sqlite3.connect(self.path)) as connection, connection, closing(connection.cursor()) as cursor:
            cursor.execute("""CREATE TRIGGER fail_write BEFORE UPDATE ON collection_queries
                BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END""")
        with self.assertRaises(sqlite3.DatabaseError):
            self.ledger.mark_collected(ITEM, "위험", [document()])
        row = CollectionLedger(self.path).get(ITEM, "위험")
        self.assertEqual(row.state, "pending")
        self.assertEqual(row.documents, ())
        with closing(sqlite3.connect(self.path)) as connection, connection, closing(connection.cursor()) as cursor:
            cursor.execute("DROP TRIGGER fail_write")
        self.ledger.mark_collected(ITEM, "위험", [document()])
        self.assertEqual(self.ledger.get(ITEM, "위험").state, "collected")
        # Deletion must succeed immediately, before temporary-directory cleanup.
        self.path.unlink()

    def test_retained_traceback_does_not_keep_database_handles_open(self):
        connections, cursors = [], []
        connect = sqlite3.connect

        class TrackingConnection(sqlite3.Connection):
            def cursor(self, *args, **kwargs):
                cursor = super().cursor(*args, **kwargs)
                cursors.append(cursor)
                return cursor

        def tracked_connect(*args, **kwargs):
            connection = connect(*args, **kwargs, factory=TrackingConnection)
            connections.append(connection)
            return connection

        with patch("dart_insight.data.collection_ledger.sqlite3.connect", side_effect=tracked_connect):
            self.ledger.ensure_pending(ITEM, "위험")
            self.ledger.get(ITEM, "위험")
            try:
                with self.ledger._connect() as cursor:
                    cursor.execute("BEGIN IMMEDIATE")
                    cursor.execute("INSERT INTO missing_table VALUES (1)")
            except sqlite3.DatabaseError as error:
                retained_traceback = error.__traceback__
            else:
                self.fail("Expected database failure")

        # Keep connections, cursors and traceback alive: no gc.collect() workaround.
        self.assertIsNotNone(retained_traceback)
        self.assertTrue(cursors)
        for cursor in cursors:
            with self.assertRaises(sqlite3.ProgrammingError):
                cursor.execute("SELECT 1")
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
        self.path.unlink()

    def test_invalid_keys_rejected(self):
        for query in ("", "  ", None):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.ledger.ensure_pending(ITEM, query)
        with self.assertRaises(ValueError):
            self.ledger.ensure_pending("bad", "위험")
        with self.assertRaises(ValueError):
            self.ledger.ensure_pending(ITEM, "위험", source="unknown")
