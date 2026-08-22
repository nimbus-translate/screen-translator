"""占位符保护 / 还原测试。"""

from utils.text_utils import clean_text, normalize_ocr_text, protect_texts, restore_texts


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


def test_safe_placeholders_survive_case_changes():
    protected, mapping = protect_texts(["Try Claude with CursorBench"])
    assert "__ST_KEEP_" in protected[0]
    restored = restore_texts([protected[0].lower()], mapping)
    assert restored == ["try Claude with CursorBench"]
    spaced = protected[0].replace("__ST", "__ ST").replace("__", " __", 1)
    assert "Claude" in restore_texts([spaced], mapping)[0]


def test_clean_text_removes_invisible_chars():
    dirty = "你好\u200b世界\x02\x01%sworld"
    cleaned = clean_text(dirty)
    assert "\u200b" not in cleaned
    assert "\x02" not in cleaned
    assert cleaned == "你好世界%sworld"


def test_clean_text_replaces_replacement_char():
    assert clean_text("a\ufffdb") == "a?b"


def test_normalize_tiny_english_table_ocr_confusions():
    assert normalize_ocr_text("SWE-8ench Pro") == "SWE-Bench Pro"
    assert normalize_ocr_text("Legal Agent 8enchmark") == "Legal Agent Benchmark"
    assert normalize_ocr_text("VlSlOn") == "vision"
    assert normalize_ocr_text("reasonlng") == "reasoning"
    assert normalize_ocr_text("no t00 ? 5") == "no tools"
    assert normalize_ocr_text("with t001s") == "with tools"
    assert normalize_ocr_text("tOOlS") == "tools"
    assert normalize_ocr_text("l<nowledge work") == "Knowledge work"
    assert normalize_ocr_text("Front i erCode (Diamond)") == "FrontierCode (Diamond)"
    assert normalize_ocr_text("MuItidisciplinary") == "Multidisciplinary"
    assert normalize_ocr_text("reason I ng") == "reasoning"
    assert normalize_ocr_text("GDPva � AA") == "GDPval-AA"
    assert normalize_ocr_text("Terminal-Bench 2 � 1") == "Terminal-Bench 2.1"
    assert normalize_ocr_text("Gemini C �") == "Gemini CLI"
    assert normalize_ocr_text("no tools �") == "no tools"
    assert normalize_ocr_text("GDPva 卜 AA") == "GDPval-AA"
    assert normalize_ocr_text("with tools 飞") == "with tools"
    assert normalize_ocr_text("Gemini C 凵") == "Gemini CLI"
    assert normalize_ocr_text("Hear from OUr customers") == "Hear from our customers"
    assert normalize_ocr_text("CIaude FabIe 5") == "Claude Fable 5"
    assert normalize_ocr_text("lt �� s opened up a class oflong") == "It's opened up a class of long"
    assert normalize_ocr_text("excites LIS most") == "excites us most"
    assert normalize_ocr_text("testing 飞 it took on complex 卜 long-horizon work tO agents") == "testing, it took on complex, long-horizon work to agents"
    assert normalize_ocr_text("testing ， it took on complex ， long-horizon") == "testing, it took on complex, long-horizon"
