"""翻译器工厂。"""

from __future__ import annotations

from typing import Callable

from services.translation.base import Translator, _REGISTRY, list_translators

_SERVICE_DISPLAY = {
    "mock": "Mock（本地词典演示）",
    "mymemory": "MyMemory（免费在线）",
    "google_free": "Google 免费（无 Key）",
    "openai": "OpenAI",
    "deepl": "DeepL",
    "google": "Google 翻译",
}


def service_display_name(name: str) -> str:
    return _SERVICE_DISPLAY.get(name, name)


def create_translator(
    name: str,
    config_section: dict,
    cache=None,
    api_key_resolver: Callable[[str], str] | None = None,
) -> Translator:
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知翻译服务：{name}")
    api_key = api_key_resolver(name) if api_key_resolver else ""
    return cls(config_section, cache, api_key=api_key)


__all__ = ["create_translator", "list_translators", "service_display_name"]
