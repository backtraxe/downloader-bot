# -*- coding: utf-8 -*-
"""双模块边界：core（下载核心）不依赖 telegram 包。"""

import re
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent / "core"
_TG_IMPORT = re.compile(r"^\s*(?:from|import)\s+telegram\b", re.M)


def test_core_no_telegram_imports():
    offenders = [
        p.name
        for p in CORE_DIR.glob("*.py")
        if _TG_IMPORT.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"core 模块不得 import telegram 包: {offenders}"


def test_import_core_downloader_pulls_no_telegram():
    sys.modules.pop("telegram.bot", None)
    sys.modules.pop("telegram.upload", None)
    import core.downloader  # noqa: F401

    assert "telegram.bot" not in sys.modules
    assert "telegram.upload" not in sys.modules
