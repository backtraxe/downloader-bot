# downloader-bot

交互式命令行媒体下载工具，输入网页链接即可抓取图片/视频到本地。

## 安装

```bash
pip install -r requirements.txt
```

**Termux**：先 `bash setup_termux.sh`（脚本内含 `pkg install python-pillow`——Pillow 无 Android 预编译 wheel，不能用 pip 装）。Pillow / pillow-heif 均为可选依赖，没有也能下载（装饰过滤自动回退体积阈值；HEIC 预览转码另有 libheif CLI/ffmpeg 兜底，live_photo 工具仍需 pillow-heif）。

## 使用

### 统一入口 core/downloader.py（推荐）

```bash
python -m core.downloader
```

输入链接后脚本按 URL 自动识别站点并分发到对应下载器，无需手动选脚本：

| 站点 | 域名 | 后端 | Cookie |
|------|------|------|--------|
| 小红书 | `xiaohongshu.com` / `xhslink.com` | 手写 `__INITIAL_STATE__` 解析 | 建议 |
| YouTube | `youtube.com` / `youtu.be` | yt-dlp | 通常不需要 |
| Bilibili | `bilibili.com` / `b23.tv` | yt-dlp | 建议（下高清需要） |
| 抖音 | `douyin.com` / `v.douyin.com` | 手写解析（图文笔记 + 视频，签名 detail API） | 需要登录态 |
| Instagram | `instagram.com` / `instagr.am` | yt-dlp | 强烈建议（否则大概率失败） |
| X(Twitter) | `x.com` / `twitter.com` / `t.co` | yt-dlp | 通常不需要 |
| 其他网站 | 任意 | 通用静态网页抓取 | 按域名命名 |

- Cookie 从 `cookies/<站点名>.txt` 读取
- 输出结构统一为 `download/<站点>[/<作者>/]<标题>[_<ID>]/`：图文内容一个目录，目录名可溯源原帖；目录内图片按 `001.jpg`、`002.jpg` 序号命名（还原帖内次序），视频为 `video.mp4`；单视频内容（yt-dlp 后端）直接落 `<标题>.<ext>` 文件
- 重复下载同一链接时，已存在的文件直接跳过，不产生 `_1` 副本
- 需要重下旧链接时加 `--force`（或 `-f`）：`python -m core.downloader --force`，已存在的文件会被覆盖重新下载（先写 `.part` 临时文件、成功后原子替换，下载中途失败不会破坏旧文件）
- 通用抓取会自动过滤站点装饰图（logo/头像/二维码/表情等）：文件名含装饰关键词的直接跳过，尺寸过小（短边 <300px）的落盘后删除
- 短链（`b23.tv` / `v.douyin.com` / `youtu.be` / `t.co` / `instagr.am` 等）会自动展开为最终直链后再下载，避免 DNS 解析失败

三个站点下载脚本也可单独运行（`python -m core.xhs_downloader` / `python -m core.douyin_downloader` / `python -m core.1024_downloader`），行为与统一入口一致。

#### 小红书后端

- 手写解析 `window.__INITIAL_STATE__`，提取图片列表和视频流（比 yt-dlp 对小红书更可控）
- 支持 `xhslink.com` 短链与 App 分享链接（含 `xsec_token`），脚本会跟随重定向到 `xiaohongshu.com` 后再解析
- **2026-10 起链接必须带 `xsec_token` 参数**：小红书对裸 `explore/<id>` 链接强制风控（返回 `error_code=300031`「当前笔记暂时无法浏览」落地 404 页——不是笔记被删）。请从浏览器地址栏复制带 `xsec_token` 的一整段链接，或用 App 内"分享"复制的短链（自带 token）；缺 token 时脚本会明确提示而不是误报"笔记已被删除"
- 需要在 `cookies/xiaohongshu.txt` 中填入浏览器 Cookie；文件不存在或为空时脚本会自动创建并提示
- 实况照片（live photo）按"静态原图 + 实况视频"同序号配对下载（`001.heic` + `001.mp4`），静态图为 HEIF/JPEG 原图、视频按画质选最高档

**实况照片后处理（core/tools/live_photo.py，独立模块）**：把下载好的实况对儿转成手机相册可用的实况格式。

```bash
python -m core.tools.live_photo "download/xiaohongshu/<作者>/<标题>_<noteId>"
```

- 默认生成 Android 侧 Motion Photo 单文件（`<stem>_motion.jpg`，JPEG 内嵌 MP4），小米/OPPO/Google Photos/三星相册可识别为动态照片；HEIC 静态帧自动转 JPEG（依赖 `pillow-heif`，已在 `requirements.txt`）
- 加 `--ios` 用 `exiftool` 写苹果配对元数据（`<stem>_ios.heic` + `<stem>_ios.mov`），需经 Live Photo 类 App 导入图库才显示为实况；exiftool 需 `brew install exiftool`
- 华为 HarmonyOS 动态照片为私有格式，目前无离线转换方案

#### 抖音图文笔记后端

- 抖音图文笔记（`douyin.com/note/<id>`，App 分享文案常见）yt-dlp 不支持，由 `core/douyin_downloader.py` 手写解析：带登录 Cookie 请求笔记页，从页面内嵌的 pace_f 流式数据块（新版页面主路径；旧版 `RENDER_DATA`/`_ROUTER_DATA` 兜底）中提取图片列表与视频
- **必须填有效登录态 Cookie** 到 `cookies/douyin.txt`：无 Cookie 时抖音返回 jsvm 反爬挑战页，脚本会识别并提示；Netscape 格式和浏览器复制的 `Cookie:` 请求头整段都能吃（脚本自动识别转换），与 yt-dlp 侧共用同一个文件
- 图片默认下载**无水印最高档**（避开 `dy-water` 水印流水线，同档 q75 中 jpeg 优先）；与网页端浏览完全同源；输出 `download/douyin/[<作者>/]<标题>_<awemeId>/`，图片按 `001.jpg` 序号命名，实况图视频同序号配对 `001.mp4`，笔记级视频为 `video.mp4`
- **抖音视频链接（`/video/<id>`）走签名 detail API**：2026-08/09 起视频详情接口（`aweme/v1/web/aweme/detail/`）启用了 JS 风控签名校验（Argus），yt-dlp 无签名整拦（报"Fresh cookies needed"误导为 Cookie 过期）；页面也不内嵌视频数据（pace_f 里 `aweme: null`）。本仓库补齐纯本地三件套签名后直连该 API——a_bogus（bdms.js 逆向）+ 伪 msToken + x-secsdk-web-signature（md5），算法移植自 [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)（Apache-2.0）。**服务端门禁（2026-10 对照矩阵实测）= Cookie 含 `UIFID`（签名素材，值不校验）+ 有效 `ttwid` 或 `sessionid` 至少其一**；缺 UIFID 是 403，凭据失效是持续 `200 空响应`（脚本会识别并提示刷新 Cookie）。播放地址取网页播放端无水印 CDN 净版，输出 `download/douyin/[<作者>/]<标题>_<awemeId>.mp4`
- 笔记页被反爬挑战（jsvm/验证码）拦截时自动改走同一个签名 detail API 兜底（两条链路风控独立），仍拦则按提示冷却/换网络

#### 通用网页后端

- 使用 `curl_cffi` 的 `impersonate="chrome110"` 做 TLS 指纹伪装，绕过防盗链 / CDN 拦截
- `curl_cffi` 不可用时自动降级为系统 `curl` 命令（由 `core/http_client.py` 统一封装，跟随 30x 重定向），Termux 等环境同样可用
- 被站点 WAF 拦截（HTTP 429 / 5xx，如腾讯云 EdgeOne 的 567 拦截页）或网络异常时自动退避重试最多 3 次
- 深度扫描 `<img>/<source>/<video>` 的懒加载属性（`data-src`、`data-original`、`ess-data` 等）、`<a href>` 直链、以及行内 `style` 中的 `background-image`
- 能识别 `正文 来自 作者 - 站点` 标题模式的页面自动提取作者，目录带作者层
- 下载时带 `Referer`（破解防盗链的关键），并发下载（`max_workers=5`）

## 目录约定

- `download/` — 下载输出目录（已 gitignore）
- `cookies/` — 各站点 Cookie 文件，命名规则为 `cookies/<站点名>.txt`（已 gitignore）
- `config.json` — 统一配置文件（已 gitignore）：telegram 段配置 Telegram 功能；不用则无需关心

## 获取 Cookie

Cookie 文件命名规则为 `cookies/<站点名>.txt`（如 `cookies/bilibili.txt`、`cookies/xiaohongshu.txt`）。
**两个入口需要的格式不同**，请按对应脚本填入：

### `downloader.py`（yt-dlp）— Netscape cookie 文件格式

yt-dlp 的 `cookiefile` 读取的是 **Netscape cookie 文件**（每行一个 cookie，字段用 Tab 分隔），**不是**浏览器里复制的 `Cookie:` 请求头字符串。格式：

```
# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	1855390400	LOGIN_INFO	xxxxxx
.youtube.com	TRUE	/	TRUE	1855390400	SID	yyyyyy
.bilibili.com	TRUE	/	FALSE	1855390400	SESSDATA	zzzzzz
```

获取方式（任选其一）：

1. **浏览器扩展导出（最省事）**：安装 [Get cookies.txt](https://chromewebstore.google.com/detail/get-cookiestxt-locally/97mihbckbjnpjbkmbigfgejebkmjnlnk) 等扩展，登录目标站点后导出为 `.txt`，内容直接粘进 `cookies/<站点名>.txt`。
2. **yt-dlp 直接从浏览器读取**（无需手动填文件）：在 `core/downloader.py` 的 `build_ydl_opts` 里加 `"cookiesfrombrowser": ("chrome",)`（支持 `chrome`/`firefox`/`edge`/`safari` 等），需关掉浏览器进程。
3. **从抓包/手头的 `Cookie:` 头字符串转换**：若你只有请求头字符串（移动端抓包常见），用本仓库自带的转换函数生成 Netscape 文件：

   ```bash
   python -c "from core.utils import cookie_header_to_netscape as f; f('SESSDATA=abc; bili_jct=xyz', '.bilibili.com', 'cookies/bilibili.txt')"
   ```

> ⚠️ 填了 `Cookie:` 头字符串会导致 yt-dlp **静默忽略** cookie，下载受限内容仍报“需要登录”。

### core/xhs_downloader.py — Cookie 请求头字符串

`xhs_downloader.py` 把文件内容原样塞进 HTTP `Cookie` 请求头，因此需要的是浏览器 DevTools 里复制的 **`Cookie:` 头整段字符串**：

1. 用浏览器登录小红书，打开任意笔记页
2. F12 → Network → 选中该页面的请求 → Headers → 找到 `Cookie` 字段
3. 复制 `Cookie:` 后的整段值（形如 `web_session=xxx; xsecappid=xhs-pc-web; ...`），粘进 `cookies/xiaohongshu.txt`

> ⚠️ 这种格式**不能**填 Netscape cookie 文件，否则小红书解析会判定为未登录。

### 桌面端（Chrome / Edge / Firefox）

1. 登录目标站点，打开任意页面
2. F12 → Network → 选一条该域名的请求 → Headers → 复制 `Cookie` 字段的整段值
3. `xhs_downloader.py` 直接粘进 `cookies/<站点名>.txt`
4. `downloader.py` 需转成 Netscape 格式：用扩展导出，或用上面的转换命令

### 移动端 — iPhone

iOS 没有 DevTools，也**无法**用“快捷指令”读取 Safari 的 Cookie（沙盒隔离）。推荐用**抓包 App**（本地 VPN + 安装根证书做 HTTPS 解密）：

- **Surge / Stream / Quantumult X**：开启抓包后访问目标站点网页版，在抓到的请求里找 `Cookie` 请求头，复制整段值。
- 导出的是 **`Cookie:` 请求头字符串**，用法同桌面端：`xhs_downloader.py` 直接用；`downloader.py` 用转换命令转成 Netscape。

> 抓包需安装并信任 App 提供的 CA 根证书（设置 → 通用 → 关于本机 → 证书信任设置）。

### 移动端 — 安卓

- **要 Netscape 文件（yt-dlp 直接用）**：用 **Edge 安卓版**（自带扩展支持）或 **Firefox for Android** + [Android Cookie Importer](https://addons.mozilla.org/en-US/android/search/?q=cookies.txt) 扩展，登录后导出 `cookies.txt`，粘进 `cookies/<站点名>.txt`。
- **要请求头字符串**：用 [Reqable](https://reqable.com/)（原 HttpCanary，本地 VPN + CA 证书）抓包，从请求里复制 `Cookie` 头。

> Chrome/Edge 的 Cookie 在非 root 设备上无法靠翻系统文件直接读取（App 沙盒保护）；但浏览器**扩展**运行在内核内可以读，所以走扩展导出才是正路。

### 手机上 Cookie 失效的快速恢复（Termux，不依赖电脑）

一次性准备：

1. 安装 **Termux:API** App（与 Termux 同来源，F-Droid 版），并在 Termux 里 `pkg install termux-api`
2. 日常用的浏览器只要支持扩展即可——**Edge 安卓版**（自带扩展支持，无需换浏览器）、Kiwi 或 Firefox for Android 都行——给它装 **Cookie-Editor** 扩展（支持导出 Header String / Netscape 两种格式）

恢复流程（约 30 秒）：

1. 在该浏览器里打开对应网页版并登录（手机号验证码登录即可，无需扫码）：
   抖音 `douyin.com`、小红书 `xiaohongshu.com`、bilibili `bilibili.com`
2. Cookie-Editor 里把本站 Cookie 复制到剪贴板（弹窗底部最右的 ↑ 图标是
   Export；手机屏窄找不到也没关系——JSON / Header String / Netscape
   **任意格式都可以**，脚本自动识别）
3. Termux 里一条命令写入（格式识别 + 站点格式转换由 `cookie_import.py` 完成）：

   ```bash
   termux-clipboard-get | python -m core.tools.cookie_import douyin
   termux-clipboard-get | python -m core.tools.cookie_import xiaohongshu
   termux-clipboard-get | python -m core.tools.cookie_import bilibili
   ```

   桌面端也通用，把扩展导出的文件重定向即可：
   `python -m core.tools.cookie_import bilibili < ~/Downloads/cookies.txt`

说明：

- 抖音登录后视频门禁所需的 `UIFID` + `ttwid`/`sessionid` 会天然齐全；失效的典型症状是脚本提示"jsvm 反爬挑战页 / Cookie 失效"或视频详情持续"非 JSON 响应（0 字节）"。
- 小红书除登录外，链接本身仍需带 `xsec_token`（2026-10 起裸 `explore/<id>` 链接一律 404，与 Cookie 无关）；从 App 分享的短链自带该参数。
- bilibili 的 Cookie 仅在下不了高清/大会员画质时才需要更新，普通视频可空。

### 检查 Cookie 是否齐全（cookie_check.py)

导入后或下载失败排查时，一条命令体检（纯离线，只看键名不看值）：

```bash
python -m core.tools.cookie_check                       # 检查 cookies/ 下所有已存在的 .txt
python -m core.tools.cookie_check douyin xiaohongshu   # 只查指定站点
```

- **douyin**：检查 `UIFID`（或 `UIFID_TEMP`）与 `ttwid`/`sessionid`（视频门禁）是否齐全；`msToken` 存在会提示"将被优先使用"
- **xiaohongshu**：检查 `a1`（核心身份键，正常约 52 位）与 `webId`/`web_session`
- 其余站点只报告文件存在性；有明确缺口（✗）时退出码为 1，可直接用于脚本判断

### 手机号验证码半自动登录（cookie_login.py，可选）

不想手动导 Cookie 时，用 Playwright 半自动登录刷新（需 `pip install playwright && playwright install chromium`）：

```bash
python -m core.tools.cookie_login douyin                 # 无头自动完成到「输入短信验证码」，终端输入即可
python -m core.tools.cookie_login xiaohongshu --headed   # 遇滑块/人机校验时弹窗手动完成（可扫码）
```

登录成功后自动按站点所需格式写入 `cookies/<站点>.txt`，支持 douyin / xiaohongshu / bilibili。

### 关于抖音 / 小红书 / Instagram 的 App

这些 App 使用 **SSL Pinning**（证书绑定），即使装了抓包根证书也会拒绝 TLS 握手，**普通抓包拿不到 Cookie**。可行路径：

- 抓这些站点的**网页版** Cookie（浏览器访问，不走 App 的 Pinning），用上面的桌面/移动方法。
- 绕过 App 的 Pinning 需 root/越狱 + Frida 等工具，不在本工具支持范围内。

### 通用注意

- Cookie 是敏感凭据，`cookies/` 已在 `.gitignore` 中，**切勿提交**。
- Cookie 有有效期，失效后需重新获取（典型症状：bilibili 下不到高清、instagram 报“需要登录”、小红书提示风控）。
- 各站点需要的关键 Cookie：bilibili → `SESSDATA`；instagram → `sessionid`；小红书 → `web_session` / `a1`；抖音 → **`ttwid` 或 `sessionid` 至少一个有效** + 视频签名用的 `UIFID`（2026-10 实测门禁；ttwid 是设备级短期 token 会自然过期，`s_v_web_id` 服务端不校验）。抖音视频详情持续返回空响应（日志提示"非 JSON 响应（0 字节）"）= ttwid/sessionid 失效，刷新 Cookie 即可。
- 抖音图文笔记的是手写解析后端，与 yt-dlp 共用 `cookies/douyin.txt`：Netscape 格式或 `Cookie:` 请求头整段都可以（脚本经 `cookie_text_to_header` 自动识别转换），但**必须含有效登录态**，否则抖音返回 jsvm 反爬挑战页。

## 上传到 Telegram（可选）

**两种使用形态**：不把 Telegram 配置（config.json 的 telegram 段）填上时，仓库就是纯本地命令行下载器；配置后即可用下面两个 Telegram 工具（共用 config.json 的 telegram 段，已 gitignore）。代码也按此分层：下载核心在 `core/`，Telegram 功能在 `telegram/`（仅依赖 core，单向），入口一律 `python -m 模块路径`（如 `python -m core.downloader`、`python -m telegram.bot`），无 shim 文件。

- **`telegram/bot.py` — 聊天闭环机器人（Termux 常驻）**：给 bot 发链接（可带分享文案），自动下载并把产物回传到聊天——图片与视频混排按 10 个/组发相册（视频笔记的封面与视频本体合成一条消息），HEIC/超标图走文件；caption 自动带 **`#作者` `#站点`** 标签（Telegram 原生可点击、可搜索，方便按作者翻记录）。重复链接提示"已下载过"，超限文件提示跳过。`allowed_users` 白名单外的用户不会消耗你的下载（被拒时会回显其用户 id，方便加白）。下载器返回结构化结果，缺少 Cookie、解析失败、无媒体会分别给出原因，不再把失败误报成"此前已下载过"。**群组**使用需把 bot 设为管理员或经 @BotFather 关闭 Group Privacy，否则它收不到群里的普通消息；**频道**里发链接同样会接力（bot 需为频道管理员，频道消息不做用户白名单——能发帖的只有管理员）
- **`telegram/upload.py` — 命令行直发**：把本地已有目录/文件传上去（`--document` 原图不压缩，`--caption` 自定义说明，`--config` 指定配置）

**配置步骤**（一次，终身有效）：

1. Telegram 找 **@BotFather** → `/newbot` 拿 bot token
2. 给 bot 发任意一条消息，浏览器打开 `https://api.telegram.org/bot<token>/getUpdates` 取你的用户 id（`message.from.id`）
3. 首跑任一脚本自动生成 `config.json` 模板，在 telegram 段填入：
   - `bot_token`：上面拿到的 token
   - `chat_id`：直发模式默认聊天（私聊你的用户 id，或频道 `@名字`）
   - `allowed_users`：允许用 bot 的用户 id 列表（一般就你自己）

   旧版 telegram.json 会被自动识别兜底读取，老用户无需手动迁移。

```bash
tmux new -s tg      # 常驻推荐
python -m telegram.bot      # 机器人模式：等消息
# 或
python -m telegram.upload download/xiaohongshu/某作者/某笔记_xxx/   # 直发模式
```

限制：官方 Bot API 上传单文件 **≤50MB**；能预览的尽量预览——HEIC 与超标大图自动转码/压缩为 ≤10MB JPEG 走相册（HEIC 三路转码：Pillow/pillow-heif 优先；次选 libheif CLI（新版 `heif-dec`、老名 `heif-convert`），仅桌面发行版附带，**Termux 的 libheif 包不带 CLI**；末路 ffmpeg 单帧导出——**Termux `pkg install ffmpeg` 一条命令即可**，与 >50MB 视频压缩同款依赖；三路全缺才回退按文件发送），>50MB 视频在本机有 ffmpeg 时压到 ~49MB 再发，没有则维持超限跳过；Telegram 上的是浏览版，原始字节始终在本地 `download/`。bot 模式的过程提示消息（开始下载/没识别到链接/失败提示等）默认 5 分钟后自动删除（`config.json` telegram 段的 `process_message_ttl_seconds`，0 关闭；相册/视频等媒体消息永不删）。需要 ≤2GB 就得自建 telegram-bot-api 服务，不在本仓库范围。

## Termux 与 TLS 指纹伪装（curl_cffi）

curl_cffi 核心是编译好的 C 扩展，PyPI 只发 glibc/macOS/Windows 的预编译 wheel——Android 是 Bionic libc，装不上或装上后被 Termux 的 Python 滚动升级（3.13→3.14）震碎 ABI。tur 第三方源也没有 curl-impersonate 系包。三个解决方案（按推荐排序）：

1. **什么都不用做（当前默认）**：`core/http_client.py` 自动降级系统 curl，只加 `--http2` + 完整 Chrome 请求头。2026-10 实测抖音签名 API / 小红书 / bilibili 全链路均不校验 JA3 指纹，伪装收益为零。
2. **源码重建**：`bash setup_termux.sh`（内含检测 + `pip install --force-reinstall curl_cffi`）。编过即恢复 chrome150 指纹；编不过自动保持降级，功能无损。
3. **proot-distro 一劳永逸**：`pkg install proot-distro && proot-distro install debian`，进 Debian（glibc aarch64）后 `pip install -r requirements.txt` 直接命中 manylinux wheel，且 Debian 的 Python 由 apt 钉版不漂移。在 `/sdcard` 下 clone 并运行，下载产物直接进共享存储。

## 测试

```bash
pip install pytest
python -m pytest -q    # 裸 pytest -q 亦可（根目录 conftest.py 引导 sys.path）
```

## License

见 [LICENSE](LICENSE)。
