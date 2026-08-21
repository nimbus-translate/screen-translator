"""Animated personalization controls used by the settings page."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from PySide6.QtCore import QLineF, QPointF, QRectF, QSize, Qt, Signal, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QRadioButton, QSizePolicy, QWidget

from ui.appearance import current_tokens, resolve_tokens
from ui.motion import ENTER_EASING, FAST, MOVE_EASING, motion_duration


def _mix(start: QColor, end: QColor, progress: float) -> QColor:
    progress = max(0.0, min(1.0, progress))
    return QColor.fromRgbF(
        start.redF() + (end.redF() - start.redF()) * progress,
        start.greenF() + (end.greenF() - start.greenF()) * progress,
        start.blueF() + (end.blueF() - start.blueF()) * progress,
        start.alphaF() + (end.alphaF() - start.alphaF()) * progress,
    )


_TOKEN_COLOR_FIELDS = (
    "accent",
    "root",
    "surface",
    "surface_alt",
    "surface_hover",
    "ink",
    "ink_soft",
    "muted",
    "border",
    "border_strong",
    "disabled",
    "accent_hover",
    "accent_pressed",
    "accent_soft",
    "accent_soft_hover",
    "accent_border",
    "on_accent",
)


class PersonalizationChoice(QRadioButton):
    """A keyboard-accessible option card with a restrained underline motion."""

    chosen = Signal(str)

    def __init__(
        self,
        value: str,
        label: str,
        detail: str,
        *,
        swatch: str | None = None,
        preview_palette: str | None = None,
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.value = value
        self.label = label
        self.detail = detail
        self.swatch = swatch
        self.preview_palette = preview_palette
        self.compact = compact
        self._selected = False
        self._selected_progress = 0.0
        self._hover_progress = 0.0
        self._press_progress = 0.0
        self._accent_override: str | None = None

        self.setText(label)
        self.setAutoExclusive(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(label)
        self.setAccessibleDescription(detail)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(52 if compact else 76)
        self.clicked.connect(self._handle_clicked)

        self._selection_animation = QVariantAnimation(self)
        self._selection_animation.setEasingCurve(ENTER_EASING)
        self._selection_animation.valueChanged.connect(self._set_selected_progress)
        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setEasingCurve(ENTER_EASING)
        self._hover_animation.valueChanged.connect(self._set_hover_progress)
        self._press_animation = QVariantAnimation(self)
        self._press_animation.setEasingCurve(MOVE_EASING)
        self._press_animation.valueChanged.connect(self._set_press_progress)

    def sizeHint(self) -> QSize:
        return QSize(148, 52 if self.compact else 76)

    def set_accent(self, color: str) -> None:
        self._accent_override = color
        self.update()

    def set_selected(self, selected: bool, *, animate: bool = True) -> None:
        selected = bool(selected)
        self._selected = selected
        self.setChecked(selected)
        state_hint = "当前已选择" if selected else "可选择"
        self.setAccessibleDescription(f"{self.detail}；{state_hint}")
        target = 1.0 if selected else 0.0
        self._selection_animation.stop()
        duration = motion_duration(FAST)
        if not animate or duration == 0:
            self._selected_progress = target
            self.update()
            return
        self._selection_animation.setDuration(duration)
        self._selection_animation.setStartValue(self._selected_progress)
        self._selection_animation.setEndValue(target)
        self._selection_animation.start()

    def _animate_hover(self, target: float) -> None:
        self._hover_animation.stop()
        duration = motion_duration(FAST)
        if duration == 0:
            self._hover_progress = target
            self.update()
            return
        self._hover_animation.setDuration(duration)
        self._hover_animation.setStartValue(self._hover_progress)
        self._hover_animation.setEndValue(target)
        self._hover_animation.start()

    def _animate_press(self, target: float) -> None:
        self._press_animation.stop()
        duration = motion_duration(80)
        if duration == 0:
            self._press_progress = target
            self.update()
            return
        self._press_animation.setDuration(duration)
        self._press_animation.setStartValue(self._press_progress)
        self._press_animation.setEndValue(target)
        self._press_animation.start()

    def _set_selected_progress(self, value: Any) -> None:
        self._selected_progress = float(value)
        self.update()

    def _set_hover_progress(self, value: Any) -> None:
        self._hover_progress = float(value)
        self.update()

    def _set_press_progress(self, value: Any) -> None:
        self._press_progress = float(value)
        self.update()

    def _handle_clicked(self, _checked: bool = False) -> None:
        self.chosen.emit(self.value)

    def enterEvent(self, event) -> None:
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate_hover(0.0)
        self._animate_press(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._animate_press(1.0)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._animate_press(0.0)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            self._animate_press(1.0)
            super().keyPressEvent(event)
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._animate_press(1.0)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._animate_press(0.0)
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.click()
                event.accept()
                return
        super().keyReleaseEvent(event)

    def focusInEvent(self, event) -> None:
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self.update()
        super().focusOutEvent(event)

    def paintEvent(self, event) -> None:
        tokens = current_tokens()
        sample = (
            resolve_tokens({"palette": self.preview_palette, "accent": self._accent_override or tokens.accent})
            if self.preview_palette
            else tokens
        )
        accent = QColor(self.swatch or self._accent_override or tokens.accent)
        emphasis = max(self._hover_progress * 0.36, self._selected_progress)
        fill = _mix(QColor(tokens.surface), QColor(tokens.accent_soft), emphasis * 0.72)
        border = _mix(QColor(tokens.border), QColor(accent), emphasis * 0.82)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(0, round(self._press_progress * 1.7))
        rect = QRectF(self.rect()).adjusted(1.2, 1.2, -1.2, -2.6)
        painter.setBrush(fill)
        painter.setPen(QPen(border, 1.0 + 0.45 * self._selected_progress))
        painter.drawRoundedRect(rect, 11, 11)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(tokens.accent), 1.5))
            painter.drawRoundedRect(rect.adjusted(2.5, 2.5, -2.5, -2.5), 9, 9)

        label_font = QFont()
        label_font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"])
        label_font.setPixelSize(13)
        label_font.setWeight(QFont.Weight.DemiBold)
        detail_font = QFont(label_font)
        detail_font.setPixelSize(10)
        detail_font.setWeight(QFont.Weight.Normal)

        text_x = 14
        if self.preview_palette:
            preview = QRectF(rect.left() + 12, rect.top() + 13, 48, rect.height() - 26)
            painter.setPen(QPen(QColor(sample.border), 1.0))
            painter.setBrush(QColor(sample.root))
            painter.drawRoundedRect(preview, 7, 7)
            inner = preview.adjusted(8, 8, -8, -8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(sample.surface))
            painter.drawRoundedRect(inner, 4, 4)
            painter.setPen(QPen(QColor(sample.ink), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QLineF(inner.left() + 5, inner.top() + 7, inner.right() - 5, inner.top() + 7))
            painter.setPen(QPen(accent, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QLineF(inner.left() + 8, inner.bottom() - 6, inner.left() + 18, inner.bottom() - 6))
            text_x = int(preview.right() + 11)
        elif self.swatch:
            center_x = rect.left() + 21
            center_y = rect.center().y()
            painter.setPen(QPen(_mix(accent, QColor(tokens.surface), 0.45), 2.0))
            painter.setBrush(accent)
            painter.drawEllipse(QRectF(center_x - 7, center_y - 7, 14, 14))
            text_x = int(center_x + 16)

        painter.setFont(label_font)
        painter.setPen(QColor(tokens.ink))
        if self.compact or self.swatch:
            painter.drawText(
                QRectF(text_x, rect.top(), rect.right() - text_x - 8, rect.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self.label,
            )
        else:
            painter.drawText(text_x, int(rect.top() + 28), self.label)
            painter.setFont(detail_font)
            painter.setPen(QColor(tokens.muted))
            painter.drawText(text_x, int(rect.top() + 48), self.detail)

        line_width = 8 + 24 * self._selected_progress
        line_x = rect.left() + 14
        painter.setPen(QPen(accent, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QLineF(line_x, rect.bottom() - 8, line_x + line_width, rect.bottom() - 8))


class PersonalizationChoiceRow(QWidget):
    valueChanged = Signal(str)

    def __init__(
        self,
        specs: list[tuple[str, str, str]],
        *,
        mode: str = "default",
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._value = specs[0][0] if specs else ""
        self._mode = mode
        self._compact = compact
        self.cards: list[PersonalizationChoice] = []
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        for value, label, detail in specs:
            self._append_card(value, label, detail)
        self.set_value(self._value, emit=False, animate=False)

    def _append_card(
        self, value: str, label: str, detail: str
    ) -> PersonalizationChoice:
        card = PersonalizationChoice(
            value,
            label,
            detail,
            swatch=value if self._mode == "accent" else None,
            preview_palette=value if self._mode == "palette" else None,
            compact=self._compact or self._mode == "accent",
        )
        card.chosen.connect(self.set_value)
        self.cards.append(card)
        self._layout.addWidget(card, 1)
        return card

    def value(self) -> str:
        return self._value

    def set_value(self, value: str, *, emit: bool = True, animate: bool = True) -> None:
        if self._mode == "accent":
            color = QColor(value)
            if not color.isValid() or color.alpha() != 255:
                return
            value = color.name(QColor.NameFormat.HexRgb).upper()
        if not any(card.value == value for card in self.cards):
            if self._mode != "accent":
                return
            self._append_card(value, "自定义", value)
        changed = value != self._value
        self._value = value
        for card in self.cards:
            card.set_selected(card.value == value, animate=animate)
        if emit and changed:
            self.valueChanged.emit(value)

    def set_accent(self, color: str) -> None:
        for card in self.cards:
            card.set_accent(color)


class AppearancePreview(QWidget):
    """A one-shot capture-to-translation preview; it never loops or scans."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(188)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._state: dict[str, Any] = {
            "palette": "warm_paper",
            "accent": "#2878E8",
            "motion_profile": "flow",
            "density": "balanced",
            "surface": "layered",
            "reduce_motion": False,
        }
        self._from_tokens = resolve_tokens(self._state)
        self._to_tokens = self._from_tokens
        self._transition_progress = 1.0
        self._reveal_progress = 1.0
        self._transition_animation = QVariantAnimation(self)
        self._transition_animation.setEasingCurve(MOVE_EASING)
        self._transition_animation.valueChanged.connect(self._set_transition_progress)
        self._reveal_animation = QVariantAnimation(self)
        self._reveal_animation.setEasingCurve(ENTER_EASING)
        self._reveal_animation.valueChanged.connect(self._set_reveal_progress)

    def state(self) -> dict[str, Any]:
        return dict(self._state)

    def _interpolated_tokens(self):
        progress = max(0.0, min(1.0, self._transition_progress))
        if progress >= 1.0:
            return self._to_tokens
        colors = {
            name: _mix(
                QColor(getattr(self._from_tokens, name)),
                QColor(getattr(self._to_tokens, name)),
                progress,
            ).name(QColor.NameFormat.HexRgb).upper()
            for name in _TOKEN_COLOR_FIELDS
        }
        return replace(self._to_tokens, **colors)

    def set_options(self, state: dict[str, Any], *, animate: bool = True) -> None:
        visible_tokens = self._interpolated_tokens()
        self._transition_animation.stop()
        self._reveal_animation.stop()
        self._from_tokens = visible_tokens
        self._state = dict(state)
        self._to_tokens = resolve_tokens(self._state)
        profile = self._to_tokens.motion_profile
        reduced = self._to_tokens.reduce_motion or profile == "minimal"
        duration = {"flow": 220, "calm": 310, "minimal": 0}.get(profile, 220)
        if not animate or reduced:
            self._transition_progress = 1.0
            self._reveal_progress = 1.0
            self.update()
            return
        self._transition_progress = 0.0
        self._reveal_progress = 0.0
        self._transition_animation.setDuration(duration)
        self._transition_animation.setStartValue(0.0)
        self._transition_animation.setEndValue(1.0)
        self._transition_animation.start()
        self._reveal_animation.setDuration(duration + 180)
        self._reveal_animation.setStartValue(0.0)
        self._reveal_animation.setEndValue(1.0)
        self._reveal_animation.start()

    def replay(self) -> None:
        self.set_options(self._state, animate=True)

    def _set_transition_progress(self, value: Any) -> None:
        self._transition_progress = float(value)
        self.update()

    def _set_reveal_progress(self, value: Any) -> None:
        self._reveal_progress = float(value)
        self.update()

    def _color(self, name: str) -> QColor:
        return _mix(
            QColor(getattr(self._from_tokens, name)),
            QColor(getattr(self._to_tokens, name)),
            self._transition_progress,
        )

    @staticmethod
    def _stage(progress: float, start: float, end: float) -> float:
        if end <= start:
            return 1.0
        return max(0.0, min(1.0, (progress - start) / (end - start)))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -2.0)
        painter.setBrush(self._color("root"))
        painter.setPen(QPen(self._color("border"), 1.0))
        painter.drawRoundedRect(outer, 14, 14)

        title_font = QFont()
        title_font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"])
        title_font.setPixelSize(13)
        title_font.setWeight(QFont.Weight.DemiBold)
        small_font = QFont(title_font)
        small_font.setPixelSize(10)
        small_font.setWeight(QFont.Weight.Normal)
        painter.setFont(title_font)
        painter.setPen(self._color("ink"))
        painter.drawText(18, 27, "实时体验")
        painter.setFont(small_font)
        painter.setPen(self._color("muted"))
        painter.drawText(80, 27, "框选 → 识别 → 译文")

        density_pad = {"spacious": 17, "balanced": 13, "compact": 9}.get(
            self._to_tokens.density, 13
        )
        body = outer.adjusted(14, 39, -14, -13)
        painter.setBrush(self._color("surface"))
        painter.setPen(QPen(self._color("border"), 1.0))
        painter.drawRoundedRect(body, 11, 11)
        divider_x = body.left() + body.width() * 0.43
        painter.setPen(QPen(self._color("border"), 1.0))
        painter.drawLine(QLineF(divider_x, body.top() + 13, divider_x, body.bottom() - 13))

        left = QRectF(body.left() + density_pad, body.top() + 11, body.width() * 0.38, body.height() - 22)
        right = QRectF(divider_x + density_pad, body.top() + 11, body.right() - divider_x - density_pad * 2, body.height() - 22)
        painter.setFont(small_font)
        painter.setPen(self._color("muted"))
        painter.drawText(QPointF(left.left(), left.top() + 12), "原文区域")
        painter.drawText(QPointF(right.left(), right.top() + 12), "译文出现")

        mini = QRectF(left.left(), left.top() + 23, left.width(), left.height() - 27)
        painter.setBrush(self._color("surface_alt"))
        painter.setPen(QPen(self._color("border"), 1.0))
        painter.drawRoundedRect(mini, 8, 8)
        painter.setPen(QPen(self._color("ink_soft"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QLineF(mini.left() + 14, mini.top() + 17, mini.right() - 20, mini.top() + 17))
        painter.setPen(QPen(self._color("muted"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QLineF(mini.left() + 14, mini.top() + 31, mini.right() - 37, mini.top() + 31))
        dash_stage = self._stage(self._reveal_progress, 0.02, 0.32)
        dash_width = 8 + 25 * dash_stage
        painter.setPen(QPen(self._color("accent"), 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QLineF(
            mini.left() + 14,
            mini.bottom() - 13,
            mini.left() + 14 + dash_width,
            mini.bottom() - 13,
        ))

        original_y = right.top() + 37
        painter.setPen(QPen(self._color("muted"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QLineF(right.left(), original_y, right.right() - 16, original_y))
        lines = (
            ("Translate what you see", 0.34, 0.65),
            ("所见，即刻可读", 0.56, 0.88),
        )
        for index, (text, start, end) in enumerate(lines):
            progress = self._stage(self._reveal_progress, start, end)
            painter.save()
            painter.setOpacity(progress)
            lift = 0 if self._to_tokens.reduce_motion else round(4 * (1.0 - progress))
            painter.setFont(title_font if index else small_font)
            painter.setPen(self._color("ink") if index else self._color("ink_soft"))
            painter.drawText(QPointF(right.left(), original_y + 27 + index * 25 + lift), text)
            painter.restore()

        badge_progress = self._stage(self._reveal_progress, 0.7, 1.0)
        painter.setOpacity(0.35 + 0.65 * badge_progress)
        badge = QRectF(right.right() - 89, right.bottom() - 21, 89, 20)
        painter.setBrush(self._color("accent_soft"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(badge, 9, 9)
        painter.setBrush(self._color("accent"))
        painter.drawEllipse(QRectF(badge.left() + 8, badge.center().y() - 2.5, 5, 5))
        painter.setFont(small_font)
        painter.setPen(self._color("accent_hover"))
        painter.drawText(
            badge.adjusted(19, 0, -6, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "主题已响应",
        )
