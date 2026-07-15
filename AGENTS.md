# Repository Guidelines

A collection of interactive CLI media downloaders. Scripts read URLs from stdin until `q` and write media under `download/`.

## Project Structure

```
downloader.py        # Unified entry — dispatch_url() routes by site name to the right backend
xhs_downloader.py    # Xiaohongshu note parser (hand-rolled __INITIAL_STATE__ extraction)
1024_downloader.py   # Generic static-site scraper (curl_cffi TLS impersonation)
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

## Coding Style & Conventions

Python 3, UTF-8 source files with `# -*- coding: utf-8 -*-` headers. 4-space indentation. Comments and user-facing messages are in Chinese — match this when editing existing code. Use `fake_useragent` for UA rotation with a hardcoded Chrome fallback. Keep concurrency at `max_workers=5` to avoid IP bans. Functions are `snake_case`; module-level constants are `UPPER_CASE`. Always route site recognition through `sites.get_site_name` so both scripts stay consistent.

## Testing

`pytest` is the only framework. Tests live in `tests/test_<module>.py` and cover pure functions only (`get_site_name`, `cookie_file_for`, `sanitize_filename`, `build_download_dir`, state normalization, error detection). Tests must not hit the network. Use parametrized cases as in `tests/test_sites.py` when adding URL or parsing branches. Run `pytest -q` before pushing; new public functions should ship with a test.

## Cookies & Security

Cookies are per-site plain-text files under `cookies/<site>.txt`. Both `download/` and `cookies/` are gitignored — never commit either. If a cookie file is missing or empty, scripts auto-create it and prompt the user to fill it in. Do not log cookie contents. Only `xiaohongshu` has a full cookie parser; `bilibili`/`douyin` resolve to a site name but print "not yet supported".

## Commits & Pull Requests

Follow the conventional-prefix style used throughout the history: `feat:`, `fix:`, `docs:` followed by a short Chinese summary, e.g. `fix: xhslink download 503 failed` or `feat: 下载目录按站点与作者归类`. Keep the subject line under ~50 chars. PRs should describe what changed and why, link any related issue, and confirm `pytest -q` passes. Screenshots are not required for CLI-only changes.
