"""翻译缓存测试。"""

import json
import time

from services.translation.cache import TranslationCache


def test_cache_set_get(tmp_path):
    cache = TranslationCache(tmp_path / "cache.json")
    cache.set("en", "zh", "hello", "你好")
    assert cache.get("en", "zh", "hello") == "你好"
    assert cache.get("en", "zh", "other") is None


def test_cache_ttl_expiry(tmp_path):
    cache = TranslationCache(tmp_path / "cache.json", ttl_days=1e-9)
    cache.set("en", "zh", "hello", "你好")
    time.sleep(0.01)
    assert cache.get("en", "zh", "hello") is None


def test_cache_max_entries(tmp_path):
    cache = TranslationCache(tmp_path / "cache.json", ttl_days=30, max_entries=3)
    for i in range(10):
        cache.set("en", "zh", f"text{i}", f"译文{i}")
    # 只保留最近 3 条
    assert len(cache._data) <= 3
    assert cache.get("en", "zh", "text0") is None
    assert cache.get("en", "zh", "text9") == "译文9"


def test_cache_persist_roundtrip(tmp_path):
    path = tmp_path / "cache.json"
    cache = TranslationCache(path)
    cache.set("en", "zh", "hello", "你好")
    cache.flush()
    cache2 = TranslationCache(path)
    assert cache2.get("en", "zh", "hello") == "你好"


def test_cache_corrupt_file(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{broken json", encoding="utf-8")
    cache = TranslationCache(path)
    cache.set("en", "zh", "a", "b")
    assert cache.get("en", "zh", "a") == "b"
