# Repository Guidelines

A collection of interactive CLI media downloaders. Scripts read URLs from stdin until `q` and write media under `download/`.

## Repo status (read first)

The README ("Telegram bot to download images or videos") is **stale**. The Telegram bot (`main.py`, `config.py`, `src/`) was deleted; the source of truth is now the standalone CLI scripts below, not the README.

## Project Structure

```
downloader.py        # Unified entry — dispatch_url() routes by site name to the right backend
xhs_downloader.py    # Xiaohongshu note parser (hand-rolled __INITIAL_STATE__ extraction)
1024_downloader.py   # Generic static-site scraper (curl_cffi TLS impersonation)
http_client.py       # Thin curl_cffi wrapper (Session, CurlResponse) used by 1024_downloader
sites.py             # Shared URL→site mapping and cookie loading (add sites here only)
utils.py             # Logging setup, filename sanitizing, path building, cookie conversion
tests/               # pytest unit tests (no network), mirroring the module names
cookies/             # Per-site cookie files, e.g. cookies/bilibili.txt (gitignored)
download/            # Output directory, organized download/<site>[/<author>]/<title>.<ext> (gitignored)
```

`downloader.py` is the single recommended entry point. Its `dispatch_url()` function
uses `sites.get_site_name` to route each URL: xiaohongshu → `xhs_downloader`,
yt-dlp sites (bilibili/douyin/youtube/instagram/twitter) → `download_url`,
everything else → `1024_downloader.extract_general_media`. The other two scripts
remain independently runnable for debugging.

## Build, Test, and Development

No build step. Install dependencies and run scripts directly:

```bash
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
pytest -q                                            # unit tests, no network
python downloader.py                                 # unified entry, auto-routes by URL
```

## The Downloaders

**`downloader.py` — 多站点统一入口（推荐）。** 底层用 `yt-dlp` 接管，按 URL 自动识别站点。`dispatch_url(url)` 路由：xiaohongshu → `xhs_downloader`，yt-dlp 站点（bilibili/douyin/youtube/instagram/twitter）→ `download_url`，其余 → `1024_downloader.extract_general_media`。

`download_url` 采用两段式下载：先 `extract_info(url, download=False)` 探一次拿作者（`resolve_uploader` 从 info dict 取 `uploader` 并 `sanitize_filename` 清洗），再 `build_ydl_opts(site, author=...)`（`cookiefile` 指向 `cookies/<site>.txt`，`outtmpl` = `download/<site>[/<author>]/<title>.<ext>`，有作者加一层、无则退化）→ `YoutubeDL.extract_info(url, download=True)`。代价是多一次请求；收益是作者缺失自然退化为无作者层（不产生 `NA/` 或 `unknown/`）。yt-dlp 静默（`quiet`/`noprogress`），进度经 `progress_hooks` 转入 `logging`，避免与 logger 输出交错。错误经 `diagnose_error` 翻译成"需登录/Cookie 失效"或原消息。**这是新增站点时的首选入口**——yt-dlp 已覆盖上千站点，通常无需写代码。

**`sites.py` — 站点识别与 Cookie 管理（共用）。** `get_site_name(url)` 把域名映射成统一小写站点名（twitter 含 x.com/t.co），`SITE_REQUIRE_COOKIE` 记录各站点是否建议填 cookie，`load_cookie_for_url`/`cookie_file_for` 按 `cookies/<site>.txt` 加载。被 `downloader.py` 和 `xhs_downloader.py` 共用——**新增站点识别只改这一处**。

**`xhs_downloader.py` — 小红书笔记下载。** 手写解析 `window.__INITIAL_STATE__`（比 yt-dlp 对小红书更可控）。`get_site_name`/`load_cookie_for_url` 从 `sites.py` 导入，本地不再定义。用 stdlib `requests`（与 `1024_downloader` 的 `curl_cffi` 不同）。取页后正则提取 `window.__INITIAL_STATE__={...}` JSON，`replace('undefined','null')`，再遍历 `note.noteDetailMap.<id>.note` 取 `imageList[*].urlDefault` 与 `video.media.stream.h264[0].masterUrl`。作者经 `extract_xhs_author(note)` 从 `note.user.nickname`/`nickName`（双 key 兼容）。检测风控/验证码页（"验证码"、"访问过于频繁"）与已删除/404 笔记（`/404` 路径、`errorCode=-510001`、或"你访问的页面不见"title，由 `detect_note_not_found` 判断）。输出 `download/xiaohongshu/[<author>/]<title>/`。

**`1024_downloader.py` — 通用静态网站媒体抓取。** 用 `curl_cffi`（非 stdlib `requests`），`impersonate="chrome110"` 做 TLS 指纹绕过防盗链/CDN。深抓 `<img>/<source>/<video>` 懒加载属性（`data-src`、`data-original`、`data-lazy-src`、`ess-data` 等）、`<a href>` 直链与内联 `style` 的 `url()` 背景图。并发下载（`ThreadPoolExecutor(max_workers=5)`），每个请求 `Referer` 设为页面 URL（真正的防盗链手段），并加 0.5–1.5s 随机延迟避免封 IP。站点名经共享 `get_site_name(url)`（未知域名归一为最后两段，如 `www.example.com` → `example.com`）。输出 `download/<site>/<title>/`（经 `build_download_dir` 构造）。

**`http_client.py` — curl_cffi 薄封装。** 提供 `Session` 与 `CurlResponse`（含 `raise_for_status`/`iter_content`/`json`），被 `1024_downloader` 复用，统一 impersonate 与超时参数。

## Coding Style & Conventions

Python 3, UTF-8 source files with `# -*- coding: utf-8 -*-` headers. 4-space indentation. Comments and user-facing messages are in Chinese — match this when editing existing code. Use `fake_useragent` for UA rotation with a hardcoded Chrome fallback. Keep concurrency at `max_workers=5` to avoid IP bans. Functions are `snake_case`; module-level constants are `UPPER_CASE`. Always route site recognition through `sites.get_site_name` so both scripts stay consistent.

## Testing

`pytest` is the only framework. Tests live in `tests/test_<module>.py` and cover pure functions only (`get_site_name`, `cookie_file_for`, `sanitize_filename`, `build_download_dir`, state normalization, error detection). Tests must not hit the network. Use parametrized cases as in `tests/test_sites.py` when adding URL or parsing branches. Run `pytest -q` before pushing; new public functions should ship with a test.

## Cookies & Security

Cookies are per-site plain-text files under `cookies/<site>.txt`. Both `download/` and `cookies/` are gitignored — never commit either. If a cookie file is missing or empty, scripts auto-create it and prompt the user to fill it in. Do not log cookie contents. Only `xiaohongshu` has a full cookie parser; `bilibili`/`douyin` resolve to a site name but print "not yet supported".

## Commits & Pull Requests

Follow the conventional-prefix style used throughout the history: `feat:`, `fix:`, `docs:` followed by a short Chinese summary, e.g. `fix: xhslink download 503 failed` or `feat: 下载目录按站点与作者归类`. Keep the subject line under ~50 chars. PRs should describe what changed and why, link any related issue, and confirm `pytest -q` passes. Screenshots are not required for CLI-only changes.
