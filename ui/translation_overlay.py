"""透明置顶覆盖窗口：把译文画在原文位置上方。"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import (
    QAbstractAnimation,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QWidget

from app.logger import get_logger
from ui.motion import (
    BASE,
    FAST,
    MICRO,
    SLOW,
    ENTER_EASING,
    EXIT_EASING,
    MOVE_EASING,
    motion_duration,
)

log = get_logger("overlay")


@dataclass
class Block:
    rect: QRectF
    text: str
    color: QColor
    background: str = ""


class TranslationOverlayWindow(QWidget):
    close_requested = Signal()

    def __init__(self, config, parent: QWidget | None = None) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(None, flags)
        self._config = config
        self._blocks: list[Block] = []
        self._size_groups: dict[int, float] = {}
        self._font_cache: dict[int, QFont] = {}
        self._reveal_ranks: dict[int, int] = {}
        self._reveal_progress = 1.0
        self._edit_mode = False
        self._drag_index: int | None = None
        self._drag_offset = QPoint(0, 0)

        self._fade_animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_animation.setEndValue(1.0)
        self._fade_animation.setEasingCurve(ENTER_EASING)
        self._hide_animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._hide_animation.setEndValue(0.0)
        self._hide_animation.setEasingCurve(EXIT_EASING)
        self._hide_animation.finished.connect(self.hide)
        self._reveal_animation = QVariantAnimation(self)
        self._reveal_animation.setEasingCurve(MOVE_EASING)
        self._reveal_animation.valueChanged.connect(self._set_reveal_progress)
        self._reveal_animation.finished.connect(self._finish_reveal)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc.activated.connect(self._on_escape)

    # ------------------------------------------------------------- public
    def set_blocks(self, blocks: list[Block]) -> None:
        self._reveal_animation.stop()
        self._blocks = blocks
        self._font_cache = {}
        self._compute_size_groups()
        self._compute_reveal_order()
        self._reveal_progress = 0.0 if blocks and not self._edit_mode else 1.0
        if self.isVisible() and self._reveal_progress < 1.0:
            self._start_reveal()
        self.update()

    def clear(self) -> None:
        self._reveal_animation.stop()
        self._blocks = []
        self._size_groups = {}
        self._font_cache = {}
        self._reveal_ranks = {}
        self._reveal_progress = 1.0
        self.update()

    def _compute_size_groups(self) -> None:
        """块高聚类：同一屏高度相近的块（差 ≤4 逻辑px）归为一组，
        组内统一用中位高度计算字号——消除 OCR 框高噪声导致的同级文字
        8/10/12pt 参差（用户：文字大小不一）。层级（标题 vs 子项）仍保留。
        """
        self._size_groups = {}
        if not self._blocks:
            return
        indexed = sorted(
            range(len(self._blocks)), key=lambda i: self._blocks[i].rect.height()
        )
        groups: list[list[int]] = []
        for idx in indexed:
            height = self._blocks[idx].rect.height()
            if groups and height - self._blocks[groups[-1][0]].rect.height() <= 4:
                groups[-1].append(idx)
            else:
                groups.append([idx])
        for group in groups:
            heights = sorted(self._blocks[i].rect.height() for i in group)
            median_height = heights[len(heights) // 2]
            for idx in group:
                self._size_groups[idx] = median_height

    def _compute_reveal_order(self) -> None:
        """Reveal in visual reading order without changing the block list."""
        ordered = sorted(
            range(len(self._blocks)),
            key=lambda index: (
                self._blocks[index].rect.top(),
                self._blocks[index].rect.left(),
            ),
        )
        self._reveal_ranks = {index: rank for rank, index in enumerate(ordered)}

    def _set_reveal_progress(self, value) -> None:
        self._reveal_progress = max(0.0, min(1.0, float(value)))
        self.update()

    def _finish_reveal(self) -> None:
        self._reveal_progress = 1.0
        self.update()

    def _start_reveal(self) -> None:
        self._reveal_animation.stop()
        if not self._blocks or self._edit_mode:
            self._finish_reveal()
            return

        duration = motion_duration(SLOW, large_surface=True)
        if duration <= 0:
            self._finish_reveal()
            return

        start = max(0.0, min(1.0, self._reveal_progress))
        if start >= 1.0:
            return
        self._reveal_animation.setDuration(
            max(MICRO, int(round(duration * (1.0 - start))))
        )
        self._reveal_animation.setStartValue(start)
        self._reveal_animation.setEndValue(1.0)
        self._reveal_animation.start()

    def _block_reveal_progress(self, index: int) -> float:
        """Map the shared timeline to a restrained per-block stagger."""
        overall = max(0.0, min(1.0, self._reveal_progress))
        count = len(self._blocks)
        if overall <= 0.0 or count <= 0:
            return 0.0
        if overall >= 1.0 or count == 1:
            return overall

        stagger_span = min(0.34, 0.09 * (count - 1))
        rank = self._reveal_ranks.get(index, index)
        start = stagger_span * rank / max(1, count - 1)
        segment = max(0.01, 1.0 - stagger_span)
        return max(0.0, min(1.0, (overall - start) / segment))

    def show_fade(self) -> None:
        """Show with one restrained fade and, for new content, a block stagger."""
        was_hiding = self._hide_animation.state() == QAbstractAnimation.State.Running
        self._hide_animation.stop()

        duration = motion_duration(BASE, large_surface=True)
        if duration <= 0:
            self._fade_animation.stop()
            self.setWindowOpacity(1.0)
            if not self.isVisible():
                self.show()
            self._start_reveal()
            self.raise_()
            self.update()
            return

        if self.isVisible() and not was_hiding:
            if self._reveal_progress < 1.0:
                self._start_reveal()
            self.raise_()
            return

        self._fade_animation.stop()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()

        start_opacity = max(0.0, min(1.0, self.windowOpacity()))
        if start_opacity < 1.0:
            self._fade_animation.setDuration(
                max(MICRO, int(round(duration * (1.0 - start_opacity))))
            )
            self._fade_animation.setStartValue(start_opacity)
            self._fade_animation.start()
        else:
            self.setWindowOpacity(1.0)
        if self._reveal_progress < 1.0:
            self._start_reveal()
        self.raise_()

    def hide_fade(self) -> None:
        """隐藏译文时使用短淡出，避免显示/隐藏两套节奏不一致。"""
        if not self.isVisible():
            return
        self._fade_animation.stop()
        self._hide_animation.stop()
        self._reveal_animation.stop()

        duration = motion_duration(FAST, large_surface=True)
        start_opacity = max(0.0, min(1.0, self.windowOpacity()))
        if duration <= 0 or start_opacity <= 0.0:
            self.setWindowOpacity(0.0)
            self.hide()
            return
        self._hide_animation.setDuration(
            max(MICRO, int(round(duration * start_opacity)))
        )
        self._hide_animation.setStartValue(start_opacity)
        self._hide_animation.start()

    def apply_style(self) -> None:
        self._font_cache = {}
        self.update()

    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not enabled)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not enabled)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, not enabled)
        if enabled:
            self._reveal_animation.stop()
            self._finish_reveal()
            self.activateWindow()
            self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.update()

    # ------------------------------------------------------------- mouse
    def mousePressEvent(self, event) -> None:
        if not self._edit_mode or event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        self._drag_index = None
        for idx in range(len(self._blocks) - 1, -1, -1):
            if self._blocks[idx].rect.contains(pos):
                self._drag_index = idx
                self._drag_offset = pos.toPoint() - self._blocks[idx].rect.topLeft().toPoint()
                break

    def mouseMoveEvent(self, event) -> None:
        if not self._edit_mode or self._drag_index is None:
            return
        block = self._blocks[self._drag_index]
        top_left = event.position().toPoint() - self._drag_offset
        max_x = max(0, self.width() - int(block.rect.width()))
        max_y = max(0, self.height() - int(block.rect.height()))
        block.rect.moveTopLeft(
            QPointF(
                min(max(top_left.x(), 0), max_x),
                min(max(top_left.y(), 0), max_y),
            )
        )
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_index = None

    def _on_escape(self) -> None:
        if self._edit_mode:
            self.close_requested.emit()

    # ------------------------------------------------------------- paint
    def paintEvent(self, event) -> None:
        if not self._blocks:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        border_color = QColor(str(self._config.get("overlay.border_color", "#FFFFFF")))
        border_color.setAlpha(int(self._config.get("overlay.border_alpha", 60)))
        radius = int(self._config.get("overlay.border_radius", 4))
        padding = int(self._config.get("overlay.padding", 4))
        auto_background = bool(self._config.get("overlay.auto_background", True))
        configured_background = QColor(str(self._config.get("overlay.background_color", "#000000")))

        for idx, block in enumerate(self._blocks):
            reveal = self._block_reveal_progress(idx)
            if reveal <= 0.0:
                continue
            painter.save()
            painter.setOpacity(reveal)

            rect = block.rect.adjusted(0.5, 0.5, -0.5, -0.5)
            if reveal < 1.0:
                rect.translate(0.0, round((1.0 - reveal) * 4.0))
                clip_width = rect.width() * (0.74 + 0.26 * reveal)
                painter.setClipRect(
                    QRectF(
                        rect.left() - 1.0,
                        rect.top() - 1.0,
                        clip_width + 1.0,
                        rect.height() + 2.0,
                    ),
                    Qt.ClipOperation.IntersectClip,
                )
            # 极小块：padding 不能吃掉全部绘制空间（否则译文不可见，只剩空块）
            eff_padding = min(padding, max(1, int(rect.height()) // 4))
            font = self._font_cache.get(idx)
            if font is None:
                font = self._fit_font(
                    block.text,
                    block.rect.adjusted(0.5, 0.5, -0.5, -0.5),
                    eff_padding,
                    size_height=self._size_groups.get(idx),
                )
                self._font_cache[idx] = font
            text_rect = rect.adjusted(eff_padding, eff_padding, -eff_padding, -eff_padding)
            # 不再扩展块高度：块保持 OCR 框原尺寸，避免相邻块互相挤压重叠
            # （用户反馈“原文不挤，译文全挤在一起”）。多行文字由 TextDontClip 完整绘制。

            if auto_background:
                if block.background:
                    # 用识别出的原界面背景色填充，译文与原界面融为一体
                    background = QColor(block.background)
                else:
                    # 深色文字配浅色底、浅色文字配深色底
                    background = QColor("#FFFFFF" if block.color.lightness() < 128 else "#000000")
            else:
                background = QColor(configured_background)
            background.setAlpha(int(self._config.get("overlay.background_alpha", 160)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(rect, radius, radius)

            if (
                bool(self._config.get("overlay.show_border", True))
                and border_color.alpha() > 0
            ):
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(border_color, 1))
                painter.drawRoundedRect(rect, radius, radius)

            painter.setFont(font)
            painter.setPen(block.color)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignVCenter
                | Qt.TextFlag.TextWordWrap
                | Qt.TextFlag.TextDontClip,
                block.text,
            )
            painter.restore()

    def _fit_font(
        self, text: str, rect: QRectF, padding: int, size_height: float | None = None
    ) -> QFont:
        families = self._font_families()
        pref_size = int(self._config.get("overlay.font_size", 18))
        min_size = int(self._config.get("overlay.min_font_size", 8))
        max_width = max(1, int(rect.width()) - 2 * padding)
        # 组内统一高度：消除 OCR 框高噪声造成的同级字号参差
        height = size_height if size_height is not None else rect.height()

        # 极小按钮/标签：配置下限仍放不下时允许继续压到更低硬下限
        floor_size = max(3, min_size - 3)
        # 目标字号：匹配原文字号。OCR 框高 ≈ 原文行高（含行距），实际文字
        # 字形高 ≈ 框高 * 0.75；pt = 字形高 / 1.333，故 target ≈ 框高 * 0.56。
        # 之前按 0.70/行高≤框高会让译文文字撑满含 padding 的框，比原文大、
        # 行距被吃光 → 相邻块视觉挤压（用户：原文不挤译文挤）。
        raw = max(min_size, min(pref_size, int(round(height * 0.56))))
        _STEPS = (8, 10, 12, 14, 16, 18, 22)
        target = min(_STEPS, key=lambda step: abs(step - raw)) if raw >= 8 else raw
        # 从目标档位按档位递减（10→8→floor），跳过 9/11 这类怪值
        candidates = [step for step in reversed(_STEPS) if step <= target]
        if target < 8:
            candidates = [target]
        candidates.append(floor_size)
        for size in candidates:
            font = self._make_font(families, size)
            metrics = QFontMetrics(font)
            bounds = metrics.boundingRect(
                0, 0, max_width, 100000, Qt.TextFlag.TextWordWrap, text
            )
            # 严格约束：字号行高不得超过块高（OCR 框高 ≈ 原文行高）。
            # 之前允许行高超框会让译文比原文大、块高度被撑开、相邻块互相重叠，
            # 用户反馈“大了之后就会互相重叠”。多行长文本由 paintEvent 扩展块高兜底。
            # 预算用组统一高度（+1 容忍行间距），同组块即使实际框高差 1px
            # 也落到同一档位，不再 8/10pt 参差
            height_budget = int(height) + 1
            if bounds.height() <= height_budget and bounds.width() <= max_width:
                return font
        return self._make_font(families, floor_size)

    def _font_families(self) -> list[str]:
        """字体回退链：自定义字体在前，CJK 字体兜底，避免中文/日文/韩文变豆腐块。"""
        custom = str(self._config.get("overlay.font_family", "")) or ""
        families = [custom] if custom else []
        families += [
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "Noto Sans CJK SC",
            "SimSun",
            "Segoe UI",
            "Arial",
        ]
        return families

    @staticmethod
    def _make_font(families: list[str], size: int) -> QFont:
        font = QFont()
        font.setFamilies(families)
        font.setPointSize(size)
        return font
