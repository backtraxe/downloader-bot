# -*- coding: utf-8 -*-
"""core 各下载模块共用的工具函数：URL 归一化、文件名清洗、原子去重、
扩展名推断、日志配置、cookie 格式转换等。

集中在此以避免各模块各写一份且实现不一致。"""

import contextlib
import io
import itertools
import logging
import os
import re
from urllib.parse import urljoin, urlparse

# Pillow 为可选依赖：Termux 上可能没有 wheel（需 pkg install python-pillow 或自行编译），
# 缺 Pillow 时 get_image_dimensions 返回 None，调用方自动回退体积阈值判定。
try:
    from PIL import Image as _PILImage
except ImportError:
    _PILImage = None

# 文件名中需要剔除的非法字符（跨平台：路径分隔符 + Windows 保留字符
# + 控制字符——小红书 desc 等来源会带原始 Tab，控制符留在目录名里会
# 破坏 shell/传输场景）
_ILLEGAL_FILENAME_CHARS = re.compile(r"[\x00-\x1f\x7f\\/*?:\"<>|]")

# 常见媒体扩展名白名单，用于从 URL 推断类型
_EXT_BY_URL = re.compile(
    r"\.(jpe?g|png|gif|webp|hei[cf]|mp4|mov|webm|avi|mkv)(?:\?|#|$)", re.IGNORECASE
)

# Content-Type -> 扩展名映射
_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
    "image/heic": ".heic",
    "image/heif": ".heic",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-msvideo": ".avi",
    "video/x-matroska": ".mkv",
}

# 被防盗链/风控拦截时常返回的 HTML content-type
_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

# 匹配 http(s):// 开头的完整 URL（到首个空白字符为止）
_URL_PATTERN = re.compile(r"https?://[^\s]+")

# URL 尾部需要剔除的字符：中日韩文字、中文标点、英文标点、括号引号等
# 这些字符不会出现在合法 URL 末尾，通常来自分享文案的上下文
_URL_TRAILING_CHARS = re.compile(
    r"[\u3000-\u9fff\uff00-\uffef，。、；：！？）》」』）\"'\]\]>}]+$"
)


# 请求级日志 ID：一个链接的处理（下载→上传）共享同一 [rNNNN]，
# grep 该 ID 即可取出完整轨迹。串行请求模型（CLI/bot 均逐条处理）下
# 模块级全局即可——下载线程池在请求进行中写日志，读到的就是当前 ID。
_request_counter = itertools.count(1)
_current_request_id = None


def current_request_id():
    """返回当前活动请求的日志 ID；无活动请求时为 None。"""
    return _current_request_id


@contextlib.contextmanager
def request_scope():
    """开启一段请求日志作用域：期间所有日志行带同一 [rNNNN] 前缀。

    已有活动作用域时复用外层 ID（嵌套不换 ID）——bot 的单条消息处理
    包住 dispatch_url 时，bot 侧日志与下载器日志同属一个请求。
    """
    global _current_request_id
    if _current_request_id is not None:
        yield _current_request_id
        return
    rid = f"r{next(_request_counter):04d}"
    _current_request_id = rid
    try:
        yield rid
    finally:
        _current_request_id = None


class _RequestIdFilter(logging.Filter):
    """把当前请求 ID 注入每条日志记录；无活动请求时置空，格式与原来一致。

    挂在 handler 上（不是 logger）——对所有 logger 的记录生效。
    """

    def filter(self, record):
        record.reqid = f"[{_current_request_id}] " if _current_request_id else ""
        return True


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """配置根日志格式，返回 logger。幂等——重复调用不会叠加 handler。

    日志行带请求 ID 段（由 _RequestIdFilter 注入）：处理链接期间为
    [rNNNN]，其余时间为空，grep 该 ID 可串联单个请求的全部日志。
    """
    root = logging.getLogger()
    # 清理已存在 handler，避免重复调用叠加输出
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(reqid)s%(name)s: %(message)s", "%H:%M:%S"
        )
    )
    root.addHandler(handler)
    root.setLevel(level)
    return logging.getLogger("downloader")


def normalize_url(url, page_url: str) -> str:
    """把相对/协议相对 URL 归一化为绝对 URL；data: URI 与空值原样返回。"""
    if not url:
        return ""
    url = url.strip()
    if not url or url.startswith("data:"):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urljoin(page_url, url)


def extract_urls(text):
    """从任意文本中提取所有 http(s) URL，去重并保持出现顺序。

    用户可能粘贴分享文案而非纯 URL（如「6 发布了小红书笔记 http://xhslink.com/o/abc 快来看」），
    本函数从混合文本中提取 URL，剔除尾部的中文/标点等上下文字符。
    返回去重后的 URL 列表；无匹配时返回空列表。
    """
    if not text:
        return []
    raw_urls = _URL_PATTERN.findall(text)
    seen = set()
    result = []
    for url in raw_urls:
        url = _URL_TRAILING_CHARS.sub("", url)
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def sanitize_filename(name: str, default: str = "untitled", max_len: int = 80) -> str:
    """清洗文件名：剔除路径分隔符与 Windows 保留字符，保留中文等非 ASCII，
    去首尾空白；空串/纯空白返回 default；超长截断。"""
    if not name:
        return default
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("", name).strip()
    if not cleaned:
        return default
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned


def guess_extension(url: str, content_type: str = "", default: str = ".jpg") -> str:
    """由 URL 后缀或 Content-Type 推断扩展名。
    URL 明确扩展名优先于 content_type（避免被防盗链 HTML 响应误导）；
    否则用 content_type；都没有则返回 default。"""
    if url:
        m = _EXT_BY_URL.search(url)
        if m:
            return "." + m.group(1).lower()
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct and ct not in _HTML_CONTENT_TYPES:
        ext = _EXT_BY_CONTENT_TYPE.get(ct)
        if ext:
            return ext
    return default


def get_image_dimensions(data: bytes):
    """从图片二进制数据解析 (width, height)，基于 Pillow（可选依赖）。

    解析不出（数据过短、格式不识别、头损坏、Pillow 未安装）返回 None，
    由调用方决定回退策略（如按文件体积）。
    """
    if not data or _PILImage is None:
        return None
    try:
        io_target = io.BytesIO(data)
        with _PILImage.open(io_target) as img:
            return img.size
    except Exception:  # noqa: BLE001 格式不识别/损坏统一返回 None
        return None


def extract_url_numeric_id(url):
    """从 URL 路径末段提取纯数字 ID（如酷安 /feed/74208176）。

    通用抓取页不依赖站点专属解析：末段为 >=5 位纯数字才采信，
    位数太短（多为页码/日期片段）或含其他字符一律返回 None，
    调用方据此决定是否在目录名中追加 _<id> 后缀。
    """
    path = urlparse(url).path.rstrip("/")
    last = path.rsplit("/", 1)[-1]
    if last.isdigit() and len(last) >= 5:
        return last
    return None


def is_html_content(content_type: str) -> bool:
    """判断响应是否为 HTML（通常意味着被防盗链/风控拦截）。"""
    ct = (content_type or "").split(";")[0].strip().lower()
    return ct in _HTML_CONTENT_TYPES


def is_partial_download(name: str) -> bool:
    """判断文件名是否为下载中断的残留临时文件。

    yt-dlp 落 `.part` / `.part-Frag*` / `.ytdl`，core 各后端 force 覆盖
    走 `.part` 临时文件——下载中断时残留，不是完整媒体，上传/回传应跳过。
    """
    return name.endswith((".part", ".ytdl")) or ".part-" in name


def unique_path(path: str) -> str:
    """对给定路径做原子去重：若已存在，追加 `_1`、`_2`… 后缀。
    通过 os.open(O_CREAT|O_EXCL) 创建占位文件保证并发下不撞名，
    返回的路径已被独占创建（空文件），调用方写入即可。"""
    if not os.path.exists(path):
        # 尝试原子创建占位，避免与并发线程竞争
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return path
        except FileExistsError:
            pass  # 落到下面的去重逻辑

    root, ext = os.path.splitext(path)
    idx = 1
    while True:
        candidate = f"{root}_{idx}{ext}"
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return candidate
        except FileExistsError:
            idx += 1


def normalize_xhs_state_json(raw: str) -> str:
    """把小红书 __INITIAL_STATE__ 里 JSON 值语境的 undefined 替换为 null。
    只替换 `:` 或 `[` 之后、作为独立 token 的 undefined，避免误伤正文/URL 中的 "undefined" 字样。"""
    return re.sub(r"(?<=[\[:\s,])\s*undefined\b", " null", raw)


def build_download_dir(site_name, title, author=None, download_dir="download"):
    """构造按站点归类的下载目录：download/<site>/<author>/<safe_title>。

    三个下载脚本共用，保证输出根目录口径一致（都按站点分子目录）。
    author 非空时多一层作者目录（"如果有的话"语义）；为空/None 时退化为
    download/<site>/<safe_title>，不产生 unknown/ 或 NA/ 占位目录。
    title 与 author 均来自网页、不可信——内部用 sanitize_filename 防御式清洗，
    确保不会因含路径分隔符而越层（目录穿越）；空标题兜底 untitled。
    返回相对路径，由调用方负责 makedirs。
    """
    parts = [download_dir, site_name]
    if author and author.strip():
        # author 非空（含非空白）才加层；纯空白归为无作者，避免 unknown/ 占位
        parts.append(sanitize_filename(author, default="unknown"))
    parts.append(sanitize_filename(title, default="untitled"))
    return os.path.join(*parts)


# Netscape cookie 文件里 expiration 用一个远期固定值（yt-dlp 不强制校验）。
# 2099-01-01 ≈ 4070908800。无法用 time.time()（脚本环境约束），故常量。
_NETSCAPE_EXPIRE = 4070908800


def cookie_header_to_netscape(header, domain, out_path=None):
    """把浏览器 `Cookie:` 请求头字符串转换成 yt-dlp 可读的 Netscape cookie 文件。

    header: 形如 "name1=value1; name2=value2" 的整段 Cookie 头
    domain: cookie 所属域名，如 ".youtube.com"（开头带点表示含子域）
    out_path: 给定则同时写入文件；返回文件内容字符串

    用于移动端抓包后把请求头字符串喂给 downloader.py（yt-dlp 需 Netscape 格式）。
    """
    lines = ["# Netscape HTTP Cookie File", "# This is generated by downloader-bot"]
    for pair in (header or "").split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name:
            continue
        # 字段：domain  include_subdomain  path  secure  expiration  name  value
        lines.append(
            "\t".join(
                [
                    domain,
                    "TRUE",
                    "/",
                    "TRUE",
                    str(_NETSCAPE_EXPIRE),
                    name,
                    value,
                ]
            )
        )
    content = "\n".join(lines) + "\n"
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
    return content


def cookie_text_to_header(text):
    """把 cookie 文件内容统一转成 `Cookie:` 请求头字符串。

    cookie_header_to_netscape 的逆操作：站点 cookie 文件（cookies/<站点>.txt）
    可能是 Netscape 格式（yt-dlp 用），也可能是浏览器复制的请求头整段；
    手写解析器（xhs/douyin）塞请求头时只需一种格式。规则：
    - 行首 # 或空行：忽略
    - Tab 分隔且 >=7 个字段的行：按 Netscape 解析，取 name/value 两段
    - 其他行：原样拼接（兼容把请求头整段直接存进文件的场景）
    """
    pairs = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        # Netscape 行：domain  flag  path  secure  expires  name  value
        if len(fields) >= 7:
            name, value = fields[5].strip(), fields[6].strip()
            if name:
                pairs.append(f"{name}={value}")
        else:
            pairs.append(line)
    return "; ".join(pairs)


def cookie_list_to_netscape(cookies, out_path=None):
    """把浏览器上下文的 cookie 字典列表（Playwright context.cookies()）转成
    Netscape cookie 文件内容，供 yt-dlp / curl_cffi 侧读取。

    cookies 元素为 {name, value, domain, path, expires, secure, ...} 字典
    （Playwright 结构；缺字段按宽松默认处理）。会话 cookie（expires<=0）
    用远期固定值兜底。out_path 给定则同时写入文件；返回文件内容字符串。
    """
    lines = ["# Netscape HTTP Cookie File", "# This is generated by downloader-bot"]
    for c in cookies or []:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        domain = c.get("domain") or ""
        path = c.get("path") or "/"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = c.get("expires") or 0
        try:
            expires = int(expires)
        except (TypeError, ValueError):
            expires = 0
        if expires <= 0:
            expires = _NETSCAPE_EXPIRE
        include_subdomain = "TRUE" if domain.startswith(".") else "FALSE"
        lines.append(
            "\t".join(
                [
                    domain,
                    include_subdomain,
                    path,
                    secure,
                    str(expires),
                    name,
                    str(c.get("value") or ""),
                ]
            )
        )
    content = "\n".join(lines) + "\n"
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
    return content


def cookie_list_to_header(cookies, domain_filter=None):
    """把浏览器上下文的 cookie 字典列表转成 `Cookie:` 请求头字符串。

    domain_filter 给定（如 "xiaohongshu.com"）时只保留 domain 命中该子串
    的 cookie——请求头只应携带目标站自身的凭据，避免跨站泄漏。
    """
    pairs = []
    for c in cookies or []:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        if domain_filter and domain_filter not in (c.get("domain") or ""):
            continue
        pairs.append(f"{name}={c.get('value') or ''}")
    return "; ".join(pairs)
