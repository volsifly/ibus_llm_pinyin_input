#!/usr/bin/env python3
"""IBus AI Pinyin – 首选项配置窗口 (GTK 3)"""

import json
import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

CONFIG_PATH = os.path.expanduser("~/.config/ibus-ai-pinyin/config.json")

# ---------------------------------------------------------------------------
# config helpers (subset of ibus_ai_pinyin.config to avoid importing engine)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "api": {
        "base_url": "http://127.0.0.1:8080/v1",
        "api_key": "",
        "api_key_env": "OPENAI_API_KEY",
        "model": "qwen3-0.6b",
        "endpoint": "/chat/completions",
        "timeout_ms": 800,
        "temperature": 0.1,
        "top_p": 0.8,
        "max_tokens": 64,
        "stream": False,
        "proxy_enabled": False,
        "thinking": {"enabled": None, "type": "disabled"},
        "extra_body": {},
    },
    "input": {
        "max_buffer_length": 120,
        "candidate_page_size": 5,
        "default_mode": "zh",
        "toggle_key": {"enabled": True, "key": "space", "modifiers": ["Control"]},
    },
    "candidate": {"max_candidates": 5, "fallback_to_raw_pinyin": True},
    "cache": {"enabled": True, "path": "~/.config/ibus-ai-pinyin/cache.sqlite3"},
    "dictionary": {
        "enabled": True,
        "path": "~/.config/ibus-ai-pinyin/cache.sqlite3",
        "max_candidates": 5,
        "priority": 90,
        "match_compact_pinyin": True,
        "match_short": True,
        "auto_generate_pinyin": False,
    },
    "memory_dictionary": {
        "enabled": True,
        "path": "~/.config/ibus-ai-pinyin/cache.sqlite3",
        "auto_learn": True,
        "send_to_llm": True,
        "max_context_terms": 8,
        "exact_match_candidate": True,
        "auto_learn_min_han": 2,
        "auto_learn_max_han": 12,
        "default_weight": 80,
        "max_weight": 120,
        "record_corrections": True,
    },
    "debug": {"log_user_input": False, "log_model_output": False},
    "prompt": {
        "system": (
            "你是一个中文拼音输入法转换器。你的任务是把用户输入的拼音转换成最可能的中文候选。"
            "只输出 JSON 字符串数组，不要解释，不要 Markdown，不要代码块。最多输出 5 个候选。"
        ),
        "user_template": "拼音：{pinyin}\n请输出中文候选 JSON 数组。",
    },
}


def _load_config():
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        return _deep_copy(DEFAULT_CONFIG)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        user = json.load(f)
    return _deep_merge(DEFAULT_CONFIG, user)


def _save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _deep_copy(d):
    return json.loads(json.dumps(d))


def _deep_merge(base, override):
    result = _deep_copy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Widget helpers
# ---------------------------------------------------------------------------

def _label(text, tooltip=None):
    lbl = Gtk.Label(label=text, xalign=0)
    if tooltip:
        lbl.set_tooltip_text(tooltip)
    return lbl


def _add_row(grid, row, label_text, widget, tooltip=None):
    """Add a label (col 0) + widget (col 1) to a Gtk.Grid at *row*."""
    lbl = _label(label_text, tooltip)
    lbl.set_margin_end(8)
    grid.attach(lbl, 0, row, 1, 1)
    grid.attach(widget, 1, row, 1, 1)
    widget.set_hexpand(True)
    widget.set_margin_bottom(4)


# ---------------------------------------------------------------------------
# Preferences window
# ---------------------------------------------------------------------------

class PreferencesWindow(Gtk.Window):
    def __init__(self):
        Gtk.Window.__init__(self, title="AI 拼音输入法 — 首选项")
        self.set_default_size(620, 520)
        self.set_border_width(12)
        self._widgets = {}  # dotted-key → {"widget": w, "type": str|int|float|bool|json|combo|textview}
        self._toggle_modifier_checkboxes = {}  # modifier name → Gtk.CheckButton

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add(vbox)

        nb = Gtk.Notebook()
        vbox.pack_start(nb, True, True, 0)

        nb.append_page(self._build_api_tab(), Gtk.Label(label="API"))
        nb.append_page(self._build_input_tab(), Gtk.Label(label="输入"))
        nb.append_page(self._build_candidate_tab(), Gtk.Label(label="候选"))
        nb.append_page(self._build_advanced_tab(), Gtk.Label(label="高级"))

        # bottom buttons
        btn_box = Gtk.Box(spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        vbox.pack_end(btn_box, False, False, 0)

        save_btn = Gtk.Button(label="保存")
        save_btn.connect("clicked", self._on_save)
        btn_box.pack_start(save_btn, False, False, 0)

        cancel_btn = Gtk.Button(label="取消")
        cancel_btn.connect("clicked", lambda _: self.destroy())
        btn_box.pack_start(cancel_btn, False, False, 0)

        # load config and populate widgets
        self._config = _load_config()
        self._populate(self._config)

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------

    def _build_api_tab(self):
        grid = Gtk.Grid(column_spacing=8, row_spacing=2)
        grid.set_margin_top(8)
        grid.set_margin_start(8)
        r = 0

        _add_row(grid, r, "Base URL", self._entry("api.base_url", "http://127.0.0.1:8080/v1"),
                 "OpenAI-compatible API 地址"); r += 1

        key_entry = Gtk.Entry()
        key_entry.set_visibility(False)
        key_entry.set_placeholder_text("留空使用环境变量")
        _add_row(grid, r, "API Key", self._reg("api.api_key", key_entry, str))
        r += 1

        _add_row(grid, r, "API Key 环境变量", self._entry("api.api_key_env", "OPENAI_API_KEY"))
        r += 1
        _add_row(grid, r, "Model", self._entry("api.model", "qwen3-0.6b"))
        r += 1
        _add_row(grid, r, "Endpoint", self._entry("api.endpoint", "/chat/completions"))
        r += 1

        _add_row(grid, r, "Timeout (ms)", self._spin_int("api.timeout_ms", 100, 30000, 100))
        r += 1
        _add_row(grid, r, "Max Tokens", self._spin_int("api.max_tokens", 8, 2048, 8))
        r += 1
        _add_row(grid, r, "Temperature", self._spin_float("api.temperature", 0.0, 2.0, 0.05))
        r += 1
        _add_row(grid, r, "Top P", self._spin_float("api.top_p", 0.0, 1.0, 0.05))
        r += 1
        _add_row(grid, r, "Stream", self._switch("api.stream"))
        r += 1
        _add_row(grid, r, "Proxy", self._switch("api.proxy_enabled"),
                 "是否信任 HTTP_PROXY/HTTPS_PROXY 环境变量")
        r += 1

        # thinking.enabled: None / True / False via combo
        think_en = Gtk.ComboBoxText()
        think_en.append("default", "默认（不发送）")
        think_en.append("true", "启用")
        think_en.append("false", "禁用")
        _add_row(grid, r, "Thinking 开关", self._reg("api.thinking.enabled", think_en, "combo"), "None 表示请求中不包含该字段"); r += 1

        think_type = Gtk.ComboBoxText()
        think_type.append("disabled", "disabled")
        think_type.append("enabled", "enabled")
        _add_row(grid, r, "Thinking 类型", self._reg("api.thinking.type", think_type, "combo")); r += 1

        return self._scrolled(grid)

    def _build_input_tab(self):
        grid = Gtk.Grid(column_spacing=8, row_spacing=2)
        grid.set_margin_top(8)
        grid.set_margin_start(8)
        r = 0

        _add_row(grid, r, "缓冲区最大长度", self._spin_int("input.max_buffer_length", 10, 500, 10)); r += 1
        _add_row(grid, r, "每页候选数", self._spin_int("input.candidate_page_size", 3, 20, 1)); r += 1

        mode = Gtk.ComboBoxText()
        mode.append("zh", "中文（zh）")
        mode.append("en", "英文（en）")
        _add_row(grid, r, "默认模式", self._reg("input.default_mode", mode, "combo")); r += 1

        # Toggle key
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup("<b>切换快捷键</b>")
        lbl.set_margin_top(8)
        grid.attach(lbl, 0, r, 2, 1); r += 1

        _add_row(grid, r, "  启用", self._switch("input.toggle_key.enabled")); r += 1

        _add_row(grid, r, "  按键", self._entry("input.toggle_key.key", "space")); r += 1

        mod_box = Gtk.Box(spacing=4)
        for name in ("Control", "Alt", "Shift", "Super", "Meta"):
            cb = Gtk.CheckButton(label=name)
            cb.set_margin_end(4)
            mod_box.pack_start(cb, False, False, 0)
            self._toggle_modifier_checkboxes[name] = cb
        grid.attach(mod_box, 1, r, 1, 1); r += 1

        return self._scrolled(grid)

    def _build_candidate_tab(self):
        grid = Gtk.Grid(column_spacing=8, row_spacing=2)
        grid.set_margin_top(8)
        grid.set_margin_start(8)
        r = 0

        _add_row(grid, r, "最大候选词数量", self._spin_int("candidate.max_candidates", 1, 20, 1),
                 "每次请求返回的候选词上限"); r += 1
        _add_row(grid, r, "回退到原始拼音", self._switch("candidate.fallback_to_raw_pinyin"),
                 "LLM 无结果时是否提交原始拼音"); r += 1

        return self._scrolled(grid)

    def _build_advanced_tab(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(8)
        vbox.set_margin_start(8)

        vbox.pack_start(self._section("缓存 (Cache)", self._build_cache_grid()), False, False, 0)
        vbox.pack_start(self._section("领域词典 (Dictionary)", self._build_dict_grid()), False, False, 0)
        vbox.pack_start(self._section("用户记忆 (Memory)", self._build_memory_grid()), False, False, 0)
        vbox.pack_start(self._section("调试 (Debug)", self._build_debug_grid()), False, False, 0)
        vbox.pack_start(self._section("提示词 (Prompt)", self._build_prompt_grid()), True, True, 0)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add_with_viewport(vbox)
        return sw

    def _build_cache_grid(self):
        g = Gtk.Grid(column_spacing=8, row_spacing=2)
        r = 0
        _add_row(g, r, "启用", self._switch("cache.enabled")); r += 1
        _add_row(g, r, "路径", self._entry("cache.path")); r += 1
        return g

    def _build_dict_grid(self):
        g = Gtk.Grid(column_spacing=8, row_spacing=2)
        r = 0
        _add_row(g, r, "启用", self._switch("dictionary.enabled")); r += 1
        _add_row(g, r, "路径", self._entry("dictionary.path")); r += 1
        _add_row(g, r, "最大候选", self._spin_int("dictionary.max_candidates", 1, 20, 1)); r += 1
        _add_row(g, r, "优先级", self._spin_int("dictionary.priority", 0, 200, 10)); r += 1
        _add_row(g, r, "匹配压缩拼音", self._switch("dictionary.match_compact_pinyin")); r += 1
        _add_row(g, r, "匹配缩写", self._switch("dictionary.match_short")); r += 1
        _add_row(g, r, "自动生成拼音", self._switch("dictionary.auto_generate_pinyin")); r += 1
        return g

    def _build_memory_grid(self):
        g = Gtk.Grid(column_spacing=8, row_spacing=2)
        r = 0
        _add_row(g, r, "启用", self._switch("memory_dictionary.enabled")); r += 1
        _add_row(g, r, "路径", self._entry("memory_dictionary.path")); r += 1
        _add_row(g, r, "自动学习", self._switch("memory_dictionary.auto_learn")); r += 1
        _add_row(g, r, "发送给 LLM", self._switch("memory_dictionary.send_to_llm")); r += 1
        _add_row(g, r, "最大上下文词数", self._spin_int("memory_dictionary.max_context_terms", 0, 50, 1)); r += 1
        _add_row(g, r, "精确匹配候选", self._switch("memory_dictionary.exact_match_candidate")); r += 1
        _add_row(g, r, "自动学习最小字数", self._spin_int("memory_dictionary.auto_learn_min_han", 1, 20, 1)); r += 1
        _add_row(g, r, "自动学习最大字数", self._spin_int("memory_dictionary.auto_learn_max_han", 1, 50, 1)); r += 1
        _add_row(g, r, "默认权重", self._spin_int("memory_dictionary.default_weight", 1, 200, 10)); r += 1
        _add_row(g, r, "最大权重", self._spin_int("memory_dictionary.max_weight", 1, 500, 10)); r += 1
        _add_row(g, r, "记录修正", self._switch("memory_dictionary.record_corrections")); r += 1
        return g

    def _build_debug_grid(self):
        g = Gtk.Grid(column_spacing=8, row_spacing=2)
        r = 0
        _add_row(g, r, "记录用户输入", self._switch("debug.log_user_input")); r += 1
        _add_row(g, r, "记录模型输出", self._switch("debug.log_model_output")); r += 1
        return g

    def _build_prompt_grid(self):
        g = Gtk.Grid(column_spacing=8, row_spacing=2)
        g.set_hexpand(True)
        g.set_vexpand(True)
        r = 0

        lbl = _label("System Prompt")
        g.attach(lbl, 0, r, 1, 1); r += 1

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(80)
        tv = Gtk.TextView()
        tv.set_wrap_mode(Gtk.WrapMode.WORD)
        sw.add(tv)
        g.attach(sw, 0, r, 2, 1); r += 1
        self._reg("prompt.system", tv, "textview")

        _add_row(g, r, "User Template", self._entry("prompt.user_template")); r += 1
        return g

    # ------------------------------------------------------------------
    # Widget factory helpers
    # ------------------------------------------------------------------

    def _reg(self, key, widget, typ):
        self._widgets[key] = {"widget": widget, "type": typ}
        return widget

    def _entry(self, key, placeholder=""):
        w = Gtk.Entry()
        if placeholder:
            w.set_placeholder_text(placeholder)
        return self._reg(key, w, str)

    def _spin_int(self, key, lo, hi, step):
        adj = Gtk.Adjustment(value=0, lower=lo, upper=hi, step_increment=step)
        w = Gtk.SpinButton(adjustment=adj, climb_rate=0, digits=0)
        return self._reg(key, w, int)

    def _spin_float(self, key, lo, hi, step):
        adj = Gtk.Adjustment(value=0.0, lower=lo, upper=hi, step_increment=step)
        w = Gtk.SpinButton(adjustment=adj, climb_rate=0, digits=2)
        return self._reg(key, w, float)

    def _switch(self, key):
        w = Gtk.Switch()
        return self._reg(key, w, bool)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _section(title, child):
        frame = Gtk.Frame(label=title)
        frame.set_margin_bottom(4)
        frame.add(child)
        return frame

    @staticmethod
    def _scrolled(child):
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add_with_viewport(child)
        return sw

    # ------------------------------------------------------------------
    # Populate widgets from config
    # ------------------------------------------------------------------

    def _populate(self, cfg):
        for key, info in self._widgets.items():
            w = info["widget"]
            val = self._get_nested(cfg, key)
            typ = info["type"]
            if typ == str:
                w.set_text(str(val) if val is not None else "")
            elif typ == int:
                w.set_value(float(val) if val is not None else 0)
            elif typ == float:
                w.set_value(float(val) if val is not None else 0.0)
            elif typ == bool:
                w.set_active(bool(val))
            elif typ == "combo":
                if val is None:
                    w.set_active_id("default")
                elif val is True:
                    w.set_active_id("true")
                elif val is False:
                    w.set_active_id("false")
                else:
                    w.set_active_id(str(val))
            elif typ == "textview":
                buf = w.get_buffer()
                buf.set_text(str(val) if val is not None else "")

        # toggle key modifiers checkboxes
        modifiers = self._get_nested(cfg, "input.toggle_key.modifiers") or []
        for name, cb in self._toggle_modifier_checkboxes.items():
            cb.set_active(name in modifiers)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _on_save(self, _btn):
        cfg = _deep_copy(DEFAULT_CONFIG)

        for key, info in self._widgets.items():
            w = info["widget"]
            typ = info["type"]
            try:
                if typ == str:
                    val = w.get_text()
                elif typ == int:
                    val = int(w.get_value())
                elif typ == float:
                    val = float(w.get_value())
                elif typ == bool:
                    val = w.get_active()
                elif typ == "combo":
                    aid = w.get_active_id()
                    if aid == "default":
                        val = None
                    elif aid == "true":
                        val = True
                    elif aid == "false":
                        val = False
                    else:
                        val = aid
                elif typ == "textview":
                    buf = w.get_buffer()
                    val = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
                else:
                    continue
            except Exception:
                continue
            self._set_nested(cfg, key, val)

        # toggle key modifiers
        mods = [n for n, cb in self._toggle_modifier_checkboxes.items() if cb.get_active()]
        self._set_nested(cfg, "input.toggle_key.modifiers", mods if mods else ["Control"])

        # validate
        base_url = self._get_nested(cfg, "api.base_url")
        if not base_url or not base_url.strip():
            self._show_error("API Base URL 不能为空")
            return

        try:
            _save_config(cfg)
        except OSError as e:
            self._show_error(f"保存配置失败: {e}")
            return

        self._ask_restart()

    def _ask_restart(self):
        dlg = Gtk.MessageDialog(
            transient_for=self, flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.NONE,
            text="配置已保存",
        )
        dlg.format_secondary_text("需要重启 IBus 使配置生效。是否立即重启？")
        dlg.add_button("稍后", Gtk.ResponseType.NO)
        dlg.add_button("立即重启", Gtk.ResponseType.YES)
        resp = dlg.run()
        dlg.destroy()
        if resp == Gtk.ResponseType.YES:
            subprocess.run(["ibus", "restart"], check=False)
            subprocess.Popen(["ibus", "engine", "ai-pinyin"])

    def _show_error(self, msg):
        dlg = Gtk.MessageDialog(
            transient_for=self, flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=msg,
        )
        dlg.run()
        dlg.destroy()

    # ------------------------------------------------------------------
    # Nested dict helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_nested(d, dotted_key):
        parts = dotted_key.split(".")
        cur = d
        for p in parts:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
        return cur

    @staticmethod
    def _set_nested(d, dotted_key, value):
        parts = dotted_key.split(".")
        cur = d
        for p in parts[:-1]:
            if p not in cur or not isinstance(cur[p], dict):
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = value


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if "-h" in sys.argv or "--help" in sys.argv:
        print("Usage: python3 preferences.py")
        print("IBus AI Pinyin 输入法首选项配置窗口")
        sys.exit(0)

    win = PreferencesWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
