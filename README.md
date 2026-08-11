# 屏幕截图翻译覆盖工具（ScreenTranslator）

> A Windows desktop screen translator: capture any region, run OCR, translate it, and place the result back over the original content.

## Why ScreenTranslator

ScreenTranslator is built for text that cannot be copied: games, comics, videos, images, legacy software, and web content. It keeps the workflow short: **capture → recognize → translate → overlay**.

### Share this project

> ScreenTranslator is an open-source Windows screen translation tool with region, full-screen and active-window capture; PaddleOCR; multiple translation providers; editable overlay results; global hotkeys; and DPI-aware multi-monitor support. Contributions and real-world feedback are welcome.

## Open-source

- License: [MIT](LICENSE)
- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Please never commit API keys, local configuration files, screenshots, or model caches.

Windows 10/11 桌面工具：按快捷键截图（全屏 / 当前窗口 / 框选）→ PaddleOCR 识别 → 自动翻译 → 用透明置顶覆盖层把译文盖在原文位置。适用于游戏、漫画、网页、图片、视频字幕等无法直接复制文字的场景。

## 功能

- 三种截图方式：全屏、当前窗口、鼠标框选（半透明遮罩 + 十字光标，Esc 取消）
- PaddleOCR 3.x 识别，结构化输出（文本、矩形、置信度、方向、文本行），支持置信度过滤与相邻文本块合并
- 统一 Translator 接口：内置 Mock（离线可测）、Google 免费（无 Key、并发快译）、MyMemory、OpenAI 兼容接口、DeepL、Google Cloud Translation v2
- 批量翻译、失败重试（指数退避）、超时、请求限速、JSON 结果缓存
- 翻译前保护数字 / URL / 邮箱 / 占位符 / 变量，避免被翻译破坏
- 透明置顶覆盖层：鼠标穿透、自动换行、自动缩字号、按背景亮度选择深浅文字、半透明底、可整体隐藏 / 显示
- 编辑模式：进入后可拖动单个译文框，Esc 退出并关闭覆盖层
- 全局快捷键（pynput），可在设置中修改并检测冲突
- 系统托盘（框选 / 全屏 / 窗口 / 隐藏显示 / 编辑模式 / 刷新 / 设置 / 退出）
- 正确感知 Per-Monitor DPI，多显示器（含负坐标）坐标一致
- OCR / 翻译 / 图像处理全部在后台 QThread，不卡 UI；重复触发自动取消过期任务
- 配置存 JSON；API Key 优先读环境变量，日志自动脱敏；默认不保存截图与历史

## 项目结构

```text
screen_translator/
├── main.py                        # 入口：DPI 感知 + QApplication + 控制器
├── requirements.txt
├── config.example.json
├── build.spec                     # PyInstaller 打包配置
├── app/
│   ├── application.py             # 顶层控制器（串联所有模块）
│   ├── config.py                  # JSON 配置（默认值合并、原子保存、脱敏）
│   ├── logger.py                  # 日志 + API Key 脱敏
│   ├── models.py                  # TextRegion / CaptureInfo 数据模型
│   └── hotkeys.py                 # pynput 全局快捷键 + 冲突检测
├── ui/
│   ├── main_window.py             # 主界面
│   ├── settings_dialog.py         # 设置对话框（5 个页签）
│   ├── selection_overlay.py       # 框选遮罩
│   ├── translation_overlay.py     # 透明覆盖窗口
│   ├── overlay_manager.py         # 按显示器分发覆盖窗口，DPI 坐标换算
│   └── tray_icon.py               # 系统托盘
├── services/
│   ├── screenshot_service.py      # mss 截图（物理像素）
│   ├── window_capture_service.py  # Win32 前台窗口矩形
│   ├── ocr/
│   │   ├── base.py                # OCREngine 抽象接口 + 工厂
│   │   ├── paddle_ocr.py          # PaddleOCR 3.x（兼容 2.x）
│   │   ├── windows_ocr.py         # Windows.Media.Ocr 兜底（可选 winocr）
│   │   └── null_ocr.py            # 引擎不可用时的空实现
│   └── translation/
│       ├── base.py                # Translator 抽象 + 缓存/重试/限速
│       ├── cache.py               # JSON 翻译缓存
│       ├── mock_translator.py     # 离线 Mock
│       ├── openai_translator.py   # OpenAI Chat Completions
│       ├── deepl_translator.py    # DeepL API v2
│       ├── google_free_translator.py  # Google 免费网页端点（无 Key，默认）
│       ├── google_translator.py   # Google Translation v2
│       └── factory.py             # 翻译器工厂
├── workers/
│   └── translation_worker.py      # 截图->OCR->合并->翻译->覆盖层 QThread 管线
├── utils/
│   ├── dpi_utils.py               # Per-Monitor DPI / 多显示器坐标换算
│   ├── image_utils.py             # 亮度、缩放、格式转换
│   ├── layout_utils.py            # 文本行分组合并、边界钳制
│   ├── text_utils.py              # 占位符保护 / 还原
│   └── language_utils.py          # 语言代码映射（各服务差异）
└── tests/                         # 基础单元测试
```

## 安装

要求 Python 3.11+（推荐 3.12）。

```powershell
cd screen_translator
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

> PaddleOCR 首次识别时会自动下载 PP-OCRv5 轻量模型（约几十 MB），需要联网；下载一次后本地缓存。

## 运行

```powershell
python main.py
```

首次启动后配置与缓存保存在 `%LOCALAPPDATA%\ScreenTranslator\`（也可用环境变量 `SCREEN_TRANSLATOR_CONFIG` 指向自定义配置文件）。

默认快捷键：

| 快捷键 | 动作 |
| --- | --- |
| Ctrl+Shift+A | 框选区域并翻译 |
| Ctrl+Shift+F | 全屏翻译 |
| Ctrl+Shift+W | 翻译当前窗口 |
| Ctrl+Shift+H | 隐藏 / 显示覆盖层 |
| Ctrl+Shift+R | 重新识别翻译上一次截图 |

默认使用 **Google 免费翻译**（`translate.googleapis.com` gtx 端点，免注册、免 Key，单块并发请求，25 个文本块约 1~2 秒，无显式配额）。检测到 `OPENAI_API_KEY` / `DEEPL_API_KEY` / `GOOGLE_TRANSLATE_API_KEY` 时应用会自动切换到对应真实服务。在“设置 → 翻译”中可手动指定服务（手动选择后不再自动切换）。MyMemory 作为无 Key 后备可选，但匿名限速约 2 请求/秒且易 429。

> MyMemory 英文→中文质量可用；中文→英文等反向翻译不稳定，需要高质量翻译请配置 OpenAI/DeepL/Google。

## 技术要点

### DPI 与多显示器

- 进程启动时通过 `SetProcessDpiAwareness(2)`（Per-Monitor V2）感知缩放，配合 Qt6 的 per-screen `devicePixelRatio`。
- mss 截图与 Win32 `GetWindowRect` 都是物理像素；Qt 坐标是逻辑像素。
- 每个显示器维护 `(物理矩形, 逻辑原点, dpr)` 映射，所有换算只在该显示器局部做偏移，再换算回全局；覆盖窗口按显示器分别创建，避免混合 DPI 时整体错位。详见 `utils/dpi_utils.py`。

### 鼠标穿透

覆盖窗口使用 `FramelessWindowHint | WindowStaysOnTopHint | Tool | WindowTransparentForInput` + `WA_TransparentForMouseEvents`，鼠标点击直接穿透到下层程序。编辑模式下临时关闭穿透并允许拖动译文框。

### OCR 坐标映射

OCR 输出是截图图像内的像素框，管线把框偏移到物理全局坐标：`region.x = capture.bbox.left + box.x`。若图像超长边 > 4096 会先缩放识别，再把坐标除以缩放比例还原。

### 翻译文本长度变化

覆盖层根据译文长度和文本框尺寸自动换行并逐级缩小字号（可设最小字号），背景半透明底保证可读性；若译文过长则按区域高度收缩显示。

### 后台线程

每次任务创建独立 `PipelineTask(QThread)`，通过 Qt 信号（status/error/result/finished）与主线程通信；新任务会先取消旧任务，`_stop` 标志在管线各阶段检查，避免任务堆积。

## API Key 配置

优先读取环境变量，其次读配置文件（`config.json` / `config.example.json`），不会硬编码进程序，日志中自动脱敏。

```powershell
# PowerShell 临时设置
$env:OPENAI_API_KEY = "sk-..."
$env:DEEPL_API_KEY = "xxx"
$env:GOOGLE_TRANSLATE_API_KEY = "xxx"
python main.py
```

或永久设置：

```powershell
setx OPENAI_API_KEY "sk-..."
```

也可以在“设置 → 翻译”中直接填写（会写入 `%LOCALAPPDATA%\ScreenTranslator\config.json`）。

## 测试

```powershell
python -m pytest tests -v
```

覆盖：文本合并、DPI 换算、翻译缓存、配置读写、失败重试、占位符保护、覆盖层边界钳制。

## PyInstaller 打包

```powershell
python -m pip install pyinstaller
pyinstaller build.spec --noconfirm
```

产物在 `dist\ScreenTranslator.exe`。Paddle 模型不会打进 exe，首次运行时自动下载到用户目录。

## 常见问题

### 识别结果为空

1. 首次运行是否已联网下载模型；2. 降低“OCR → 最低置信度”；3. 在设置中切换到 Windows OCR（需系统已装语言包）；4. 检查目标区域是否真的含文字。

### 开启“文字方向识别”后识别卡死

方向识别模型（PP-LCNet_x1_0_textline_ori）在部分 paddle CPU 构建上会挂起。在设置中关闭“启用文字方向识别”即可；水平文字识别不受影响。

### 覆盖层位置偏了 / 缩放显示器上错位

确认系统“显示设置”中的缩放已生效；多显示器混用不同缩放时，尽量把目标窗口放到同一显示器内框选。

### 快捷键没反应

可能被其他软件占用（如输入法/录屏工具）。在“设置 → 快捷键”里换一组并保存；注册失败时主界面状态栏会提示。

### 翻译提示没有 API Key

按上文设置环境变量，或到设置里填写；Mock 翻译器不需要任何 Key。

### 翻译额度不足 / 429

在线服务返回错误时会自动重试并保留原文显示，请检查账号额度；可通过“请求间隔”降低调用频率。

### 打包后 OCR 不可用

Paddle 依赖较大且带动态库，建议用 `pyinstaller build.spec` 完整打包；若仍有问题，可在打出的 exe 同目录放 `paddle` 相关 DLL，或改用 Windows OCR 引擎。

### 识别时报 `ConvertPirAttribute2RuntimeAttribute not support`

paddlepaddle 3.3.x 在 Windows CPU 上的已知 oneDNN bug。请把 paddlepaddle 固定到 3.2.x：

```powershell
python -m pip install "paddlepaddle>=3.2.2,<3.3.0"
```

### Windows OCR 提示需要语言包

Windows.Media.Ocr 依赖系统 OCR 语言包。可在管理员 PowerShell 中安装对应语言，例如：

```powershell
Add-WindowsCapability -Online -Name "Language.OCR~~~en-US~0.0.1.0"
```

## 隐私说明

- 默认不保存截图；OCR / 翻译完成后图片数据即释放。
- “通用 → 保存截图与识别历史”默认关闭，开启后才写入指定目录。
- 使用在线翻译时，识别出的文字会发送给对应第三方服务，界面与文档均有提示。
- 日志不记录识别文本、截图或 API Key。

## 后续优化建议

- 截图遮罩增加放大镜 / 精细选区；区域截图支持跨显示器混合 DPI 分段处理
- 翻译服务支持多 Key 轮换、按文本长度分片、结果流式返回
- 覆盖层支持逐块手动微调并持久化位置记忆
- OCR 增加版面分析（表格 / 竖排 / 印章）与二次校对
- 单实例锁、自动更新、开机自启的托盘开关
