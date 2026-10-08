# 仓库指南（供 AI 编码助手阅读）

一组交互式命令行媒体下载工具：脚本从 stdin 循环读取链接（输入 `q` 退出），把图片/视频下载到 `download/`。无构建步骤，纯 Python 3 脚本。

## 仓库现状（先读）

README 已重写为中文并与当前代码一致，是本仓库的权威使用文档。历史上曾有的 Telegram bot（`main.py`、`config.py`、`src/`）早已删除，不要按那个架构理解本仓库。

仓库没有 `pyproject.toml` / `setup.py` / `pytest.ini`，不是可安装的包——模块之间靠扁平目录直接 `import`，**必须在项目根目录运行脚本和测试**。

## 项目结构

```
downloader.py        # 统一入口——dispatch_url() 按站点名路由到三个后端之一
xhs_downloader.py    # 小红书笔记解析（手写 __INITIAL_STATE__ 提取）
1024_downloader.py   # 通用静态网页抓取（curl_cffi TLS 伪装）
http_client.py       # HTTP 统一封装：curl_cffi 优先，不可用时降级系统 curl 命令
sites.py             # URL→站点名映射、Cookie 文件加载（新增站点识别只改这里）
utils.py             # 日志、文件名清洗、目录构造、UA 初始化、cookie 转换等共用函数
setup_termux.sh      # Termux 首次使用配置脚本（仅 Termux 环境）
tests/               # pytest 单元测试（不联网），文件名与模块一一对应
requirements.txt     # 依赖清单（requests / fake_useragent / beautifulsoup4 / curl_cffi / yt-dlp）
cookies/             # 各站点 cookie 文件，cookies/<站点名>.txt（已 gitignore）
download/            # 输出目录，download/<站点>[/<作者>]/<标题>[_<ID>]/ 或 <标题>.<ext>（已 gitignore）
```

## 构建、测试与运行

```bash
python -m venv .venv && source .venv/bin/activate   # 可选但推荐
pip install -r requirements.txt
pip install pytest
python -m pytest -q                                  # 223 个单元测试，全部离线
python downloader.py                                 # 统一入口，按 URL 自动路由
```

注意：裸 `pytest -q` 在没有 conftest.py 的情况下不会把项目根加入 `sys.path`，会因 `ModuleNotFoundError` 收集失败；统一用 `python -m pytest -q`（或补充 conftest.py）。当前 223 个测试全部通过（已验证）。

## 下载器架构

`downloader.py` 是唯一的推荐入口，`dispatch_url(url)` 用 `sites.get_site_name` 路由：

| 路由 | 后端 |
|------|------|
| xiaohongshu | `xhs_downloader.download_xhs_media`（手写解析，需 Cookie） |
| bilibili / douyin / youtube / instagram / twitter | `download_url` → yt-dlp |
| 其余任意站点 | `1024_downloader.extract_general_media`（通用静态抓取） |

**`downloader.py`（yt-dlp 后端）。** `download_url` 采用两段式：先 `extract_info(download=False)` 探一次拿作者（`resolve_uploader` 取 `uploader` 并 `sanitize_filename` 清洗），再 `build_ydl_opts(site, author=...)` 构造选项后真正下载。代价是多一次请求；收益是作者缺失时自然退化为无作者层，不产生 `NA/` 或 `unknown/` 占位目录。cookiefile 仅当 `cookies/<site>.txt` 存在且非空时才传入。短链（`b23.tv`/`v.douyin.com`/`youtu.be`/`t.co`/`instagr.am`/`xhslink.com`）先经 `resolve_short_link` 用 http_client 展开为最终 URL——yt-dlp 走 Python socket 做 DNS，部分环境（沙箱、Termux）对短链域名解析失败，curl 自带解析器正常。yt-dlp 保持静默（`quiet`/`noprogress`），进度经 `progress_hooks` 转 logging。瞬时网络错误（SSL EOF、连接重置、超时、DNS 失败、502/503）由 `diagnose_error` 识别后整链退避重试最多 3 次；登录类错误翻译成"需登录 / Cookie 失效"提示。**新增站点首选这个入口**——yt-dlp 已覆盖上千站点，通常只需在 `sites.py` 加映射。

**`sites.py`（共用）。** `get_site_name(url)` 把域名归一为小写站点名：twitter 含 `x.com`/`t.co`，xiaohongshu 含 `xiaohongshu.com`/`xhslink.com`/`xhslink.cn`；未知域名取最后两段（`www.example.com` → `example.com`），无点/IP 用完整 host。`SITE_REQUIRE_COOKIE` 记录各站点 cookie 建议级别。`load_cookie_for_url` 按 `cookies/<site>.txt` 加载：文件不存在时自动创建空文件并提示，内容为空返回 None。**新增站点识别只改这一处**，保证 downloader.py 与 xhs_downloader.py 口径一致。

**`xhs_downloader.py`（小红书）。** 手写正则提取 `window.__INITIAL_STATE__={...}`（比 yt-dlp 对小红书更可控），经 `normalize_xhs_state_json` 只把 JSON 值语境的 `undefined` 替换为 `null`，再遍历 `note.noteDetailMap.<id>.note` 取 `imageList[*].urlDefault` 与 `video.media.stream.h264[0].masterUrl`。用 stdlib `requests`（区别于 1024 的 curl_cffi），Session 配 urllib3 Retry（total=5, backoff）。作者经 `extract_xhs_author` 取 `note.user.nickname`/`nickName` 双 key 兼容。检测风控（"验证码"/"访问过于频繁"/重定向到 verify/login/captcha，由 `detect_risk_control`）与笔记不存在（`/404` 路径、`errorCode=-510001`、"你访问的页面不见了" title，由 `detect_note_not_found`），避免 404 页被误报为"页面结构变更"。媒体 URL 统一经 `ensure_https` 升级。输出 `download/xiaohongshu/[<作者>/]<标题>_<noteId>/`，无标题笔记兜底 `xhs_<noteId>`；目录内图片按 `001.jpg` 序号命名、视频固定 `video.mp4`。

**`1024_downloader.py`（通用静态抓取）。** 用 `curl_cffi` 的 `impersonate="chrome110"` 做 TLS 指纹伪装绕过防盗链/CDN。页面请求经 `fetch_page` 封装：`429` 限流、所有 `5xx`（含腾讯云 EdgeOne WAF 的非标拦截码 `567`，酷安等站点会间歇返回"请求已被站点的安全策略拦截"页）及网络异常按指数退避重试至多 3 次（`_is_block_status` 判定），`4xx` 确定性错误立即放弃。深扫 `<img>/<source>/<video>` 的懒加载属性（`data-src`、`data-original`、`ess-data` 等）、`<a href>` 直链、行内 `style` 的 `url()` 背景图。装饰资源过滤：扫描阶段（`collect_media_urls._add`）按原始 URL 文件名命中 `_DECORATIVE_KEYWORDS`（logo/avatar/qrcode/emotion/beian/icon 等）剔除——必须在此时做，落盘会被序号重命名为 `001.<ext>`，下载阶段已拿不到原始关键词；下载后按宽高过滤——`utils.get_image_dimensions` 用 Pillow 解析图片宽高（解析失败返回 None），短边 < `MIN_IMAGE_DIMENSION`（300px）判为装饰图删除，尺寸解析失败回退体积阈值 `MIN_IMAGE_BYTES`（20KB）；视频不过滤（短视频也可能很小）。下载时 `Referer` 设为页面 URL（破解防盗链的关键），拦截到 HTML 响应（被风控）直接判失败，扩展名按 URL + Content-Type 校正（`guess_extension`）。`ThreadPoolExecutor(max_workers=5)` 并发，请求间 0.5–1.5s 随机延迟防封 IP。媒体扫描由 `collect_media_urls` 按 DOM 顺序保序去重；落盘按 `build_media_tasks` 序号命名 `001.<ext>`（目录名已带标题与 ID，文件本体只要序号；与小红书同风格，目录内按序号还原帖内次序），不再保留远端乱序哈希名。作者经 `extract_page_author` 两级提取：DOM 选择器（酷安 `.username-item`）优先，`<title>` 的"正文 来自 作者 - 站点"后缀模式兜底；命中时标题剥离作者尾巴（避免目录名重复），未命中返回空串自然退化为无作者层。输出 `download/<站点>[/<作者>/]<标题>[_<数字ID>]/`——URL 末段含 ≥5 位数字 ID 时经 `utils.extract_url_numeric_id` 追加后缀（`make_page_dir_name`），同标题可区分、目录可溯源。

**`http_client.py`（HTTP 统一封装）。** 提供 `Session` 和模块级 `get()`。curl_cffi 可用时委托给它；不可用时（Termux 上编译扩展常因 Python ABI 不匹配加载失败）降级为系统 `curl` 命令（subprocess），返回兼容 requests 接口的 `CurlResponse`（`text`/`headers`/`iter_content`/`raise_for_status`/`json`）。系统 curl 不做 JA3 伪装，只加 `--http2`（硬编码 cipher/curve 反而会 TLS 握手失败）。被 `1024_downloader` 和 `downloader.py` 的短链展开共用。

**`utils.py`（共用工具）。** `setup_logging`（幂等）、`init_useragent`（fake_useragent 随机 UA，多采样探测不可用时降级固定 Chrome UA）、`extract_urls`（从粘贴的分享文案中提取 URL，剔除尾部中文/标点）、`sanitize_filename`、`build_download_dir`（作者非空才加层，防御目录穿越）、`unique_path`（`O_CREAT|O_EXCL` 原子占位去重，并发不撞名）、`guess_extension`、`normalize_url`、`is_html_content`、`normalize_xhs_state_json`、`cookie_header_to_netscape`（把 `Cookie:` 请求头字符串转成 yt-dlp 可读的 Netscape 文件）。

## 代码风格约定

- Python 3，源码 UTF-8，带 `# -*- coding: utf-8 -*-` 头（部分老文件无此头，不必特意补）。
- 4 空格缩进；函数 `snake_case`；模块级常量 `UPPER_CASE`。
- **注释和用户可见消息用中文**，修改既有代码时沿用。
- 并发维持 `max_workers=5`，避免触发 IP 熔断。
- UA 用 `init_useragent` 获取（fake_useragent + 固定 Chrome 兜底），不要自己硬编码。
- 站点识别一律走 `sites.get_site_name`，不要在各脚本里重复写域名判断。
- 下载目录一律经 `utils.build_download_dir` 构造，保证"有作者加层、无作者退化"口径一致。
- 文件名冲突用 `unique_path` 原子去重，绝不覆盖已有文件。

## 测试

pytest 是唯一框架，223 个测试全部离线（不联网）。测试位于 `tests/test_<模块>.py`，只覆盖纯函数：`get_site_name`、`resolve_uploader`、`build_ydl_opts`、`dispatch_url`、`diagnose_error`、`resolve_short_link`、`detect_risk_control`、`detect_note_not_found`、`extract_xhs_author`、`extract_video_url`、`ensure_https`、`_is_transient_error`、`_is_block_status`、`fetch_page`、`_is_decorative_name`、`download_file`（注入假 session）、`get_image_dimensions`（Pillow 生成真实图片）、`extract_url_numeric_id`、`make_page_dir_name`、`collect_media_urls`（构造 HTML 字符串）、`build_media_tasks`、sanitize/build_download_dir/unique_path/cookie_header_to_netscape 等。新增 URL 或解析分支时按 `tests/test_sites.py` 的方式用参数化用例；新的公共函数应附带测试。提交前跑 `python -m pytest -q`。

## Cookie 与安全

- Cookie 是每站点一个纯文本文件：`cookies/<站点名>.txt`。文件缺失或为空时脚本自动创建并提示用户填写。
- `download/` 和 `cookies/` 都已 gitignore，**绝不提交**；不要打印 cookie 内容。
- **两个入口的 cookie 格式不同**：yt-dlp 后端（downloader.py）要 Netscape cookie 文件（可用 `utils.cookie_header_to_netscape` 转换）；xhs_downloader 直接把文件内容塞进 `Cookie:` 请求头，要的是浏览器复制的请求头整段字符串。填错格式会被静默忽略或判定未登录（详见 README）。

## 提交与 PR

提交信息沿用历史约定式前缀 + 简短中文摘要，如 `fix: xhslink download 503 failed`、`feat: 识别 xhslink.cn 为小红书站点`、`docs: README 补充短链自动展开`，主题行约 50 字以内。PR 说明改了什么、为什么，并确认 `python -m pytest -q` 通过。

## Termux 支持

`setup_termux.sh` 是 Termux 首次使用的一键配置脚本（申请存储权限、装依赖等）。http_client 的系统 curl 降级路径与 downloader.py 的短链 curl 预展开都是为 Termux 等受限环境设计的，改动这些路径时注意不要破坏降级语义。
