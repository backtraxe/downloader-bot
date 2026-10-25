# 仓库指南（供 AI 编码助手阅读）

交互式命令行媒体下载工具：从 stdin 循环读取链接（`q` 退出），把图片/视频下载到 `download/`。纯 Python 3 脚本，无构建步骤。README 是权威使用文档。

## 关键约束（先读）

- 没有 `pyproject.toml` / `setup.py`，不是可安装包。两个 sys.path 包：`core/`（下载核心）与 `telegram/`（机器人，仅依赖 core）。**core 绝不 import telegram**（`tests/test_module_boundary.py` 固化）。
- 入口一律 `python -m 模块路径`，根目录无 shim：`python -m core.downloader`、`python -m telegram.bot` 等。改功能直接改 `core/` 或 `telegram/` 对应模块。
- **脚本和测试必须在仓库根目录运行**（`cookies/`、`download/`、`config.json` 均按 CWD 解析）。
- `download/`、`cookies/`、`config.json` 已 gitignore：**绝不提交，也不要打印 cookie 内容**。

## 项目结构

```
core/
  downloader.py        统一入口 dispatch_url()，按站点路由
  result.py            DownloadResult / DownloadStatus 契约
  xhs_downloader.py    小红书（手写 __INITIAL_STATE__ 解析）
  douyin_downloader.py 抖音图文+视频（手写解析 + 签名 detail API）
  1024_downloader.py   通用静态网页抓取（数字开头，只能 import_module 导入）
  douyin_sign/         vendored 签名三件套（Apache-2.0，ruff 整体排除）
  http_client.py       curl_cffi 优先，不可用时降级系统 curl
  sites.py             URL→站点名、Cookie 加载（新增站点识别只改这里）
  utils.py             文件名/目录/Cookie 转换等共用函数
  config.py            config.json 加载（兼容旧 telegram.json）
  tools/               cookie_import / cookie_check / cookie_login / live_photo
telegram/
  bot.py               长轮询机器人：链接→下载→回传，caption 带 #作者 #站点
  upload.py            Bot API 上传：照片+视频混排相册、HEIC/大图转码、>50MB 视频压缩
tests/                 pytest 单元测试（全部离线）
```

## 构建、测试与运行

```bash
pip install -r requirements.txt
python -m pytest -q        # 单元测试，全部离线
ruff check .               # lint
ruff format .              # 格式化
python -m core.downloader  # 统一下载入口
```

CI（`.github/workflows/ci.yml`）在 Python 3.11–3.13 上跑 pytest + `ruff check` + `ruff format --check`，并额外安装 Pillow/pillow-heif。提交前至少跑 pytest 与 ruff。

## 路由与结果契约

`dispatch_url(url, force=False)` 按 `sites.get_site_name` 路由：

| 站点 | 后端 |
|---|---|
| xiaohongshu（含 xhslink.com/.cn、rednote.com） | `xhs_downloader.download_xhs_media`（需 Cookie） |
| 抖音 /note/ | `douyin_downloader.download_douyin_note`（需登录 Cookie） |
| 抖音 /video/ | `download_douyin_video`（签名 detail API，需登录 Cookie） |
| bilibili / youtube / instagram / twitter | yt-dlp |
| 其他任意站点 | `1024_downloader.extract_general_media` |

画质/去水印、重试、风控识别等实现细节写在各模块 docstring 和函数注释里，改代码前先看对应模块。后端统一返回 `core.result.DownloadResult`：`DOWNLOADED` / `PARTIAL` / `SKIPPED` / `FAILED` / `NO_MEDIA`。缺 Cookie、请求或解析失败必须是 `FAILED`，不能把静默失败当成功（`_as_result` 对 None 也判失败）。

## 易踩坑点

- **抖音**：短链先经 `resolve_short_link_with_retry` 展开再判 /note/ 或 /video/；yt-dlp 的 DouyinIE 已被 Argus 风控整拦，不要用于抖音作品下载（/user/ 等非作品链接才兜底 yt-dlp）。Cookie 失效表现为 jsvm 挑战页或 detail API 持续 200 空响应。
- **小红书**：链接必须带 `xsec_token`；裸 `/explore/<id>` 的 404 是风控而非笔记删除，别误报。
- **UA 固定对齐指纹**：xhs/douyin 用 `_UA150` + chrome150，1024 用 `_UA110` + chrome110。不要改回随机 UA（会 302 到 /login 或触发验证码页）。
- **通用抓取**：扫描阶段按文件名剔除装饰图，落盘后按短边 <300px 再过滤；`.skip` 标记避免重跑时重复下载图标。
- **Termux**：`setup_termux.sh` 一键配置；`http_client.py` 的系统 curl 降级路径与短链预展开是为此设计的，改动时别破坏降级语义。

## 代码约定

- Python 3，源文件 UTF-8。注释和用户可见消息用中文。
- 4 空格缩进；函数 `snake_case`；常量 `UPPER_CASE`。格式由 `ruff format` 统一。
- 站点识别只走 `sites.get_site_name`；下载目录只经 `utils.build_download_dir`。
- 文件名冲突用 `unique_path` 原子去重，绝不覆盖已有文件；重复链接已存在即跳过。`--force` 逐层下传（`.part` + `os.replace` 原子替换，yt-dlp 用 `overwrites`）。
- 并发维持 `max_workers=5`。
- 新增公共函数要附带离线测试（参数化写法见 `tests/test_sites.py`）。

## Cookie

- 格式：yt-dlp 要 Netscape 文件；`xhs_downloader` 要 `Cookie:` 请求头整段；`douyin_downloader` 两种都能自动识别。
- 门禁：抖音需 `UIFID`/`UIFID_TEMP` + 有效 `ttwid`/`sessionid` 其一；小红书核心键为 `a1`（约 52 位）与 `webId`/`web_session`。
- `core/tools/cookie_import.py` 从 stdin 导入（JSON / 请求头 / Netscape 自动识别）并按站点写文件；Termux 用法：`termux-clipboard-get | python -m core.tools.cookie_import <站点>`。
- `core/tools/cookie_check.py` 纯离线体检，有明确缺口时退出码为 1。
- Pillow / pillow-heif 为可选依赖：缺 Pillow 时装饰过滤回退体积阈值，缺 pillow-heif 时 live_photo 不能转 HEIC。

## 提交

约定式前缀 + 简短中文摘要（`feat`/`fix`/`docs`/`style`），主题行约 50 字内，如 `fix: xhslink download 503 failed`。PR 说明改了什么、为什么，并确认 `python -m pytest -q` 通过。
