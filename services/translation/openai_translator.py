"""OpenAI 兼容 Chat Completions 翻译适配器。"""

from __future__ import annotations

import json
import re
from typing import Any

import requests

from services.translation.base import TranslationError, Translator, register_translator
from utils.language_utils import to_openai_lang

DEFAULT_GLOSSARY = """\
health = 生命值
mana = 法力
attack = 攻击力
defense = 防御力
damage = 伤害
experience = 经验
level = 等级
quest = 任务
inventory = 背包
settings = 设置
save = 保存
load = 读取
"""


def _parse_json_array(content: str) -> list[str] | None:
    content = content.strip()
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except Exception:
        pass
    # 模型偶尔会包一层 markdown 代码块或前后有说明文字
    match = re.search(r"\[[\s\S]*\]", content)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except Exception:
            pass
    return None


class OpenAITranslator(Translator):
    name = "openai"

    def __init__(self, config_section: dict, cache=None, api_key: str = "") -> None:
        super().__init__(config_section, cache)
        openai_cfg = config_section.get("openai", {}) if config_section else {}
        self.api_key = api_key or str(openai_cfg.get("api_key", ""))
        self.base_url = str(openai_cfg.get("base_url", "https://api.openai.com/v1")).rstrip("/")
        self.model = str(openai_cfg.get("model", "gpt-4o-mini"))
        custom_glossary = str(config_section.get("glossary", "")) if config_section else ""
        self.glossary = DEFAULT_GLOSSARY + ("\n" + custom_glossary if custom_glossary else "")

    def _translate_batch(self, texts: list[str], source_language: str | None, target_language: str) -> list[str]:
        if not self.api_key:
            raise TranslationError(
                "缺少 OpenAI API Key：请设置 OPENAI_API_KEY 环境变量或在设置中填写"
            )

        target_desc = to_openai_lang(target_language)
        source_desc = to_openai_lang(source_language) if source_language and source_language != "auto" else "自动检测"
        numbered = "\n".join(f"{i + 1}\t{text}" for i, text in enumerate(texts))

        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是资深游戏本地化与字幕翻译专家。把用户按行号给出的文本翻译成 "
                        f"{target_desc}（源语言：{source_desc}）。\n"
                        "要求：\n"
                        "1. 译文口语自然、贴近母语表达，不要逐字直译，不要翻译腔；\n"
                        "2. 长度尽量与原文接近，适合覆盖显示；\n"
                        "3. 专有名词、人名、地名、产品名保持原文；\n"
                        "4. 术语表（原文 = 译文）必须严格遵守：\n"
                        f"{self.glossary}\n"
                        "5. 只输出 JSON 数组，元素顺序和数量与输入行完全一致，每元素一行译文；\n"
                        "6. 保留数字、网址、邮箱、占位符、变量名和特殊符号；\n"
                        "7. 不要输出任何解释、引号包裹或代码块标记。"
                    ),
                },
                {"role": "user", "content": numbered},
            ],
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=float(self.config.get("timeout_seconds", 30)),
            )
        except requests.RequestException as exc:
            raise TranslationError("OpenAI 请求失败（超时或网络错误）") from exc

        response.encoding = "utf-8"
        if response.status_code == 401:
            raise TranslationError("OpenAI API Key 无效或已过期")
        if response.status_code == 429:
            raise TranslationError("OpenAI 请求频率或额度受限（429）")
        if response.status_code != 200:
            raise TranslationError(f"OpenAI 返回错误 {response.status_code}")

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise TranslationError("OpenAI 响应格式异常") from exc

        parsed = _parse_json_array(content)
        if parsed is None:
            raise TranslationError("OpenAI 返回内容不是可解析的 JSON 数组")
        if len(parsed) != len(texts):
            # 数量对不上说明模型漏行/错位，抛错让上层逐条重试，避免译文错位
            raise TranslationError(f"OpenAI 返回 {len(parsed)} 条，期望 {len(texts)} 条，已逐条重试")
        return parsed


register_translator(OpenAITranslator)
