"""配置管理：JSON 读写、默认值合并、原子保存、密钥脱敏。"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

from app.logger import app_data_dir

DEFAULTS: dict[str, Any] = {
    "general": {
        "startup_with_system": False,
        "minimize_to_tray": True,
        "save_history": False,
        "history_dir": "",
    },
    "updates": {
        "auto_check": True,
        "include_prereleases": False,
        "repository": "nimbus-translate/screen-translator",
    },
    "appearance": {
        "schema_version": 1,
        "palette": "warm_paper",
        "accent": "#2878E8",
        "motion_profile": "flow",
        "density": "balanced",
        "surface": "layered",
        "reduce_motion": False,
    },
    "capture": {
        "select_mask_opacity": 84,
        "select_border_color": "#2878E8",
    },
    "ocr": {
        "engine": "windows",
        "lang": "auto",
        "min_confidence": 0.6,
        "merge_y_tolerance_ratio": 0.3,
        "merge_x_gap_ratio": 0.8,
        "paddle": {
            "use_gpu": False,
            "text_detection_model_name": "PP-OCRv5_mobile_det",
            "text_recognition_model_name": "PP-OCRv5_mobile_rec",
            "use_textline_orientation": False,
            "component_manifest_url": "https://github.com/nimbus-translate/screen-translator/releases/latest/download/paddle-component-manifest.json",
        },
    },
    "translation": {
        "service": "google_free",
        "auto_select_service": True,
        "mock_mode": "dictionary",
        "glossary": "",
        "source_language": "auto",
        "target_language": "zh",
        "keep_original": False,
        "timeout_seconds": 30,
        "max_retries": 3,
        "retry_delay_seconds": 1.0,
        "request_interval_seconds": 0.0,
        "google_free_max_workers": 8,
        "google_free_interval": 0.0,
        "cache_ttl_days": 30,
        "cache_max_entries": 2000,
        "openai": {
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
        },
        "deepl": {
            "api_key": "",
            "base_url": "https://api-free.deepl.com/v2",
            "formality": "default",
        },
        "google": {
            "api_key": "",
            "base_url": "https://translation.googleapis.com/language/translate/v2",
        },
    },
    "overlay": {
        "background_color": "#000000",
        "auto_background": True,
        "background_alpha": 160,
        "font_family": "",
        "font_size": 18,
        "text_color": "#FFFFFF",
        "use_auto_text_color": True,
        "padding": 4,
        "border_radius": 4,
        "border_color": "#FFFFFF",
        "border_alpha": 60,
        "show_border": True,
        "min_font_size": 8,
    },
    "hotkeys": {
        "capture_region": "ctrl+shift+a",
        "capture_fullscreen": "ctrl+shift+f",
        "capture_window": "ctrl+shift+w",
        "toggle_overlay": "ctrl+shift+h",
        "refresh": "ctrl+shift+r",
    },
}

_KEY_SECTIONS = (
    "translation.openai.api_key",
    "translation.deepl.api_key",
    "translation.google.api_key",
)


def _deep_merge(base: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _normalize_appearance(data: dict[str, Any]) -> None:
    defaults = DEFAULTS["appearance"]
    appearance = data.get("appearance")
    if not isinstance(appearance, dict):
        data["appearance"] = copy.deepcopy(defaults)
        return

    allowed = {
        "palette": {"warm_paper", "mist", "midnight", "system"},
        "motion_profile": {"flow", "calm", "minimal"},
        "density": {"spacious", "balanced", "compact"},
        "surface": {"clean", "layered"},
    }
    appearance["schema_version"] = 1
    for key, values in allowed.items():
        if appearance.get(key) not in values:
            appearance[key] = defaults[key]

    accent = str(appearance.get("accent", defaults["accent"])).strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", accent):
        appearance["accent"] = accent.upper()
    else:
        appearance["accent"] = defaults["accent"]
    if not isinstance(appearance.get("reduce_motion"), bool):
        appearance["reduce_motion"] = defaults["reduce_motion"]


class AppConfig:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else self._default_path()
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    @staticmethod
    def _default_path() -> Path:
        override = os.environ.get("SCREEN_TRANSLATOR_CONFIG")
        if override:
            return Path(override)
        return app_data_dir() / "config.json"

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fp:
                loaded = json.load(fp)
            if isinstance(loaded, dict):
                _deep_merge(self.data, loaded)
                _normalize_appearance(self.data)
                # This legacy field is retained for compatibility; capture
                # feedback now reads appearance.accent.  Normalize the red
                # debug value so old files no longer carry misleading state.
                capture = self.data.setdefault("capture", {})
                capture["select_border_color"] = "#2878E8"
                if capture.get("select_mask_opacity") == 100:
                    capture["select_mask_opacity"] = 84
                ocr = self.data.setdefault("ocr", {})
                if ocr.get("lang") == "ch":
                    ocr["lang"] = "zh"
        except (json.JSONDecodeError, OSError) as exc:
            backup = self.path.with_suffix(".json.bak")
            try:
                backup.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                pass
            from app.logger import get_logger

            get_logger("config").warning("配置文件损坏，已备份到 %s：%s", backup, exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(self.data, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def section(self, *names: str) -> dict:
        node: Any = self.data
        for name in names:
            node = node.get(name, {})
        return node if isinstance(node, dict) else {}

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def hotkeys(self) -> dict[str, str]:
        return dict(self.section("hotkeys"))

    def masked_snapshot(self) -> dict:
        snapshot = copy.deepcopy(self.data)
        for key in _KEY_SECTIONS:
            parts = key.split(".")
            node = snapshot
            for part in parts[:-1]:
                node = node.get(part, {})
            if parts[-1] in node and node[parts[-1]]:
                node[parts[-1]] = "***"
        return snapshot

    def api_key(self, service: str) -> str:
        env_map = {
            "openai": "OPENAI_API_KEY",
            "deepl": "DEEPL_API_KEY",
            "google": "GOOGLE_TRANSLATE_API_KEY",
        }
        env_value = os.environ.get(env_map.get(service, ""), "")
        if env_value:
            return env_value.strip()
        return str(self.get(f"translation.{service}.api_key", "")).strip()

    def reset(self) -> None:
        self.data = copy.deepcopy(DEFAULTS)
