# 屏幕截图翻译覆盖工具（ScreenTranslator）

> A lightweight Windows desktop translator: capture any region, recognize text with Windows OCR or optional PaddleOCR, translate it, and place the result back over the original content.

> **v0.2.5-beta:** this prerelease replaces the incorrect v0.2.5 code. It fixes OCR text-block coverage, paragraph translation, natural font sizing, color matching, overlay layout, translation latency, and failure handling. Windows OCR remains the lightweight default.

## Why ScreenTranslator

ScreenTranslator is built for text that cannot be copied: games, comics, videos, images, legacy software, and web content. It keeps the workflow short: **capture → recognize → translate → overlay**.

### Share this project

> ScreenTranslator is an open-source Windows screen translation tool with region, full-screen and active-window capture; Windows OCR plus optional PaddleOCR; multiple translation providers; editable overlay results; global hotkeys; and DPI-aware multi-monitor support. Contributions and real-world feedback are welcome.

## Open-source

- License: [MIT](LICENSE)
- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Please never commit API keys, local configuration files, screenshots, or model caches.

Windows 10/11 桌面工具：按快捷键截图（全屏 / 当前窗口 / 框选）→ Windows OCR 或可选 PaddleOCR 识别 → 自动翻译 → 用透明置顶覆盖层把译文盖在原文位置。适用于游戏、漫画、网页、图片、视频字幕等无法直接复制文字的场景。

## 功能

- 三种截图方式：全屏、当前窗口、鼠标框选；当前窗口优先使用 Win32 `PrintWindow` 原生捕获，失败才安全回退到经过校验的可见区域，绝不退化成整桌面截图
- 框选支持负坐标多显示器、右下开区间精确坐标、Esc 取消与跨缩放安全检查
- 轻量版默认使用 Windows.Media.Ocr，不内置 Paddle、PaddleX、OpenCV 与模型
- 可在“设置 → OCR”按需下载 PaddleOCR 独立组件；显示实时进度，校验 HTTPS manifest、协议版本、文件大小、SHA-256 与 Authenticode 后原子安装
- OCR 统一输出文本、矩形、置信度、方向和文本行，支持置信度过滤与相邻文本块合并
- 统一 Translator 接口：内置 Mock（离线可测）、Google 免费（无 Key、并发快译）、MyMemory、OpenAI 兼容接口、DeepL、Google Cloud Translation v2
- 批量翻译、失败重试（指数退避）、超时、请求限速、JSON 结果缓存
- 翻译前保护数字 / URL / 邮箱 / 占位符 / 变量，避免被翻译破坏
- 透明置顶覆盖层：鼠标穿透、自动换行、自动缩字号、按背景亮度选择深浅文字、半透明底、可整体隐藏 / 显示
- 编辑模式：进入后可拖动单个译文框，Esc 退出并关闭覆盖层
- 全局快捷键（pynput），可在设置中修改并检测冲突
- 系统托盘（框选 / 全屏 / 窗口 / 隐藏显示 / 编辑模式 / 刷新 / 设置 / 退出）
- 正确感知 Per-Monitor V2 DPI，多显示器（含负坐标）坐标一致
- 统一动效节奏：捕获卡短线确认、主题色选框收束、进度波点与译文分段揭示；支持 reduced / eco 动效策略
- OCR / 翻译 / 图像处理全部在后台 QThread，不卡 UI；处理期间阻止重入并使用配置快照
- 配置存 JSON；API Key 优先读环境变量，日志自动脱敏；默认不保存截图与历史
- 设置内可检查并下载更新；安装前必须通过发布方 SHA-256 与 Authenticode，正式版还会核对当前程序的签名主体
- 一键导出诊断 ZIP，严格只包含脱敏配置、运行环境和最近日志，不包含截图、翻译历史、模型或 API Key

## 项目结构

```text
screen_translator/
├── main.py                        # 入口：DPI 感知 + QApplication + 控制器
├── requirements-core.txt          # 轻量运行依赖（Windows OCR）
├── requirements-paddle.txt        # 本地完整 Paddle 开发依赖
├── requirements-dev.txt           # 测试与打包依赖
├── config.example.json
├── build-lite.spec                # 轻量版 PyInstaller 配置
├── build.spec                     # 传统完整 PyInstaller 配置
├── paddle_component.spec          # 独立 PaddleOCR worker 配置
├── installer/                     # Inno Setup 轻量安装器
├── scripts/                       # 轻量构建、组件构建与签名脚本
├── app/
│   ├── application.py             # 顶层控制器（串联所有模块）
│   ├── config.py                  # JSON 配置（默认值合并、原子保存、脱敏）
│   ├── logger.py                  # 日志 + API Key 脱敏
│   ├── models.py                  # TextRegion / CaptureInfo 数据模型
│   ├── hotkeys.py                 # pynput 全局快捷键 + 冲突检测
│   └── version.py                 # 应用版本与发行仓库
├── ui/
│   ├── main_window.py             # 主界面
│   ├── settings_dialog.py         # 设置页面（6 个页签）
│   ├── selection_overlay.py       # 框选遮罩
│   ├── translation_overlay.py     # 透明覆盖窗口
│   ├── overlay_manager.py         # 按显示器分发覆盖窗口，DPI 坐标换算
│   └── tray_icon.py               # 系统托盘
├── services/
│   ├── screenshot_service.py      # mss 截图（物理像素）
│   ├── diagnostics.py             # 脱敏诊断包导出
│   ├── update_service.py          # GitHub Release 检查与校验下载
│   ├── window_capture_service.py  # Win32 前台窗口矩形
│   ├── ocr/
│   │   ├── base.py                # OCREngine 抽象接口 + 工厂
│   │   ├── paddle_ocr.py          # 本地或独立组件 PaddleOCR 适配
│   │   ├── component_manager.py   # 可选组件下载、校验与原子激活
│   │   ├── windows_ocr.py         # Windows.Media.Ocr 默认后端
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

只运行轻量版功能：

```powershell
cd screen_translator
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-core.txt
```

需要在源码环境直接运行 PaddleOCR：

```powershell
python -m pip install -r requirements-paddle.txt
```

开发和测试使用 `requirements-dev.txt`。`requirements.txt` 保留为兼容入口，会安装完整开发依赖。

> 轻量版首次启动不下载模型。Windows OCR 默认使用“自动检测”，依次尝试已安装的中文和英文 OCR 语言包；只有用户在“设置 → OCR”选择下载高精度组件时，才会下载 PaddleOCR 与模型并显示进度。

## 运行

```powershell
python main.py
```

首次启动后配置、日志、更新包与可选组件保存在 `%LOCALAPPDATA%\ScreenTranslator\`（也可用环境变量 `SCREEN_TRANSLATOR_CONFIG` 指向自定义配置文件）。

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

### 下载高精度 OCR

打开“设置 → OCR → 可选高精度组件”，点击“下载 PaddleOCR 组件”。下载、校验和安装都在后台进行；取消或异常不会覆盖已有可用组件。保存设置后切换到 PaddleOCR。

### 更新与诊断

- “设置 → 通用 → 软件更新”可检查 GitHub Releases；下载后先验证发布方 SHA-256，用户确认安装后会在执行前再次验证 Authenticode，任一步失败都不会启动安装包。
- “设置 → 通用 → 诊断与支持”可导出 ZIP。压缩包采用白名单，不会遍历应用数据目录。

## 技术要点

### DPI 与多显示器

- 进程启动时优先通过 `SetProcessDpiAwarenessContext(-4)` 启用 Per-Monitor V2，失败后再逐级回退，配合 Qt6 的 per-screen `devicePixelRatio`。
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

## 构建轻量安装包

```powershell
python -m pip install -r requirements-core.txt pyinstaller
.\scripts\build_lite.ps1 -Version 0.2.5-beta
```

脚本先用 `build-lite.spec` 生成 `dist\ScreenTranslator-Lite.exe`，再调用 Inno Setup 6 生成安装器。构建会拒绝大于或等于 200,000,000 bytes 的安装包。轻量归档明确排除 Paddle、PaddleX、OpenCV、SciPy、scikit-learn 和 Torch。

只构建 EXE：

```powershell
.\scripts\build_lite.ps1 -Version 0.2.5-beta -SkipInstaller
```

传统完整包仍可用 `python -m PyInstaller build.spec --noconfirm` 构建，但不作为 v0.2.5-beta 的默认下载。

### 代码签名与发布

标签发布由 `.github/workflows/release-windows.yml` 完成。仓库必须配置以下 GitHub Actions secrets：

| Secret | 用途 |
| --- | --- |
| `WINDOWS_CERTIFICATE_PFX_BASE64` | Base64 编码的代码签名 PFX |
| `WINDOWS_CERTIFICATE_PASSWORD` | PFX 密码 |

流水线会依次签名主 EXE、安装器、卸载程序与 PaddleOCR worker，调用时间戳服务、验证 Authenticode、再次检查体积，并生成同名 `.sha256`。缺少签名凭据时发布直接失败，不会产出冒充“已签名”的文件。

## 常见问题

### 识别结果为空

1. 确认 Windows 已安装目标语言的 OCR 包；2. 降低“OCR → 最低置信度”；3. 对复杂字体可按需下载并切换 PaddleOCR；4. 检查目标区域是否真的含文字。

### 开启“文字方向识别”后识别卡死

方向识别模型（PP-LCNet_x1_0_textline_ori）在部分 paddle CPU 构建上会挂起。在设置中关闭“启用文字方向识别”即可；水平文字识别不受影响。

### 覆盖层位置偏了 / 缩放显示器上错位

确认系统“显示设置”中的缩放已生效。跨不同缩放比例显示器的框选会被明确拒绝，请在单个屏幕内完成框选，避免错误扩大截图范围。

### 快捷键没反应

可能被其他软件占用（如输入法/录屏工具）。在“设置 → 快捷键”里换一组并保存；注册失败时主界面状态栏会提示。

### 翻译提示没有 API Key

按上文设置环境变量，或到设置里填写；Mock 翻译器不需要任何 Key。

### 翻译额度不足 / 429

在线服务返回错误时会自动重试并保留原文显示，请检查账号额度；可通过“请求间隔”降低调用频率。

### 打包后 OCR 不可用

轻量版先检查 Windows OCR 语言包；PaddleOCR 必须通过设置页安装已发布的独立组件。源码完整包则使用 `build.spec`，不要把零散 Paddle DLL 手工塞进轻量版目录。

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

## 路线图

- **v0.2.5-beta：覆盖层修复版。** 修复 OCR 文本块覆盖、段落翻译、自然字号与颜色匹配，并改进翻译速度和失败恢复。
- **v0.3：连续区域翻译。** 框选一次后检测画面变化，只在字幕变化时 OCR，并用前文作为翻译上下文。
- **v0.4：场景预设与术语表。** 游戏字幕、视觉小说、漫画竖排、视频字幕等受控预设，以及术语记忆。
- **v1.0：签名、更新与稳定性。** 完整发布治理、长期运行可靠性与自然覆盖效果。

当前不扩展手机端、macOS/Linux、浏览器插件、云账号或无关 AI 聊天功能。
