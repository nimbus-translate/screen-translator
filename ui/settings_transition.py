"""Input guard for the native settings page transition."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QWidget


class SettingsTransitionGuard(QWidget):
    """Transparent event shield used only while pages are moving.

    The guard deliberately has no custom paint event.  The transition is made
    from native widget geometry and opacity properties, so the visible pages
    remain the actual application widgets throughout the animation.
    """

    _BLOCKED_EVENTS = {
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.MouseMove,
        QEvent.Type.Wheel,
        QEvent.Type.KeyPress,
        QEvent.Type.KeyRelease,
        QEvent.Type.Shortcut,
        QEvent.Type.ShortcutOverride,
        QEvent.Type.TouchBegin,
        QEvent.Type.TouchUpdate,
        QEvent.Type.TouchEnd,
        QEvent.Type.ContextMenu,
        QEvent.Type.TabletPress,
        QEvent.Type.TabletMove,
        QEvent.Type.TabletRelease,
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsTransitionGuard")
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.hide()

    def event(self, event) -> bool:
        if event.type() in self._BLOCKED_EVENTS:
            event.accept()
            return True
        return super().event(event)
