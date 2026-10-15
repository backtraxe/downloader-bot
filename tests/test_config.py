# -*- coding: utf-8 -*-
"""core/config.py 公共函数测试（tmp_path 显式传参，不碰真实 config.json）"""

import json

from core import config as core_config


def test_write_template_does_not_overwrite_existing(tmp_path):
    target = tmp_path / "config.json"
    target.write_text('{"telegram": {"bot_token": "123:real"}}', encoding="utf-8")
    core_config.write_template(str(target))
    assert (
        json.loads(target.read_text(encoding="utf-8"))["telegram"]["bot_token"]
        == "123:real"
    )


def test_load_config_neither_writes_template_and_returns_none(tmp_path):
    cfg_path = tmp_path / "config.json"
    result = core_config.load_config(str(cfg_path), str(tmp_path / "telegram.json"))
    assert result is None
    template = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "telegram" in template


def test_load_config_reads_config_json_as_is(tmp_path):
    cfg_path = tmp_path / "config.json"
    data = {"telegram": {"bot_token": "123:abc"}, "other": 1}
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    assert (
        core_config.load_config(str(cfg_path), str(tmp_path / "telegram.json")) == data
    )


def test_load_config_legacy_telegram_json_fallback(tmp_path):
    legacy = tmp_path / "telegram.json"
    legacy.write_text(json.dumps({"bot_token": "123:abc"}), encoding="utf-8")
    result = core_config.load_config(str(tmp_path / "config.json"), str(legacy))
    assert result == {"telegram": {"bot_token": "123:abc"}}


def test_load_config_config_json_wins_over_legacy(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"telegram": {"bot_token": "new"}}), encoding="utf-8"
    )
    legacy = tmp_path / "telegram.json"
    legacy.write_text(json.dumps({"bot_token": "old"}), encoding="utf-8")
    result = core_config.load_config(str(cfg_path), str(legacy))
    assert result == {"telegram": {"bot_token": "new"}}
