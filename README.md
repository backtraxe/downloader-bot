# downloader-bot

两个交互式命令行媒体下载脚本，输入网页链接即可抓取图片/视频到本地。

## 安装

```bash
pip install -r requirements.txt
```

## 使用

两个脚本都是交互式 prompt，反复输入链接下载，输入 `q` 退出。

### `1024_downloader.py` — 通用网页媒体抓取

```bash
python 1024_downloader.py
```

- 使用 `curl_cffi` 的 `impersonate="chrome110"` 做 TLS 指纹伪装，绕过防盗链 / CDN 拦截
- 深度扫描 `<img>/<source>/<video>` 的懒加载属性（`data-src`、`data-original`、`ess-data` 等）、`<a href>` 直链、以及行内 `style` 中的 `background-image`
- 下载时带 `Referer`（破解防盗链的关键），并发下载（`max_workers=5`）
- 输出到 `download/<域名>_<标题前缀>/`

### `xhs_downloader.py` — 小红书笔记下载

```bash
python xhs_downloader.py
```

- 仅支持小红书（`xiaohongshu.com` / `xhslink.com`），其他站点（bilibili、douyin）目前未实现
- 抓取页面后正则提取 `window.__INITIAL_STATE__`，解析图片列表和视频流
- 需要在 `cookies/xiaohongshu.txt` 中填入浏览器 Cookie；文件不存在或为空时脚本会自动创建并提示

## 目录约定

- `download/` — 下载输出目录（已 gitignore）
- `cookies/` — 各站点 Cookie 文件，命名规则为 `cookies/<站点名>.txt`（已 gitignore）

## License

见 [LICENSE](LICENSE)。
