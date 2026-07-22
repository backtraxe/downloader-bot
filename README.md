# downloader-bot

交互式命令行媒体下载工具，输入网页链接即可抓取图片/视频到本地。

## 安装

```bash
pip install -r requirements.txt
```

## 使用

### `downloader.py` — 统一入口（推荐）

```bash
python downloader.py
```

输入链接后脚本按 URL 自动识别站点并分发到对应下载器，无需手动选脚本：

| 站点 | 域名 | 后端 | Cookie |
|------|------|------|--------|
| 小红书 | `xiaohongshu.com` / `xhslink.com` | 手写 `__INITIAL_STATE__` 解析 | 建议 |
| YouTube | `youtube.com` / `youtu.be` | yt-dlp | 通常不需要 |
| Bilibili | `bilibili.com` / `b23.tv` | yt-dlp | 建议（下高清需要） |
| 抖音 | `douyin.com` / `v.douyin.com` | yt-dlp | 建议 |
| Instagram | `instagram.com` / `instagr.am` | yt-dlp | 强烈建议（否则大概率失败） |
| X(Twitter) | `x.com` / `twitter.com` / `t.co` | yt-dlp | 通常不需要 |
| 其他网站 | 任意 | 通用静态网页抓取 | 按域名命名 |

- Cookie 从 `cookies/<站点名>.txt` 读取
- 输出到 `download/<站点>[/<作者>]/<标题>.<ext>`
- 短链（`b23.tv` / `v.douyin.com` / `youtu.be` / `t.co` / `instagr.am` 等）会自动展开为最终直链后再下载，避免 DNS 解析失败

三个下载脚本也可单独运行（`python xhs_downloader.py` / `python 1024_downloader.py`），行为与统一入口一致。

#### 小红书后端

- 手写解析 `window.__INITIAL_STATE__`，提取图片列表和视频流（比 yt-dlp 对小红书更可控）
- 支持 `xhslink.com` 短链与 App 分享链接（含 `xsec_token`），脚本会跟随重定向到 `xiaohongshu.com` 后再解析
- 需要在 `cookies/xiaohongshu.txt` 中填入浏览器 Cookie；文件不存在或为空时脚本会自动创建并提示

#### 通用网页后端

- 使用 `curl_cffi` 的 `impersonate="chrome110"` 做 TLS 指纹伪装，绕过防盗链 / CDN 拦截
- `curl_cffi` 不可用时自动降级为系统 `curl` 命令（由 `http_client.py` 统一封装），Termux 等环境同样可用
- 深度扫描 `<img>/<source>/<video>` 的懒加载属性（`data-src`、`data-original`、`ess-data` 等）、`<a href>` 直链、以及行内 `style` 中的 `background-image`
- 下载时带 `Referer`（破解防盗链的关键），并发下载（`max_workers=5`）

## 目录约定

- `download/` — 下载输出目录（已 gitignore）
- `cookies/` — 各站点 Cookie 文件，命名规则为 `cookies/<站点名>.txt`（已 gitignore）

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
2. **yt-dlp 直接从浏览器读取**（无需手动填文件）：在 `downloader.py` 的 `build_ydl_opts` 里加 `"cookiesfrombrowser": ("chrome",)`（支持 `chrome`/`firefox`/`edge`/`safari` 等），需关掉浏览器进程。
3. **从抓包/手头的 `Cookie:` 头字符串转换**：若你只有请求头字符串（移动端抓包常见），用本仓库自带的转换函数生成 Netscape 文件：

   ```bash
   python -c "from utils import cookie_header_to_netscape as f; f('SESSDATA=abc; bili_jct=xyz', '.bilibili.com', 'cookies/bilibili.txt')"
   ```

> ⚠️ 填了 `Cookie:` 头字符串会导致 yt-dlp **静默忽略** cookie，下载受限内容仍报“需要登录”。

### `xhs_downloader.py` — Cookie 请求头字符串

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

- **要 Netscape 文件（yt-dlp 直接用）**：用 **Firefox for Android** + [Android Cookie Importer](https://addons.mozilla.org/en-US/android/search/?q=cookies.txt) 扩展，登录后导出 `cookies.txt`，粘进 `cookies/<站点名>.txt`。
- **要请求头字符串**：用 [Reqable](https://reqable.com/)（原 HttpCanary，本地 VPN + CA 证书）抓包，从请求里复制 `Cookie` 头。

> Chrome/Edge 的 Cookie 在非 root 设备上无法直接读取（App 沙盒保护），不要尝试去翻系统文件。

### 关于抖音 / 小红书 / Instagram 的 App

这些 App 使用 **SSL Pinning**（证书绑定），即使装了抓包根证书也会拒绝 TLS 握手，**普通抓包拿不到 Cookie**。可行路径：

- 抓这些站点的**网页版** Cookie（浏览器访问，不走 App 的 Pinning），用上面的桌面/移动方法。
- 绕过 App 的 Pinning 需 root/越狱 + Frida 等工具，不在本工具支持范围内。

### 通用注意

- Cookie 是敏感凭据，`cookies/` 已在 `.gitignore` 中，**切勿提交**。
- Cookie 有有效期，失效后需重新获取（典型症状：bilibili 下不到高清、instagram 报“需要登录”、小红书提示风控）。
- 各站点需要的关键 Cookie：bilibili → `SESSDATA`；instagram → `sessionid`；小红书 → `web_session` / `a1`。

## 测试

```bash
pip install pytest
pytest -q
```

## License

见 [LICENSE](LICENSE)。
