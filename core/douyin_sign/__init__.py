# -*- coding: utf-8 -*-
"""抖音风控签名三件套（vendored，Apache-2.0，各文件头注有溯源）。

a_bogus（bdms.js v1.0.1.19 逆向）+ SM3 + x-secsdk-web-signature，
供 douyin_downloader 的签名 detail API 使用。整体排除在 ruff 之外
（ruff.toml），保持与 Evil0ctal 上游原样，便于改版后 diff 重新同步。
"""
