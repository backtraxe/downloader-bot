# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo status (read first)

The README ("Telegram bot to download images or videos") is **stale**. The most recent commit (`47f7c2f`) deleted the entire Telegram bot (`main.py`, `config.py`, `src/`) and replaced it with two standalone interactive CLI scripts. There is currently no bot, no tests, no lint config, and no build system — only `1024_downloader.py` and `xhs_downloader.py`. Treat the two scripts as the source of truth, not the README.

## Running

三个交互式脚本(读 URL 从 stdin 直到 `q`),无 CLI 参数,无构建步骤:

```bash
pip install -r requirements.txt
python downloader.py       # 多站点统一入口(yt-dlp,推荐)
python xhs_downloader.py  # 小红书(手写解析)
python 1024_downloader.py  # 通用网页媒体抓取
```

测试:`pytest -q`(纯函数单测,无网络)。

## The downloaders

**`downloader.py` — 多站点统一入口(推荐)。** 底层用 `yt-dlp` 接管,按 URL 自动识别站点(youtube/bilibili/douyin/instagram/twitter)。流程:`get_site_name(url)` → `cookie_file_for(site)` → 两段式下载:先 `extract_info(url, download=False)` 探一次拿作者(`resolve_uploader` 从 info dict 取 `uploader` 并 `sanitize_filename` 清洗),再 `build_ydl_opts(site, author=...)`(`cookiefile` 指向 `cookies/<site>.txt`,`outtmpl` = `download/<site>[/<author>]/<title>.<ext>`,有作者加一层、无则退化)→ `YoutubeDL.extract_info(url, download=True)`。代价是多一次请求;收益是作者缺失自然退化为无作者层(不产生 `NA/` 或 `unknown/`)。yt-dlp 静默(`quiet`/`noprogress`),进度经 `progress_hooks` 转入 `logging`,避免与 logger 输出交错。错误经 `diagnose_error` 翻译成"需登录/Cookie 失效"或原消息。**这是新增站点时的首选入口**——yt-dlp 已覆盖上千站点,通常无需写代码。

**`sites.py` — 站点识别与 Cookie 管理(共用)。** `get_site_name(url)` 把域名映射成统一小写站点名(twitter 含 x.com/t.co),`SITE_REQUIRE_COOKIE` 记录各站点是否建议填 cookie,`load_cookie_for_url`/`cookie_file_for` 按 `cookies/<site>.txt` 加载。被 `downloader.py` 和 `xhs_downloader.py` 共用——**新增站点识别只改这一处**。

**`xhs_downloader.py` — 小红书笔记下载。** 手写解析 `window.__INITIAL_STATE__`(比 yt-dlp 对小红书更可控),流程见下。`get_site_name`/`load_cookie_for_url` 从 `sites.py` 导入,本地不再定义。

**`1024_downloader.py` — 通用静态网站媒体抓取。**

**`1024_downloader.py` — generic static-site media scraper.** Uses `curl_cffi` (not stdlib `requests`) specifically for `impersonate="chrome110"` TLS fingerprinting to bypass anti-hotlink/CDN blocks. Deep-scrapes media from `<img>/<source>/<video>` lazy-load attributes (`data-src`, `data-original`, `data-lazy-src`, `ess-data`, etc.), `<a href>` direct links, and inline `style` `url()` background images. Downloads concurrently (`ThreadPoolExecutor(max_workers=5)`) with per-request `Referer` set to the page URL (the actual anti-hotlink trick) and a random 0.5–1.5s delay to avoid IP bans. Site name via shared `get_site_name(url)` (normalizes any domain to its last two labels, e.g. `www.example.com` → `example.com`). Output: `download/<site>/<title>/` (constructed via `build_download_dir`).

**`xhs_downloader.py` — Xiaohongshu note downloader.** Uses stdlib `requests` (different HTTP lib from the other script). Fetches the note page, regex-extracts `window.__INITIAL_STATE__={...}` JSON, `replace('undefined','null')`, then walks `note.noteDetailMap.<id>.note` for `imageList[*].urlDefault` and `video.media.stream.h264[0].masterUrl`. Author via `extract_xhs_author(note)` from `note.user.nickname`/`nickName` (双 key 兼容). Detects risk-control/captcha pages ("验证码", "访问过于频繁") and deleted/404 notes (`/404` path, `errorCode=-510001`, or "你访问的页面不见了" title — via `detect_note_not_found`). Output: `download/xiaohongshu/[<author>/]<title>/` (constructed via `build_download_dir`,有作者加一层、无则退化).

### Cookies convention (`xhs_downloader.py`)

Cookies are loaded per-site from `cookies/<site_name>.txt`, where `site_name` comes from `get_site_name(url)` (maps `xiaohongshu.com`/`xhslink.com` → `xiaohongshu`, `bilibili.com`/`b23.tv` → `bilibili`, etc.). Missing/empty cookie files are auto-created empty with a prompt to fill them. Only `xiaohongshu` has a parser implemented; `bilibili`/`douyin` resolve to a site name but print "not yet supported". Adding a new site = add a branch in `get_site_name` plus a `download_<site>_media` function and a dispatch case in `__main__`.

## Conventions

- `download/` (output) and `cookies/` (secrets) are gitignored — never commit either.
- Both scripts use `fake_useragent` for rotating UAs, with a hardcoded Chrome 120 UA fallback if init fails.
- Comments are in Chinese; match this when editing existing code.
- Concurrency is fixed at `max_workers=5` in both scripts — deliberately conservative to avoid IP bans.
