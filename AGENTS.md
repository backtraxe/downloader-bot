# 仓库指南（供 AI 编码助手阅读）

一组交互式命令行媒体下载工具：脚本从 stdin 循环读取链接（输入 `q` 退出），把图片/视频下载到 `download/`。无构建步骤，纯 Python 3 脚本。

## 仓库现状（先读）

README 是本仓库的权威使用文档（中文，与当前代码一致）。历史上曾有的旧 Telegram bot（`main.py`、`config.py`、`src/`）早已删除；现在的 `telegram/` 目录是后来重新实现的另一套，不要按旧架构理解本仓库。

仓库没有 `pyproject.toml` / `setup.py`，不是可安装的包——代码分两个 sys.path 包：`core/`（下载核心）与 `telegram/`（机器人功能，仅依赖 core，单向）。边界纪律：core 绝不 import telegram（`tests/test_module_boundary.py` 固化）；telegram 对 core 的契约只有 `dispatch_url` / `http_client` / `utils`。入口统一 `python -m 模块路径`（`python -m core.downloader` 统一下载、`python -m telegram.bot` 机器人等），根目录无脚本 shim——**改功能直接改 core/ 或 telegram/ 里的对应模块**。**脚本和测试都必须在项目根目录运行**（`cookies/`、`download/`、`config.json` 均为 CWD 相对路径）。

## 项目结构

```
core/
  downloader.py        # 统一入口——dispatch_url() 按站点名路由到各后端
  xhs_downloader.py    # 小红书笔记解析（手写 __INITIAL_STATE__ 提取）
  douyin_downloader.py # 抖音图文笔记+视频（pace_f/RENDER_DATA 手写解析 + 签名 detail API，需登录 Cookie）
  1024_downloader.py   # 通用静态网页抓取（curl_cffi TLS 伪装；数字开头只能 import_module 导入）
  douyin_sign/         # vendored 签名三件套（Apache-2.0，见各文件头注；整体排除在 ruff 之外）
  tools/               # 独立 CLI：cookie_import / cookie_login 维护 Cookie，live_photo 实况后处理
  http_client.py       # HTTP 统一封装：curl_cffi 优先，不可用时降级系统 curl 命令
  sites.py             # URL→站点名映射、Cookie 文件加载（新增站点识别只改这里）
  utils.py             # 日志、文件名清洗、目录构造、UA 初始化、cookie 转换等共用函数
  config.py          # 统一配置 config.json 加载（telegram 段供 tg 层；向后兼容旧 telegram.json）
telegram/
  bot.py               # Telegram 机器人（长轮询常驻）：聊天发链接→dispatch_url 下载→产物回传，caption 打 #作者 #站点 标签
  upload.py            # Telegram 直发（Bot API）：目录→10 张/组相册，HEIC/大图走 document；读 config.json 的 telegram 段
入口统一 python -m 模块路径（python -m core.downloader / python -m telegram.bot 等）
conftest.py            # 让裸 pytest 也能从仓库根导入两个包
ruff.toml              # ruff 配置：lint 只选 F/E9/W/B bug 类规则；整体排除 core/douyin_sign/
setup_termux.sh        # Termux 首次使用配置脚本（仅 Termux 环境）
tests/                 # pytest 单元测试（不联网），文件名与模块一一对应
requirements.txt       # 依赖清单（requests / fake_useragent / beautifulsoup4 / curl_cffi / yt-dlp）
cookies/               # 各站点 cookie 文件，cookies/<站点名>.txt（已 gitignore）
download/              # 输出目录，download/<站点>[/<作者>]/<标题>[_<ID>]/ 或 <标题>.<ext>（已 gitignore）
config.json            # 仓库统一配置（已 gitignore）；旧版 telegram.json 仍可被兜底读取
.nomedia               # 阻止 Android 媒体扫描索引仓库目录（含 download/ 产物，Termux 场景）
```

## 构建、测试与运行

```bash
python -m venv .venv && source .venv/bin/activate   # 可选但推荐
pip install -r requirements.txt
pip install pytest
python -m pytest -q                                  # 单元测试，全部离线
python -m core.downloader                            # 统一入口，按 URL 自动路由
ruff check .                                         # 静态检查（先 pip install ruff）
ruff format .                                        # 代码格式化（black 兼容）
```

根目录 conftest.py 会把项目根加入 sys.path，裸 `pytest -q` 与 `python -m pytest -q` 均可用。静态检查用 ruff（配置 ruff.toml，只选 F/E9/W/B 等 bug 类规则，不做风格重排），当前全绿。

## 下载器架构

`core/downloader.py` 是唯一的推荐入口，`dispatch_url(url)` 用 `sites.get_site_name` 路由：

| 路由 | 后端 |
|------|------|
| xiaohongshu | `xhs_downloader.download_xhs_media`（手写解析，需 Cookie） |
| 抖音图文笔记（/note/） | `douyin_downloader.download_douyin_note`（手写解析，需登录 Cookie；拦截时签名 API 兜底） |
| 抖音视频（/video/） | `douyin_downloader.download_douyin_video`（签名 detail API，需登录 Cookie） |
| bilibili / youtube / instagram / twitter | `download_url` → yt-dlp |
| 其余任意站点 | `1024_downloader.extract_general_media`（通用静态抓取） |

注意：douyin 必须先 `resolve_short_link` 展开短链（v.douyin.com）才能判断目标是 /note/ 还是 /video/（两个手写解析器各自认领）——yt-dlp 的 DouyinIE 只认 /video/ 且 2026-08/09 起被 Argus 风控签名整拦，不再用于抖音作品下载；/user/ 等其他 douyin URL 仍兜底 yt-dlp。短链服务端偶发不返回 302 而是 200（反爬不一致，实测发生过），因此路由和 download_url 都用 `resolve_short_link_with_retry`（无变化时退避重试至多 3 次）而不是单次 `resolve_short_link`。

**`core/downloader.py`（yt-dlp 后端）。** `download_url` 采用两段式：先 `extract_info(download=False)` 探一次拿作者（`resolve_uploader` 取 `uploader` 并 `sanitize_filename` 清洗），再 `build_ydl_opts(site, author=...)` 构造选项后真正下载。代价是多一次请求；收益是作者缺失时自然退化为无作者层，不产生 `NA/` 或 `unknown/` 占位目录。cookiefile 仅当 `cookies/<site>.txt` 存在且非空时才传入。短链（`b23.tv`/`v.douyin.com`/`youtu.be`/`t.co`/`instagr.am`/`xhslink.com`）先经 `resolve_short_link` 用 http_client 展开为最终 URL——yt-dlp 走 Python socket 做 DNS，部分环境（沙箱、Termux）对短链域名解析失败，curl 自带解析器正常。yt-dlp 保持静默（`quiet`/`noprogress`），进度经 `progress_hooks` 转 logging。瞬时网络错误（SSL EOF、连接重置、超时、DNS 失败、502/503）由 `diagnose_error` 识别后整链退避重试最多 3 次；登录类错误翻译成"需登录 / Cookie 失效"提示；平台风控的确定性拦截归类 `blocked` 直接给出准确说明。**新增站点首选这个入口**——yt-dlp 已覆盖上千站点，通常只需在 `core/sites.py` 加映射。

**抖音签名 detail API（2026-10-14 打通）。** `aweme/v1/web/aweme/detail/` 由 Argus 风控插件把守：缺签名一律 403 "Blocked by ArgusSecurityPlugin Uifid Not Found"（Cookie 完整也一样拦，yt-dlp 的 DouyinIE 即因此整拦、报误导性 "Fresh cookies needed"👉由 `diagnose_error` 归为 `blocked` 兜底提示）。页面侧无活路：视频页 pace_f 数据里 `"aweme": null`——视频详情不内嵌（笔记页内嵌，故图文走 HTML、视频只能走 API）；iesdouyin legacy 空响应、share 页纯壳、蜘蛛 UA 被 JS 墙拦。解法：**三件套纯本地签名**（算法移植自 Evil0ctal/Douyin_TikTok_Download_API，Apache-2.0，`core/douyin_sign/` 三件套头注有溯源）：a_bogus（bdms.js v1.0.1.19 逆向，SM3+RC4 变体）+ 伪 msToken（126 位随机串，服务端不校验）+ x-secsdk-web-signature（`md5(uifid_timestamp_盐_query)`，盐随 SDK 版本，**这是 "Uifid Not Found" 的对应物**）。签名素材就是 Cookie 里的 `UIFID`/`UIFID_TEMP`（websign 用它，**任意非空值即可**，服务端不校验内容）——但 2026-10-14 对照矩阵实测：仅 UIFID 仍返回 `200 空`，真正门禁是 **UIFID 签名 + 有效 ttwid 或 sessionid 至少其一**（`ttwid`/`sessionid` 的值会被校验，过期即 200 空；`s_v_web_id` 只随 query 的 verifyFp/fp 携带，服务端不校验，缺了也没事；ttwid 访客短期会过期，是"旧 Cookie 突然全 200 空"的常见原因；纯 HTTP 自助换 ttwid 需过 `__ac_signature` 挑战，未实现——刷新 Cookie 是正路）。封装在 `build_signed_detail_url`（拼接顺序与编码语义是命门：a_bogus 层 urlencode 编码后签，websign 先解码再按 JS URLSearchParams 语义整体重编码，签发的字节与发送的字节必须一致）；`fetch_aweme_detail` 重试瞬时错误与 2xx 非 JSON（`_NonJsonResponse`，日志带响应头部+指向刷 Cookie 的精确提示）、Argus 403 确定性拒绝不重试。底层风控若改版（盐/SDK 版本更新），三件套会整体失效，需对 Evil0ctal 上游重新同步。另注意：短间隔高频请求会把整个 douyin.com 的 HTML 链路打成 jsvm 挑战页（API 链路独立不受影响，故笔记页拦时自动改走 API 兜底），冷却后恢复。

**`telegram/bot.py`（Telegram 机器人）。** 长轮询 `getUpdates`（走 http_client，Termux 降级路径可用），**下载核心零改动**：`dispatch_url` 调用前后对 `download/` 做快照比对（`snapshot_tree`/`group_new_files`）拿本批产物，按 `download/<站点>[/<作者>]/<标题>` 深度反推 site/author/title（作者层存在与否 = 目录深度），caption 经 `make_tag` 压成 `#作者 #站点` 单 token（Telegram 原生可搜）。白名单 `config.json` 的 `telegram.allowed_users` 为空则全拒（防白嫖，被拒消息回显用户 id 便于加白）。重复链接靠"快照无新文件"识别（下载器幂等跳过不落新盘）。串行处理、防重启重放（`initial_offset` 跳过离线积压）。上传复用 `telegram/upload.py` 的 `upload()`（`chat_id` 按消息来源覆盖配置默认）。**telegram/upload.py 与 telegram/bot.py 共用 config.json 的 telegram 段。**

**`core/sites.py`（共用）。** `get_site_name(url)` 把域名归一为小写站点名：twitter 含 `x.com`/`t.co`，xiaohongshu 含 `xiaohongshu.com`/`xhslink.com`/`xhslink.cn`；未知域名取最后两段（`www.example.com` → `example.com`），无点/IP 用完整 host。`SITE_REQUIRE_COOKIE` 记录各站点 cookie 建议级别。`load_cookie_for_url` 按 `cookies/<site>.txt` 加载：文件不存在时自动创建空文件并提示，内容为空返回 None。**新增站点识别只改这一处**，保证 core/downloader.py 与 core/xhs_downloader.py 口径一致。

**`core/xhs_downloader.py`（小红书）。** 手写正则提取 `window.__INITIAL_STATE__={...}`（比 yt-dlp 对小红书更可控），经 `normalize_xhs_state_json` 只把 JSON 值语境的 `undefined` 替换为 `null`，再遍历 `note.noteDetailMap.<id>.note`：图片经 `extract_image_url` 优先用 `imageList[].fileId` 拼 `ci.xiaohongshu.com/<fileId>` 原图直链（未转码、尺寸与 note 声明宽高一致），fileId 缺失才回退 `urlDefault` 展示版压缩 webp；实况照片（live photo）每张 image 的 `stream.EF4~EF7` 档位里另有 h264 视频流，经 `extract_live_photo_stream` 与静态图一并下载——同序号配对 `001.heic` + `001.mp4`（静态图为 HEIF/MPO 原图，实况视频按 qualityType 选最高档）。部分原图实为 HEIF（`image/heic`），扩展名按 Content-Type 校正为 `.heic`（HEIF 无 Pillow 解码时 `get_image_dimensions` 返回 None，装饰过滤自动回退体积判定）；视频经 `extract_video_url` 从 `video.media.stream.h264` 按 `qualityType`(HD>SD>LD)/`videoBitrate` 选最高画质流而非首项。HTTP 走 http_client（curl_cffi `impersonate="chrome150"`，Termux 自动降级系统 curl），**UA 与指纹版本固定对齐 Chrome150**——2026-10 实测：chrome110/requests + 随机 UA 会被一律 302 到 /login 误判为 Cookie 失效，chrome150+UA150 一致组合即使无 Cookie 也能进笔记页。瞬时重试由 `_fetch_page`/`download_file` 各自的上层退避循环负责。作者经 `extract_xhs_author` 取 `note.user.nickname`/`nickName` 双 key 兼容。检测风控（"验证码"/"访问过于频繁"，或重定向**域名/路径**含 verify/login/captcha，由 `detect_risk_control`——query 键名不参与判定，防止 404 落地页的 `verifyMsg=` 参数误报为风控而掩盖"笔记不存在"）缺 xsec_token 风控（2026-10 起裸 /explore/<id> 无 token 一律 302 到 404 落地页，`error_code=300031`/`当前笔记暂时无法浏览`，由 `detect_missing_xsec_token`——与"笔记真被删"共用 404 页和 title，必须先行判定，误报成"笔记被删"会让用户白找链接；补救：浏览器地址栏带 token 的完整链接或 App 分享短链自带 token）与笔记不存在（`/404` 路径、`errorCode=-510001`、"你访问的页面不见了" title，由 `detect_note_not_found`），避免 404 页被误报为"页面结构变更"。媒体 URL 统一经 `ensure_https` 升级。输出 `download/xiaohongshu/[<作者>/]<标题>_<noteId>/`，无标题笔记基名经 `resolve_xhs_basename` 回退链（title → desc 前 30 字 → `#tag1_#tag2`（下划线分隔）→ `xhs`）；目录内图片按 `001.jpg` 序号命名、视频固定 `video.mp4`。

**`core/douyin_downloader.py`（抖音图文笔记）。** 手写解析 `douyin.com/note/<id>`：yt-dlp 的 DouyinIE 只支持 /video/。数据提取总入口 `extract_note_detail`——新版桌面页（React Flight 架构，2025 年后主路径）详情在 `self.__pace_f.push([1,"<chunk>"])` 流式数据块里：chunk 是 JSON 转义两层的 JS 字符串，解开含 `"aweme":{"statusCode":0,"detail":{...}}` 包装段（`parse_pace_f_detail`，`_ROUTER_DATA`/`RENDER_DATA` 递归定位是旧版兜底，`extract_note_detail` 先 RENDER_DATA 后 pace_f）；实测新版页面的 RENDER_DATA 只剩 app 配置、无 aweme detail。字段名 camelCase：`awemeId`、`images[].downloadUrlList/urlList`、`authorInfo.nickname/uniqueId`，兼容老 snake_case（`_FIRST_*`/`extract_*` 函数内双 key）。图片经 `extract_douyin_image_url` 过滤 `dy-water` 水印流水线后取值——downloadUrlList 实为 ~tplv-dy-water-* 水印端，urlList 才是 ~tplv-dy-aweme-images:* 无水印原尺寸端（同档 q75 双格式中 jpeg 优先；全部含水印时才退回水印版避免漏图）；实况图与笔记级视频经 `_first_play_url` 通用提取（`playAddr` 支持字符串列表和带 urlList 的字典，`cover*` 封面键跳过）——图文笔记的 video.playAddr 实为背景音乐音频；落 `video.mp4`。未登录/Cookie 失效时抖音返回 jsvm 反爬挑战页（空 body + `_$jsvmprt` 混淆脚本）；IP+Cookie 组合高频请求被限流时返回验证码中间页（~6KB，仅按 `title=验证码中间页` 判定——sec_sdk_build 路径在真实笔记页同样出现，不能当标记，Termux 移动网络真实样本）。两种拦截页由 `detect_jsvm_challenge` 统一识别（`_CHALLENGE_MARKERS` 任一命中），给出对应的 Cookie 失效 / IP 限流提示——纯 HTTP 都无法通过。命中验证码中间页时 `_fetch_page` 会**换新 Session 重试**（服务端通过 Set-Cookie 在 jar 里埋拦截标记，复用同一会话会被持续拦截，对照实验：同会话 3 连拦、新会话单发全通过），至多 2 次，仍命中交回调用方提示冷却。HTTP 走 http_client（curl_cffi `impersonate="chrome150"`，Termux 自动降级系统 curl），UA 固定对齐指纹版本（Chrome150）——随机 UA 与指纹版本不一致会概率性触发验证码中间页。1024 仍用 chrome110（一般站点无问题）。Cookie 文件两种格式皆可：Netscape（与 yt-dlp 侧共用 cookies/douyin.txt）或浏览器复制的 `Cookie:` 请求头整段，经 `utils.cookie_text_to_header` 统一转请求头。输出 `download/douyin/[<作者>/]<desc 前 30 字>_<awemeId>/`（`resolve_douyin_basename`，desc 空兜底 `douyin`），作者经 `extract_douyin_author` 取 `authorInfo.nickname`/`author.unique_id` 兜底。

**core/douyin_downloader.py 的视频路径（download_douyin_video）。** `/video/<id>` 经 `fetch_aweme_detail` 走签名 detail API（见上"签名 detail API"段），`extract_video_play_urls` 选流：优先 `video.play_addr.url_list`（网页播放端无水印 CDN 净版，实测全候选 200；download_addr 为烙印水印端，不取），playwm 标记候选剔除，`bit_rate` 首个有效码率档兜底；按候选顺序经 `download_file` 尝试直至成功。图文笔记页被 jsvm 挑战/验证码拦截时同样改走该 API 兜底（下游提取函数 camel/snake 双 key 已兼容）。单视频落盘与 yt-dlp 后端同构：`download/douyin/[<作者>/]<desc 前 30 字>_<awemeId>.mp4`（`build_download_dir` 定位父目录，目录只为占位）。`__main__` 入口 note/video 两种链接都收。

**`core/1024_downloader.py`（通用静态抓取）。** 用 `curl_cffi` 的 `impersonate="chrome110"` 做 TLS 指纹伪装绕过防盗链/CDN。页面请求经 `fetch_page` 封装：`429` 限流、所有 `5xx`（含腾讯云 EdgeOne WAF 的非标拦截码 `567`，酷安等站点会间歇返回"请求已被站点的安全策略拦截"页）及网络异常按指数退避重试至多 3 次（`_is_block_status` 判定），`4xx` 确定性错误立即放弃。深扫 `<img>/<source>/<video>` 的懒加载属性（`data-src`、`data-original`、`ess-data` 等）、`<a href>` 直链、行内 `style` 的 `url()` 背景图。装饰资源过滤：扫描阶段（`collect_media_urls._add`）按原始 URL 文件名命中 `_DECORATIVE_KEYWORDS`（logo/avatar/qrcode/emotion/beian/icon 等，单词边界匹配防 "iconic" 误杀）剔除——必须在此时做，落盘会被序号重命名为 `001.<ext>`，下载阶段已拿不到原始关键词；下载后按宽高过滤——`utils.get_image_dimensions` 用 Pillow 解析图片宽高（解析失败返回 None），短边 < `MIN_IMAGE_DIMENSION`（300px）判为装饰图删除，尺寸解析失败回退体积阈值 `MIN_IMAGE_BYTES`（20KB）；视频不过滤（短视频也可能很小）。下载时 `Referer` 设为页面 URL（破解防盗链的关键），拦截到 HTML 响应（被风控）直接判失败，扩展名按 URL + Content-Type 校正（`guess_extension`）。`ThreadPoolExecutor(max_workers=5)` 并发，请求间 0.5–1.5s 随机延迟防封 IP。媒体扫描由 `collect_media_urls` 按 DOM 顺序保序去重；落盘按 `build_media_tasks` 序号命名 `001.<ext>`（目录名已带标题与 ID，文件本体只要序号；与小红书同风格，目录内按序号还原帖内次序）。作者经 `extract_page_author` 两级提取：DOM 选择器（酷安 `.username-item p`）优先，`<title>` 的"正文 来自 作者 - 站点"后缀模式兜底（尾巴站点标签须与 URL 域名一致或为品牌名映射如酷安↔coolapk.com 才采信，防正文含"来自"误剥）；命中时标题剥离作者尾巴，未命中返回空串自然退化为无作者层。输出 `download/<站点>[/<作者>/]<标题>[_<数字ID>]/`——URL 末段含 ≥5 位数字 ID 时经 `utils.extract_url_numeric_id` 追加后缀（`make_page_dir_name`），同标题可区分、目录可溯源。

**`core/http_client.py`（HTTP 统一封装）。** 提供 `Session`（`get` / `post_files` multipart 上传）和模块级 `get()`。curl_cffi 可用时委托给它；不可用时（Termux 上编译扩展常因 Python ABI 不匹配加载失败）降级为系统 `curl` 命令（subprocess），返回兼容 requests 接口的 `CurlResponse`（`text`/`headers`/`iter_content`/`raise_for_status`/`json`）。系统 curl 不做 JA3 伪装，只加 `--http2`（硬编码 cipher/curve 反而会 TLS 握手失败）。`get` 被 `1024_downloader`、`xhs_downloader`、`douyin_downloader` 和 `downloader.py` 的短链展开共用；`post_files` 供 `telegram/upload.py` 走 Telegram Bot API（cffi 路径读文件入内存，降级路径翻译为 `curl -F field=@path`）。降级路径用 `curl -L` 跟随重定向时，`_parse_response_blocks` 逐跳累积中间块的 Location 保存为 `CurlResponse.url`（effective_url）——丢失它会让 `resolve_short_link` 永远判断"无重定向"、重试全徒劳，Termux 上的真实故障（已修）。

**`core/utils.py`（共用工具）。** `setup_logging`（幂等）、`init_useragent`（fake_useragent 随机 UA，多采样探测不可用时降级固定 Chrome UA；仅 1024_downloader 使用）、`extract_urls`（从粘贴的分享文案中提取 URL，剔除尾部中文/标点）、`sanitize_filename`、`build_download_dir`（作者非空才加层，防御目录穿越）、`unique_path`（`O_CREAT|O_EXCL` 原子占位去重，并发不撞名）、`guess_extension`、`normalize_url`、`is_html_content`、`normalize_xhs_state_json`、`cookie_header_to_netscape`（把 `Cookie:` 请求头字符串转成 yt-dlp 可读的 Netscape 文件）、`cookie_text_to_header`（逆操作：Netscape 文件/请求头整段统一转回 `Cookie:` 头，douyin_downloader 用）。

**`core/tools/live_photo.py`（实况后处理，独立模块）。** 把小红书实况对儿（`001.heic`+`001.mp4`）打包为平台可用格式：`--android`（默认）把 HEIC 转 JPEG（pillow-heif，可选依赖——无 HEIC 时不需安装）+ MP4 mux 成 Motion Photo 单文件（`<stem>_motion.jpg`，双写 Camera + MicroVideo 两种 XMP，XMP 段必须紧跟 JPEG SOI 之后）；`--ios` 用 exiftool 写配对 ContentIdentifier 输出 `<stem>_ios.<原图扩展名>` + `<stem>_ios.mov`。华为的私有格式不支持。**注意**：被下载器完全解耦——不被 downloader/1024/xhs 任何模块 import，独立 CLI 使用。

## 代码风格约定

- Python 3，源码 UTF-8，带 `# -*- coding: utf-8 -*-` 头（部分老文件无此头，不必特意补）。
- 格式由 `ruff format` 统一（black 兼容）；**vendored 签名三件套（`core/douyin_sign/` 三件套）在 ruff.toml 里整体 exclude**，保持与 Evil0ctal 上游原样，便于风控改版后 diff 重新同步。
- 4 空格缩进；函数 `snake_case`；模块级常量 `UPPER_CASE`。
- **注释和用户可见消息用中文**，修改既有代码时沿用。
- 并发维持 `max_workers=5`，避免触发 IP 熔断。
- **UA 策略分两类**：1024_downloader 用 `init_useragent` 随机 UA（fake_useragent + 固定 Chrome 兜底）；xhs/douyin 用与 `impersonate` 指纹版本**严格对齐的固定 UA**（当前 Chrome150）——随机 UA 与 TLS 指纹版本不一致会被小红书 302 到 /login、概率性触发抖音验证码中间页，不要改回随机 UA。
- 站点识别一律走 `sites.get_site_name`，不要在各脚本里重复写域名判断。
- 下载目录一律经 `utils.build_download_dir` 构造，保证"有作者加层、无作者退化"口径一致。
- 文件名冲突用 `unique_path` 原子去重，绝不覆盖已有文件；重复下载同一链接时目标序号文件已存在直接跳过（`glob(stem + ".*")` 幂等判断），不产生 `_1` 副本。

## 测试

pytest 是唯一框架，测试全部离线（不联网）。测试位于 `tests/test_<模块>.py`，文件名与模块一一对应，只覆盖纯函数：`get_site_name`、`resolve_uploader`、`build_ydl_opts`、`dispatch_url`（含 douyin /note//video/ 路由）、`diagnose_error`、`resolve_short_link`、`detect_risk_control`、`detect_note_not_found`、`detect_missing_xsec_token`、`extract_xhs_author`、`extract_image_url`、`extract_video_url`（含多画质流选择）、`extract_live_photo_stream`、`ensure_https`、`_is_transient_error`、`_is_block_status`、`fetch_page`、`_is_decorative_name`、`download_file`（注入假 session）、`get_image_dimensions`（Pillow 生成真实图片）、`extract_url_numeric_id`、`make_page_dir_name`、`collect_media_urls`（构造 HTML 字符串）、`build_media_tasks`、`extract_page_author`、`detect_jsvm_challenge`、`parse_pace_f_detail`/`parse_render_data`/`extract_aweme_detail`/`extract_douyin_image_url`/`extract_douyin_author`/`resolve_douyin_basename`（构造页面 fixture）、`is_douyin_video_url`/`build_signed_detail_url`（websign md5 预签名重算）/`extract_video_play_urls`（video fixture）；vendored 签名栈 `douyin_sm3`（GB 向量）/`douyin_websign`（编码语义+md5 公式）/`douyin_abogus`（固定种子黄金值）、`mux_motion_photo`/`digest_motion_photo`（live_photo mux 往返）、`_parse_response_blocks`/`CurlResponse`（http_client 降级路径）、sanitize/build_download_dir/unique_path/cookie_header_to_netscape/cookie_text_to_header 等。另有 `tests/test_module_boundary.py` 固化 core/telegram 单向边界（core 源码无 telegram import、import core.downloader 不会带入 telegram 包）。新增 URL 或解析分支时按 `tests/test_sites.py` 的方式用参数化用例；新的公共函数应附带测试。提交前跑 `python -m pytest -q` 与 `ruff check .`。

## Cookie 与安全

- Cookie 是每站点一个纯文本文件：`cookies/<站点名>.txt`。文件缺失或为空时脚本自动创建并提示用户填写。
- `download/` 和 `cookies/` 都已 gitignore，**绝不提交**；不要打印 cookie 内容。
- **两个入口的 cookie 格式不同**：yt-dlp 后端（core/downloader.py）要 Netscape cookie 文件（可用 `utils.cookie_header_to_netscape` 转换）；core/xhs_downloader 直接把文件内容塞进 `Cookie:` 请求头，要的是浏览器复制的请求头整段字符串。填错格式会被静默忽略或判定未登录（详见 README）。
- douyin 图文/视频都由 douyin_downloader 消费同一文件：经 `cookie_text_to_header` 自动识别 Netscape 或 `Cookie:` 请求头整段两种格式——推荐统一填 Netscape（其他 yt-dlp 站点也兼容）；视频门禁要求 Cookie 含 `UIFID` + 有效 `ttwid`/`sessionid` 其一（2026-10-14 对照矩阵实测，`s_v_web_id` 服务端不校验；有效登录态天然齐全）。注意 yt-dlp 复用 cookiefile 时会回写该文件（jar resave），属预期行为。
- `core/tools/cookie_import.py` 是手机端恢复 Cookie 的推荐入口：stdin 自动识别 Cookie-Editor 的 JSON / `Cookie:` 请求头整段 / Netscape 三种导出（`detect_format`），按站点书面格式写文件——douyin/bilibili → Netscape（yt-dlp 可读），xiaohongshu → 请求头整段且 JSON 输入按域过滤第三方 cookie（`convert_to_site_format`）。Termux 用法：`termux-clipboard-get | python -m core.tools.cookie_import <站点>`；桌面端可重定向扩展导出文件。
- Pillow / pillow-heif 为可选依赖（requirements.txt 中注释列出）：缺 Pillow 时装饰过滤自动回退体积阈值，缺 pillow-heif 时 live_photo 不能转 HEIC。Termux 上 Pillow 无 Android wheel，用 `pkg install python-pillow`。

## 提交与 PR

提交信息沿用历史约定式前缀 + 简短中文摘要（前缀见 git log：`feat`/`fix`/`docs`/`style` 等），如 `fix: xhslink download 503 failed`、`feat: 识别 xhslink.cn 为小红书站点`、`docs: README 补充短链自动展开`，主题行约 50 字以内。PR 说明改了什么、为什么，并确认 `python -m pytest -q` 通过。

## Termux 支持

`setup_termux.sh` 是 Termux 首次使用的一键配置脚本（申请存储权限、装依赖、检测并重建 ABI 失配的 curl_cffi 等）。http_client 的系统 curl 降级路径与 core/downloader.py 的短链 curl 预展开都是为 Termux 等受限环境设计的，改动这些路径时注意不要破坏降级语义。
