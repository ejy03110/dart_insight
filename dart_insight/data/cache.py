"""Atomic, expiring JSON cache. Never store API keys or request URLs."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


class JsonCache:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, namespace: str, params: dict) -> Path:
        digest = hashlib.sha256(
            json.dumps(params, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return self.root / namespace / f"{digest}.json"

    def get(self, namespace: str, params: dict, ttl: int):
        if ttl == 0:
            return None
        try:
            entry = json.loads(self._path(namespace, params).read_text(encoding="utf-8"))
            if entry["version"] != 1 or not 0 <= time.time() - entry["created_at"] < ttl:
                return None
            return entry["payload"]
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def put(self, namespace: str, params: dict, payload):
        path = self._path(namespace, params)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             delete=False) as stream:
                temp_path = stream.name
                json.dump({"version": 1, "created_at": time.time(), "payload": payload},
                          stream, ensure_ascii=False)
            os.replace(temp_path, path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
