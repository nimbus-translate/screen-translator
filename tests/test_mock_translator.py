"""Mock 翻译器本地词典测试。"""

from services.translation.mock_translator import MockTranslator


def make_translator():
    return MockTranslator({"mock_mode": "dictionary"}, None)


def test_phrase_translation():
    translator = make_translator()
    assert translator.translate(["Game Over"], "en", "zh") == ["游戏结束"]


def test_word_translation_keeps_unknown_and_numbers():
    translator = make_translator()
    result = translator.translate(["Hello World 123 Quest XP"], "en", "zh")[0]
    assert "你好" in result
    assert "世界" in result
    assert "123" in result
    assert "任务" in result


def test_non_chinese_target_falls_back_to_prefix():
    translator = make_translator()
    result = translator.translate(["Hello"], "auto", "ja")[0]
    assert result.startswith("[")
    assert translator.unsupported_direction is True


def test_chinese_target_supported():
    translator = make_translator()
    translator.translate(["Hello"], "auto", "zh")
    assert translator.unsupported_direction is False


def test_reverse_mode():
    translator = MockTranslator({"mock_mode": "reverse"}, None)
    assert translator.translate(["abc"], "en", "zh") == ["cba"]
