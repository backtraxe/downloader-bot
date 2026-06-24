# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo status (read first)

The README ("Telegram bot to download images or videos") is **stale**. The most recent commit (`47f7c2f`) deleted the entire Telegram bot (`main.py`, `config.py`, `src/`) and replaced it with two standalone interactive CLI scripts. There is currently no bot, no tests, no lint config, and no build system — only `1024_downloader.py` and `xhs_downloader.py`. Treat the two scripts as the source of truth, not the README.

## Running

Both scripts are interactive prompts (read URLs from stdin until `q`). No CLI args, no build step.

```bash
pip install -r requirements.txt
python 1024_downloader.py   # generic web media scraper
python xhs_downloader.py   # Xiaohongshu (小红书) media downloader
```

There is no test suite and no linter configured. Verification is manual: run the script, paste a URL, confirm files land in `download/`.

## The two downloaders

**`1024_downloader.py` — generic static-site media scraper.** Uses `curl_cffi` (not stdlib `requests`) specifically for `impersonate="chrome110"` TLS fingerprinting to bypass anti-hotlink/CDN blocks. Deep-scrapes media from `<img>/<source>/<video>` lazy-load attributes (`data-src`, `data-original`, `data-lazy-src`, `ess-data`, etc.), `<a href>` direct links, and inline `style` `url()` background images. Downloads concurrently (`ThreadPoolExecutor(max_workers=5)`) with per-request `Referer` set to the page URL (the actual anti-hotlink trick) and a random 0.5–1.5s delay to avoid IP bans. Output: `download/<domain>_<title-prefix>/`.

**`xhs_downloader.py` — Xiaohongshu note downloader.** Uses stdlib `requests` (different HTTP lib from the other script). Fetches the note page, regex-extracts `window.__INITIAL_STATE__={...}` JSON, `replace('undefined','null')`, then walks `note.noteDetailMap.<id>.note` for `imageList[*].urlDefault` and `video.media.stream.h264[0].masterUrl`. Detects risk-control/captcha pages ("验证码", "访问过于频繁"). Output: `download/<sanitized-title>/`.

### Cookies convention (`xhs_downloader.py`)

Cookies are loaded per-site from `cookies/<site_name>.txt`, where `site_name` comes from `get_site_name(url)` (maps `xiaohongshu.com`/`xhslink.com` → `xiaohongshu`, `bilibili.com`/`b23.tv` → `bilibili`, etc.). Missing/empty cookie files are auto-created empty with a prompt to fill them. Only `xiaohongshu` has a parser implemented; `bilibili`/`douyin` resolve to a site name but print "not yet supported". Adding a new site = add a branch in `get_site_name` plus a `download_<site>_media` function and a dispatch case in `__main__`.

## Conventions

- `download/` (output) and `cookies/` (secrets) are gitignored — never commit either.
- Both scripts use `fake_useragent` for rotating UAs, with a hardcoded Chrome 120 UA fallback if init fails.
- Comments are in Chinese; match this when editing existing code.
- Concurrency is fixed at `max_workers=5` in both scripts — deliberately conservative to avoid IP bans.
