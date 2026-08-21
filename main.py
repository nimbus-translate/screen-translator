"""屏幕截图翻译覆盖工具 - 入口。"""

from __future__ import annotations

import os
import sys


def _setup_runtime() -> None:
    # 让 Qt 使用 Per-Monitor V2 DPI 感知，保证坐标与 mss 物理像素一致
    try:
        import ctypes

        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4. The older
        # SetProcessDpiAwareness(2) API enables V1 only.
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        import ctypes

        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main() -> int:
    _setup_runtime()

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QSharedMemory
    from PySide6.QtWidgets import QMessageBox

    from app.application import Application
    from app.logger import setup_logging

    setup_logging()

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("ScreenTranslator")
    qt_app.setOrganizationName("ScreenTranslator")
    # 关掉主窗口后不退出（交给托盘接管）
    qt_app.setQuitOnLastWindowClosed(False)

    # 单实例锁：防止重复启动导致快捷键冲突 / exe 被占用
    single_instance = QSharedMemory("ScreenTranslator_SingleInstance_v1")
    if not single_instance.create(1):
        QMessageBox.information(None, "屏幕截图翻译", "程序已在运行，请从系统托盘图标操作。")
        return 0

    controller = Application(qt_app)
    controller.start()

    if os.environ.get("SCREEN_TRANSLATOR_SELFTEST") == "1":
        from PySide6.QtCore import QTimer

        QTimer.singleShot(1500, lambda: _run_selftest(qt_app, controller))

    # 冒烟测试钩子：SCREEN_TRANSLATOR_SMOKE=1 时自动退出，用于 CI/验证
    if os.environ.get("SCREEN_TRANSLATOR_SMOKE") == "1":
        from PySide6.QtCore import QTimer

        QTimer.singleShot(3000, qt_app.quit)

    return qt_app.exec()


def _run_selftest(qt_app, controller) -> None:
    """SCREEN_TRANSLATOR_SELFTEST=1 时：合成图片 -> OCR -> Mock 翻译，验证打包后的完整管线。"""
    import logging

    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from PySide6.QtCore import QEventLoop, QTimer

    from app.logger import get_logger
    from app.models import CaptureInfo
    from workers.translation_worker import PipelineTask

    log = get_logger("selftest")
    logging.getLogger("screen_translator.selftest").setLevel(logging.INFO)
    log.info("SELFTEST START")

    image = Image.new("RGB", (700, 220), (245, 245, 245))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 42)
    except Exception:
        font = ImageFont.load_default()
    draw.text((40, 60), "Hello Selftest 123", fill=(20, 20, 20), font=font)
    draw.text((40, 140), "Game Over", fill=(20, 20, 20), font=font)
    bgr = np.array(image)[:, :, ::-1].copy()

    capture = CaptureInfo(image=bgr, bbox=(0, 0, 700, 220), monitor_indices=[], mode="selftest")
    task = PipelineTask(
        capture=capture,
        ocr_engine=controller.ocr_engine,
        translator=controller.translator,
        config=controller.config,
    )
    loop = QEventLoop()
    state: dict = {}

    def on_result(payload) -> None:
        state["regions"] = payload["regions"]
        loop.quit()

    def on_error(message) -> None:
        state["error"] = message
        loop.quit()

    task.result.connect(on_result)
    task.error.connect(on_error)
    task.finished.connect(loop.quit)
    task.start()
    QTimer.singleShot(120000, loop.quit)
    loop.exec()
    task.wait(3000)

    regions = state.get("regions")
    if regions:
        log.info("SELFTEST OK: %d regions -> %s", len(regions), [r.translated_text for r in regions])
        qt_app.exit(0)
    else:
        log.error("SELFTEST FAILED: %s", state.get("error", "no result"))
        qt_app.exit(1)


if __name__ == "__main__":
    sys.exit(main())
