# -*- coding: utf-8 -*-
"""仓库统一配置文件 config.json 的加载。

config.json 不只服务于 Telegram：任何需要用户级配置的模块都在此文件下
读写自己的顶层键。当前已有的顶层键：

  telegram    telegram/bot.py 与 telegram/upload.py 共用
              （bot_token / chat_id / allowed_users /
              process_message_ttl_seconds）

向后兼容：旧版扁平结构的 `telegram.json` 在 config.json 缺失时仍被读取，
等价于 {"telegram": <其内容>}；两者都没有时写模板并返回 None，由调用方
打印指引退出。路径均为 CWD 相对（脚本一律从仓库根运行）。
"""

import json
import os

CONFIG_PATH = "config.json"
LEGACY_TELEGRAM_PATH = "telegram.json"

# 文件级模板：新顶层键随需求往这里加，旧文件缺键由消费方自己判空
_TEMPLATE = {
    "telegram": {
        "bot_token": "在此填入 @BotFather 给的 token",
        "chat_id": 0,
        "allowed_users": [],
        "process_message_ttl_seconds": 300,
    },
}


def write_template(path=CONFIG_PATH):
    """在 path 写入配置模板（仅当不存在，绝不覆盖用户已有配置）。"""
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_TEMPLATE, f, ensure_ascii=False, indent=2)


def load_config(path=CONFIG_PATH, legacy_telegram_path=LEGACY_TELEGRAM_PATH):
    """加载统一配置，返回完整 dict；无可用配置时写模板并返回 None。

    解析顺序：config.json →（缺失时）旧版 telegram.json（视作 telegram 段）。
    """
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if os.path.exists(legacy_telegram_path):
        with open(legacy_telegram_path, encoding="utf-8") as f:
            return {"telegram": json.load(f)}
    write_template(path)
    return None
