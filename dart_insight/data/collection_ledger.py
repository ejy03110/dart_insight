"""Persistent F2004/F2005 state, keyed by selected item, query and source.

pending -> collected -> indexed. A failed download leaves pending; a failed
index leaves collected. Only explicit successful transitions persist progress.
This is a completion ledger, not a lock/lease for concurrent collection workers.
"""

from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Literal
import unicodedata

from dart_insight.data.documents import CollectedDocument, validate_receipt


def normalize_query(query: str) -> str:
    if not isinstance(query, str):
        raise ValueError("검색어 문자열이 필요합니다.")
    normalized = " ".join(unicodedata.normalize("NFKC", query).split()).casefold()
    if not normalized:
        raise ValueError("비어 있지 않은 검색어가 필요합니다.")
    return normalized


@dataclass(frozen=True)
class CollectionRecord:
    item_rcept_no: str
    query: str
    source: str
    state: Literal["pending", "collected", "indexed"]
    documents: tuple[CollectedDocument, ...]
    updated_at: str


class CollectionLedger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as cursor:
            cursor.execute("""CREATE TABLE IF NOT EXISTS collection_queries (
                item_rcept_no TEXT NOT NULL, query TEXT NOT NULL, source TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending', 'collected', 'indexed')),
                documents TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL,
                PRIMARY KEY (item_rcept_no, query, source)
            )""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS collection_plans (
                item_rcept_no TEXT NOT NULL, query TEXT NOT NULL, plan TEXT NOT NULL,
                PRIMARY KEY (item_rcept_no, query))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS question_queries (
                item_rcept_no TEXT NOT NULL, question TEXT NOT NULL, queries TEXT NOT NULL,
                PRIMARY KEY (item_rcept_no, question))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS collected_originals (
                item_rcept_no TEXT NOT NULL, document_rcept_no TEXT NOT NULL, document TEXT NOT NULL,
                PRIMARY KEY (item_rcept_no, document_rcept_no))""")

    def save_document(self, doc):
        if not isinstance(doc, CollectedDocument):
            raise ValueError("수집한 실제 원문이 필요합니다.")
        with self._connect() as cursor:
            cursor.execute("INSERT OR IGNORE INTO collected_originals VALUES (?, ?, ?)",
                           (doc.item_rcept_no, doc.document_rcept_no,
                            json.dumps(doc.to_dict(), ensure_ascii=False)))

    def question_queries(self, item, question, queries=None):
        validate_receipt(item)
        key = (item, normalize_query(question))
        with self._connect() as cursor:
            if queries is not None:
                cursor.execute("INSERT OR REPLACE INTO question_queries VALUES (?, ?, ?)",
                               (*key, json.dumps(queries, ensure_ascii=False)))
            row = cursor.execute("SELECT queries FROM question_queries WHERE item_rcept_no=? AND question=?", key).fetchone()
        return json.loads(row[0]) if row else None

    def plan(self, item, query, value=None):
        """Persist search/selection before downloads so failed downloads resume the same plan."""
        validate_receipt(item)
        key = (item, normalize_query(query))
        with self._connect() as cursor:
            if value is not None:
                cursor.execute("INSERT OR REPLACE INTO collection_plans VALUES (?, ?, ?)",
                               (*key, json.dumps(value, ensure_ascii=False)))
            row = cursor.execute("SELECT plan FROM collection_plans WHERE item_rcept_no=? AND query=?", key).fetchone()
        return json.loads(row[0]) if row else None

    def documents_for_item(self, item):
        validate_receipt(item)
        with self._connect() as cursor:
            rows = cursor.execute("SELECT documents FROM collection_queries WHERE item_rcept_no=? AND state != 'pending'", (item,)).fetchall()
            originals = cursor.execute("SELECT document FROM collected_originals WHERE item_rcept_no=?", (item,)).fetchall()
        docs = {doc.document_rcept_no: doc for row in rows
                for doc in (CollectedDocument(**d) for d in json.loads(row[0]))}
        docs.update({doc.document_rcept_no: doc for doc in
                     (CollectedDocument(**json.loads(row[0])) for row in originals)})
        return docs

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=20)
        try:
            connection.row_factory = sqlite3.Row
            # Close statements before transaction exit/connection.close(), including
            # when a retained exception traceback still references this frame.
            with connection:
                with closing(connection.cursor()) as cursor:
                    yield cursor
        finally:
            connection.close()

    def _key(self, item_rcept_no, query, source):
        validate_receipt(item_rcept_no)
        # Only the currently supported original-document source is accepted.
        if source != "opendart_document":
            raise ValueError("지원되는 원문 출처가 필요합니다.")
        return item_rcept_no, normalize_query(query), source

    def get(self, item_rcept_no: str, query: str, source: str = "opendart_document") -> CollectionRecord | None:
        key = self._key(item_rcept_no, query, source)
        with self._connect() as cursor:
            row = cursor.execute("SELECT * FROM collection_queries WHERE item_rcept_no=? AND query=? AND source=?", key).fetchone()
        if row is None:
            return None
        return CollectionRecord(row["item_rcept_no"], row["query"], row["source"], row["state"],
                                tuple(CollectedDocument(**value) for value in json.loads(row["documents"])),
                                row["updated_at"])

    def ensure_pending(self, item_rcept_no: str, query: str, source: str = "opendart_document"):
        """Register a new retryable query; never downgrade an existing record."""
        key = self._key(item_rcept_no, query, source)
        with self._connect() as cursor:
            cursor.execute("""INSERT INTO collection_queries
                (item_rcept_no, query, source, state, updated_at) VALUES (?, ?, ?, 'pending', ?)
                ON CONFLICT(item_rcept_no, query, source) DO NOTHING""", (*key, self._now()))

    def mark_collected(self, item_rcept_no: str, query: str, documents: list[CollectedDocument],
                       source: str = "opendart_document"):
        """Atomically commit a complete batch; never call for partial/failed collection.

        One document per original receipt is stored here; XML subfiles/chunks are
        a later parsing/indexing concern. Empty results cannot imply completion.
        """
        key = self._key(item_rcept_no, query, source)
        if not documents or any(not isinstance(doc, CollectedDocument) or
                doc.item_rcept_no != item_rcept_no or doc.source_type != source for doc in documents):
            raise ValueError("선택 공시·출처에 일치하는 수집 문서가 필요합니다.")
        if len({doc.document_rcept_no for doc in documents}) != len(documents):
            raise ValueError("중복 원문 접수번호가 있습니다.")
        payload = json.dumps([doc.to_dict() for doc in sorted(documents, key=lambda doc: doc.document_rcept_no)],
                             ensure_ascii=False, sort_keys=True)
        with self._connect() as cursor:
            cursor.execute("BEGIN IMMEDIATE")
            row = cursor.execute("SELECT state, documents FROM collection_queries WHERE item_rcept_no=? AND query=? AND source=?", key).fetchone()
            if row is None:
                raise ValueError("먼저 검색어를 pending으로 등록하세요.")
            if row["state"] != "pending":
                if row["documents"] == payload:
                    return  # Idempotent; retain indexed status if already indexed.
                raise ValueError("완료된 수집 문서를 임의로 덮어쓸 수 없습니다.")
            cursor.execute("""UPDATE collection_queries SET state='collected', documents=?, updated_at=?
                WHERE item_rcept_no=? AND query=? AND source=?""", (payload, self._now(), *key))

    def mark_indexed(self, item_rcept_no: str, query: str, source: str = "opendart_document"):
        """F2005 must call only after successful indexing; no indexing occurs here."""
        key = self._key(item_rcept_no, query, source)
        with self._connect() as cursor:
            result = cursor.execute("""UPDATE collection_queries SET state='indexed', updated_at=?
                WHERE item_rcept_no=? AND query=? AND source=? AND state IN ('collected', 'indexed')""",
                (self._now(), *key))
            if result.rowcount != 1:
                raise ValueError("수집 완료 후에만 색인 완료로 전환할 수 있습니다.")

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()
