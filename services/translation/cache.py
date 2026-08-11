"""翻译结果缓存（JSON 落地，可配 TTL 与容量上限）。"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from app.logger import get_logger

log = get_logger("translation.cache")


class TranslationCache:
    def __init__(
        self,
        path: Path | str,
        ttl_days: float = 30,
        max_entries: int = 2000,
    ) -> None:
        self.path = Path(path)
        self.ttl_seconds = ttl_days * 86400
        self.max_entries = max_entries
        self._data: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load()

    @staticmethod
    def _key(source: str | None, target: str, text: str) -> str:
        raw = f"{source or ''}|{target}|{text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, source: str | None, target: str, text: str) -> str | None:
        key = self._key(source, target, text)
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if time.time() - entry.get("ts", 0) > self.ttl_seconds:
                self._data.pop(key, None)
                return None
            return entry.get("translated")

    def set(self, source: str | None, target: str, text: str, translated: str) -> None:
        key = self._key(source, target, text)
        with self._lock:
            self._data[key] = {"text": text, "translated": translated, "ts": time.time()}
            self._prune_locked()

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
        self.flush()

    def flush(self) -> None:
        with self._lock:
            self._save_locked()

    def set_ttl(self, ttl_days: float, max_entries: int) -> None:
        with self._lock:
            self.ttl_seconds = ttl_days * 86400
            self.max_entries = max_entries
            self._prune_locked()

    # ------------------------------------------------------------------
    def _prune_locked(self) -> None:
        now = time.time()
        expired = [k for k, v in self._data.items() if now - v.get("ts", 0) > self.ttl_seconds]
        for k in expired:
            self._data.pop(k, None)
        if len(self._data) > self.max_entries:
            ordered = sorted(self._data.items(), key=lambda kv: kv[1].get("ts", 0))
            for key, _ in ordered[: len(self._data) - self.max_entries]:
                self._data.pop(key, None)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fp:
                loaded = json.load(fp)
            if isinstance(loaded, dict):
                self._data = loaded
        except (json.JSONDecodeError, OSError) as exc:
            try:
                self.path.rename(self.path.with_suffix(".json.bak"))
            except OSError:
                pass
            log.warning("翻译缓存损坏，已重置：%s", exc)

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(self._data, fp, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("翻译缓存保存失败：%s", exc)
