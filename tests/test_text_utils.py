"""占位符保护 / 还原测试。"""

from utils.text_utils import clean_text, protect_texts, restore_texts


def test_protect_and_restore():
    texts = ["URL: https://example.com/path?q=1，价格 $12.50，变量 %s"]
    protected, mapping = protect_texts(texts)
    assert "https://example.com/path?q=1" not in protected[0]
    translated = [protected[0].replace("https://", "[译]").replace("$12.50", "[译]").replace("%s", "[译]")]
    restored = restore_texts(translated, mapping)
    assert "https://example.com/path?q=1" in restored[0]
    assert "$12.50" in restored[0]
    assert "%s" in restored[0]


def test_numbers_kept():
    texts = ["Level 3, 128.5 damage, 90% HP"]
    protected, mapping = protect_texts(texts)
    translated = [t.replace("Level", "[译]").replace("damage", "[译]").replace("HP", "[译]") for t in protected]
    restored = restore_texts(translated, mapping)
    assert "3" in restored[0]
    assert "128.5" in restored[0]
    assert "90%" in restored[0]


def test_repeated_token_all_occurrences_protected():
    texts = ["ID 42 and ID 42 again"]
    protected, mapping = protect_texts(texts)
    translated = [t.replace("ID", "[译]") for t in protected]
    restored = restore_texts(translated, mapping)
    assert restored[0] == "[译] 42 and [译] 42 again"


def test_clean_text_removes_invisible_chars():
    dirty = "你好\u200b世界\x02\x01%sworld"
    cleaned = clean_text(dirty)
    assert "\u200b" not in cleaned
    assert "\x02" not in cleaned
    assert cleaned == "你好世界%sworld"


def test_clean_text_replaces_replacement_char():
    assert clean_text("a\ufffdb") == "a?b"
