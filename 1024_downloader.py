# -*- coding: utf-8 -*-
"""兼容入口 shim：实现已迁至 core/1024_downloader.py。"""
from importlib import import_module

if __name__ == "__main__":
    import_module("core.1024_downloader").main()
