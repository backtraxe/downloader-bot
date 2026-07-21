# -*- coding: utf-8 -*-
"""统一 HTTP 客户端：curl_cffi 不可用时降级到系统 curl 命令（subprocess）。

提供与 requests 兼容的最小接口：Session.get() 返回的 Response 对象支持
.text / .headers / .iter_content() / .raise_for_status()，让调用方无感知。

Termux 上 curl_cffi 的编译扩展常因 Python 版本不匹配而加载失败
（如 _wrapper.abi3.so 链接 libpython3.13.so 但运行时是 3.14）。
系统 curl 命令由 libcurl 处理 TLS，不依赖 Python ABI，在 Termux 上天然可用。
"""
import logging
import os
import re
import shutil
import subprocess

logger = logging.getLogger("downloader")

# curl_cffi 是否可用——全局探测一次
try:
    from curl_cffi import requests as _cffi_requests
    _HAS_CURL_CFFI = True
except Exception as _cffi_err:  # noqa: BLE001
    _cffi_requests = None
    _HAS_CURL_CFFI = False
    logger.warning("curl_cffi 不可用，降级为系统 curl 命令。原因: %s", _cffi_err)

# 系统 curl 是否存在
_CURL_BIN = shutil.which("curl")
if not _CURL_BIN and not _HAS_CURL_CFFI:
    logger.error("系统 curl 命令也未找到，HTTP 请求将无法发送")

# curl-impersonate 风格的浏览器指纹参数（尽量贴近 Chrome 110）
_CURL_IMPERSONATE_FLAGS = [
    "--http2",
    "--tlsv1.3",
    "--ciphers",
    "TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:"
    "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
    "ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:"
    "ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305",
    "--curves",
    "X25519:secp256r1:secp384r1",
]


class CurlResponse:
    """用系统 curl 命令构造的伪 Response 对象，兼容 requests 接口。"""

    def __init__(self, status_code, headers, body, url=""):
        self.status_code = status_code
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self._body = body  # bytes
        self.url = url
        self.text = body.decode("utf-8", errors="replace")
        self.content = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code} for {self.url}")

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def json(self):
        import json
        return json.loads(self._body)


def _build_curl_args(url, method, headers, timeout, impersonate, stream, extra_flags=None):
    """组装 curl 命令行参数列表。"""
    args = [_CURL_BIN, "-s", "-S", "--show-error", "-X", method]
    args += [
        "-D", "-",          # 输出响应头到 stdout
        "--max-time", str(timeout),
        "-o", "-",          # 输出 body 到 stdout
    ]
    if impersonate:
        args += _CURL_IMPERSONATE_FLAGS
    for key, val in (headers or {}).items():
        args += ["-H", f"{key}: {val}"]
    if extra_flags:
        args += extra_flags
    args.append(url)
    return args


def _curl_request(url, method="GET", headers=None, timeout=15, impersonate=None, stream=False):
    """用系统 curl 发送请求并解析响应。"""
    args = _build_curl_args(url, method, headers, timeout, impersonate, stream)
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout + 10)
    except subprocess.TimeoutExpired:
        raise Exception(f"curl 请求超时（{timeout}s）：{url}")

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise Exception(f"curl 失败（返回码 {proc.returncode}）：{stderr}")

    raw = proc.stdout
    # 分离响应头和 body：第一个 \r\n\r\n 或 \n\n 之后是 body
    sep = b"\r\n\r\n"
    idx = raw.find(sep)
    if idx == -1:
        sep = b"\n\n"
        idx = raw.find(sep)
    if idx == -1:
        header_bytes = b""
        body = raw
    else:
        header_bytes = raw[:idx]
        body = raw[idx + len(sep):]

    # 解析状态行和头部
    header_text = header_bytes.decode("utf-8", errors="replace")
    lines = header_text.splitlines()
    status_code = 200
    resp_headers = {}
    for line in lines:
        line = line.strip()
        if line.startswith("HTTP/"):
            parts = line.split(None, 2)
            if len(parts) >= 2:
                try:
                    status_code = int(parts[1])
                except ValueError:
                    pass
            continue
        m = re.match(r"^([^:]+):\s*(.*)$", line)
        if m:
            resp_headers[m.group(1).strip().lower()] = m.group(2).strip()

    return CurlResponse(status_code, resp_headers, body, url)


class Session:
    """统一 Session：curl_cffi 可用时委托给它，否则用系统 curl。"""

    def __init__(self):
        if _HAS_CURL_CFFI:
            self._cffi = _cffi_requests.Session()
        else:
            self._cffi = None

    def get(self, url, **kwargs):
        if self._cffi is not None:
            return self._cffi.get(url, **kwargs)
        # 从 kwargs 提取我们支持的参数
        headers = kwargs.get("headers")
        timeout = kwargs.get("timeout", 15)
        impersonate = kwargs.get("impersonate")
        stream = kwargs.get("stream", False)
        return _curl_request(url, "GET", headers, timeout, impersonate, stream)

    def close(self):
        if self._cffi is not None:
            self._cffi.close()


# 模块级便利函数
def get(url, **kwargs):
    """发起 GET 请求，自动选择可用后端。"""
    if _HAS_CURL_CFFI:
        return _cffi_requests.get(url, **kwargs)
    headers = kwargs.get("headers")
    timeout = kwargs.get("timeout", 15)
    impersonate = kwargs.get("impersonate")
    stream = kwargs.get("stream", False)
    return _curl_request(url, "GET", headers, timeout, impersonate, stream)
