#!/usr/bin/env python3
import copy
import json
import os
import subprocess
import sys
import time
import uuid

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from ibus_ai_pinyin.config import (
    DEFAULT_CONFIG,
    CONFIG_PATH,
    LLM_PROFILE_API_KEYS,
    deep_merge,
    load_config,
    load_user_config,
    save_user_config,
)
from ibus_ai_pinyin.stats import LLMStatsStore


LOG_PATH = os.path.expanduser("~/.cache/ibus-ai-pinyin/engine.log")
PROFILE_API_KEYS = LLM_PROFILE_API_KEYS
GLOBAL_API_KEYS = (
    "timeout_ms", "stream_timeout_ms", "temperature", "top_p", "max_tokens",
    "stream", "proxy_enabled",
)


class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="AI 拼音输入法设置")
        self.set_default_size(920, 680)
        self.raw_config = load_user_config()
        self.effective_config = load_config()
        self.global_api = deep_merge(DEFAULT_CONFIG["api"], self.raw_config.get("api", {}))
        self.profiles = copy.deepcopy(self.raw_config.get("llm_profiles") or [])
        if not self.profiles:
            self.profiles = [{
                "id": "default",
                "api": {
                    key: copy.deepcopy(self.global_api.get(key))
                    for key in PROFILE_API_KEYS if key in self.global_api
                },
            }]
        for profile in self.profiles:
            profile["api"] = {
                key: copy.deepcopy((profile.get("api") or {}).get(key))
                for key in PROFILE_API_KEYS if key in (profile.get("api") or {})
            }
        self.active_profile_id = self.raw_config.get("active_llm_profile") or self.profiles[0]["id"]
        if not any(item.get("id") == self.active_profile_id for item in self.profiles):
            self.active_profile_id = self.profiles[0]["id"]
        self.profile_fields = {}
        self.global_fields = {}
        self.loaded_profile_index = None
        self.reloading_profiles = False
        self.build_ui()
        self.reload_profile_list()
        self.load_prompt()
        self.refresh_logs()
        self.refresh_stats()

    def build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_margin_top(12)
        root.set_margin_bottom(12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        self.set_child(root)

        switcher = Gtk.StackSwitcher()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        switcher.set_stack(self.stack)
        root.append(switcher)
        root.append(self.stack)
        self.stack.set_vexpand(True)

        self.stack.add_titled(self.build_llm_page(), "llm", "LLM 配置")
        self.stack.add_titled(self.build_prompt_page(), "prompt", "提示词")
        self.stack.add_titled(self.build_log_page(), "logs", "运行日志")
        self.stack.add_titled(self.build_stats_page(), "stats", "数据统计")

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_halign(Gtk.Align.END)
        self.status_label = Gtk.Label(label="")
        self.status_label.set_hexpand(True)
        self.status_label.set_halign(Gtk.Align.START)
        actions.append(self.status_label)
        save = Gtk.Button(label="保存")
        save.connect("clicked", self.on_save, False)
        actions.append(save)
        save_restart = Gtk.Button(label="保存并重启 IBus")
        save_restart.add_css_class("suggested-action")
        save_restart.connect("clicked", self.on_save, True)
        actions.append(save_restart)
        root.append(actions)

    def build_llm_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.append(Gtk.Label(label="当前模型"))
        self.profile_model = Gtk.StringList()
        self.profile_dropdown = Gtk.DropDown(model=self.profile_model)
        self.profile_dropdown.set_hexpand(True)
        self.profile_dropdown.connect("notify::selected", self.on_profile_selected)
        top.append(self.profile_dropdown)
        add = Gtk.Button(label="新增")
        add.connect("clicked", self.on_add_profile)
        top.append(add)
        delete = Gtk.Button(label="删除")
        delete.connect("clicked", self.on_delete_profile)
        top.append(delete)
        page.append(top)

        page.append(Gtk.Label(label="模型连接", xalign=0, css_classes=["heading"]))
        profile_grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        page.append(profile_grid)
        profile_specs = [
            ("base_url", "Base URL", "entry"),
            ("model", "模型", "entry"),
            ("api_key", "API Key", "password"),
        ]
        self.add_config_fields(profile_grid, profile_specs, self.profile_fields)

        page.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        page.append(Gtk.Label(label="通用请求配置", xalign=0, css_classes=["heading"]))
        global_grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        page.append(global_grid)
        global_specs = [
            ("timeout_ms", "超时 ms", "int"),
            ("stream_timeout_ms", "流式超时 ms", "int"),
            ("temperature", "Temperature", "float"),
            ("top_p", "Top P", "float"),
            ("max_tokens", "最大输出 Token", "int"),
            ("stream", "流式输出", "switch"),
            ("proxy_enabled", "使用系统代理", "switch"),
        ]
        self.add_config_fields(global_grid, global_specs, self.global_fields)

        self.log_input_switch = Gtk.Switch(halign=Gtk.Align.START)
        self.log_input_switch.set_active(bool(self.effective_config.get("debug", {}).get("log_user_input", False)))
        row = len(global_specs)
        global_grid.attach(Gtk.Label(label="记录请求正文", xalign=0), 0, row, 1, 1)
        global_grid.attach(self.log_input_switch, 1, row, 1, 1)
        self.load_global_fields()
        return page

    def add_config_fields(self, grid, specs, target):
        for row, (key, label, kind) in enumerate(specs):
            text = Gtk.Label(label=label, xalign=0)
            grid.attach(text, 0, row, 1, 1)
            if kind == "switch":
                widget = Gtk.Switch(halign=Gtk.Align.START)
            elif kind == "int":
                widget = Gtk.SpinButton.new_with_range(0, 1000000, 1)
            elif kind == "float":
                widget = Gtk.SpinButton.new_with_range(0, 2, 0.05)
                widget.set_digits(2)
            else:
                widget = Gtk.Entry()
                if kind == "password":
                    widget.set_visibility(False)
                    widget.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1, 1)
            target[key] = widget

    def build_prompt_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.append(Gtk.Label(label="System 提示词（必须保留 {history}）", xalign=0))
        self.system_prompt = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        scroll1 = Gtk.ScrolledWindow(vexpand=True)
        scroll1.set_child(self.system_prompt)
        page.append(scroll1)
        page.append(Gtk.Label(label="User 模板（必须保留 {pinyin}）", xalign=0))
        self.user_template = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        scroll2 = Gtk.ScrolledWindow(vexpand=True)
        scroll2.set_child(self.user_template)
        page.append(scroll2)
        reset = Gtk.Button(label="恢复默认提示词", halign=Gtk.Align.START)
        reset.connect("clicked", self.on_reset_prompt)
        page.append(reset)
        return page

    def build_log_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        refresh = Gtk.Button(label="刷新")
        refresh.connect("clicked", lambda *_: self.refresh_logs())
        toolbar.append(refresh)
        open_file = Gtk.Button(label="用默认程序打开日志")
        open_file.connect("clicked", lambda *_: subprocess.Popen(["xdg-open", LOG_PATH]))
        toolbar.append(open_file)
        self.log_status = Gtk.Label(label="", xalign=1)
        self.log_status.set_hexpand(True)
        toolbar.append(self.log_status)
        page.append(toolbar)
        self.log_view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.NONE)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroll.set_child(self.log_view)
        page.append(scroll)
        return page

    def build_stats_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        refresh = Gtk.Button(label="刷新统计")
        refresh.connect("clicked", lambda *_: self.refresh_stats())
        toolbar.append(refresh)
        page.append(toolbar)
        self.stats_summary = Gtk.Label(label="", xalign=0)
        self.stats_summary.set_selectable(True)
        page.append(self.stats_summary)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.stats_table = Gtk.Grid(column_spacing=1, row_spacing=1)
        self.stats_table.set_column_homogeneous(False)
        scroll.set_child(self.stats_table)
        page.append(scroll)
        return page

    def current_profile_index(self):
        selected = self.profile_dropdown.get_selected()
        return selected if 0 <= selected < len(self.profiles) else 0

    def save_current_profile_fields(self):
        if not self.profiles:
            return
        self.save_profile_fields(self.current_profile_index())

    def save_profile_fields(self, index):
        profile = self.profiles[index]
        profile.pop("name", None)
        api = profile.setdefault("api", {})
        for key in ("base_url", "model", "api_key"):
            api[key] = self.profile_fields[key].get_text().strip()
        for key in list(api):
            if key not in PROFILE_API_KEYS:
                del api[key]

    def load_profile_fields(self, index):
        profile = self.profiles[index]
        api = profile.get("api", {})
        for key in ("base_url", "model", "api_key"):
            self.profile_fields[key].set_text(str(api.get(key, "")))
        self.loaded_profile_index = index

    def load_global_fields(self):
        for key in ("timeout_ms", "stream_timeout_ms", "max_tokens"):
            self.global_fields[key].set_value(float(self.global_api.get(key, 0)))
        for key in ("temperature", "top_p"):
            self.global_fields[key].set_value(float(self.global_api.get(key, 0)))
        for key in ("stream", "proxy_enabled"):
            self.global_fields[key].set_active(bool(self.global_api.get(key, False)))

    def save_global_fields(self):
        for key in ("timeout_ms", "stream_timeout_ms", "max_tokens"):
            self.global_api[key] = int(self.global_fields[key].get_value())
        for key in ("temperature", "top_p"):
            self.global_api[key] = float(self.global_fields[key].get_value())
        for key in ("stream", "proxy_enabled"):
            self.global_api[key] = bool(self.global_fields[key].get_active())

    def reload_profile_list(self, selected_id=None):
        self.reloading_profiles = True
        while self.profile_model.get_n_items():
            self.profile_model.remove(0)
        for profile in self.profiles:
            self.profile_model.append(profile.get("api", {}).get("model") or "未命名模型")
        selected_id = selected_id or self.active_profile_id
        index = next((i for i, p in enumerate(self.profiles) if p.get("id") == selected_id), 0)
        self.profile_dropdown.set_selected(index)
        self.load_profile_fields(index)
        self.reloading_profiles = False

    def on_profile_selected(self, *_args):
        if self.reloading_profiles:
            return
        index = self.current_profile_index()
        if self.profiles:
            if self.loaded_profile_index is not None and self.loaded_profile_index != index:
                self.save_profile_fields(self.loaded_profile_index)
            self.active_profile_id = self.profiles[index]["id"]
            self.load_profile_fields(index)

    def on_add_profile(self, *_args):
        self.save_current_profile_fields()
        profile = {
            "id": uuid.uuid4().hex[:12],
            "api": copy.deepcopy(self.profiles[self.current_profile_index()].get("api", {})),
        }
        profile["api"]["api_key"] = ""
        self.profiles.append(profile)
        self.active_profile_id = profile["id"]
        self.reload_profile_list(profile["id"])

    def on_delete_profile(self, *_args):
        if len(self.profiles) <= 1:
            self.status_label.set_text("至少保留一个 LLM 配置")
            return
        del self.profiles[self.current_profile_index()]
        self.active_profile_id = self.profiles[0]["id"]
        self.reload_profile_list(self.active_profile_id)

    def load_prompt(self):
        prompt = deep_merge(DEFAULT_CONFIG["prompt"], self.raw_config.get("prompt", {}))
        self.system_prompt.get_buffer().set_text(prompt["system"])
        self.user_template.get_buffer().set_text(prompt["user_template"])

    def textview_text(self, view):
        buf = view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)

    def on_reset_prompt(self, *_args):
        self.system_prompt.get_buffer().set_text(DEFAULT_CONFIG["prompt"]["system"])
        self.user_template.get_buffer().set_text(DEFAULT_CONFIG["prompt"]["user_template"])

    def on_save(self, _button, restart):
        self.save_current_profile_fields()
        self.save_global_fields()
        template = self.textview_text(self.user_template).strip()
        if "{pinyin}" not in template:
            self.status_label.set_text("User 模板必须包含 {pinyin}")
            return
        system_template = self.textview_text(self.system_prompt).strip()
        if "{history}" not in system_template:
            self.status_label.set_text("System 提示词必须包含 {history}")
            return
        config = copy.deepcopy(self.raw_config)
        config["llm_profiles"] = self.profiles
        config["active_llm_profile"] = self.active_profile_id
        active = next(item for item in self.profiles if item["id"] == self.active_profile_id)
        self.global_api["endpoint"] = DEFAULT_CONFIG["api"]["endpoint"]
        config["api"] = deep_merge(self.global_api, active["api"])
        config["prompt"] = {
            "system": system_template,
            "user_template": template,
        }
        config.setdefault("debug", {})["log_user_input"] = self.log_input_switch.get_active()
        save_user_config(config)
        self.raw_config = config
        self.status_label.set_text("配置已保存" + ("，正在重启 IBus" if restart else ""))
        if restart:
            subprocess.Popen(["ibus", "restart"])

    def refresh_logs(self):
        try:
            with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-500:]
            text = "".join(lines)
            self.log_status.set_text(f"最近 {len(lines)} 行 · {LOG_PATH}")
        except FileNotFoundError:
            text = "日志文件尚未生成。"
            self.log_status.set_text(LOG_PATH)
        buf = self.log_view.get_buffer()
        buf.set_text(text)
        return True

    def refresh_stats(self):
        path = self.effective_config.get("stats", {}).get(
            "path", self.effective_config.get("cache", {}).get("path")
        )
        store = LLMStatsStore(path)
        summary = store.summary(30)
        calls = summary["calls"] or 0
        successes = summary["successes"] or 0
        rate = (successes / calls * 100) if calls else 0
        self.stats_summary.set_text(
            "最近 30 天\n"
            f"调用 {calls} 次    成功率 {rate:.1f}%    平均响应 {summary['avg_latency_ms']:.0f} ms\n"
            f"输入 Token {summary['prompt_tokens']}    输出 Token {summary['completion_tokens']}    "
            f"总 Token {summary['total_tokens']}\n"
            f"最快 {summary['min_latency_ms']} ms    最慢 {summary['max_latency_ms']} ms"
        )
        child = self.stats_table.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.stats_table.remove(child)
            child = next_child

        headers = [
            "模型", "调用次数", "成功率", "输入 Token", "输出 Token", "总 Token",
            "平均响应", "最快", "最慢", "平均候选",
        ]
        for column, title in enumerate(headers):
            label = self.table_label(title, header=True, numeric=column > 0)
            self.stats_table.attach(label, column, 0, 1, 1)

        for row_index, row in enumerate(store.grouped_by_model(30), start=1):
            calls = row["calls"] or 0
            successes = row["successes"] or 0
            values = [
                row["model"] or "未命名模型",
                f"{calls}",
                f"{(successes / calls * 100) if calls else 0:.1f}%",
                f"{row['prompt_tokens']}",
                f"{row['completion_tokens']}",
                f"{row['total_tokens']}",
                f"{row['avg_latency_ms']:.0f} ms",
                f"{row['min_latency_ms']} ms",
                f"{row['max_latency_ms']} ms",
                f"{row['avg_candidate_count']:.1f}",
            ]
            for column, value in enumerate(values):
                self.stats_table.attach(
                    self.table_label(value, numeric=column > 0), column, row_index, 1, 1
                )
        self.stats_table.set_visible(True)
        return True

    def table_label(self, text, header=False, numeric=False):
        label = Gtk.Label(label=str(text), xalign=1 if numeric else 0)
        label.set_margin_top(8)
        label.set_margin_bottom(8)
        label.set_margin_start(10)
        label.set_margin_end(10)
        label.set_hexpand(not numeric)
        label.set_selectable(not header)
        if header:
            label.add_css_class("heading")
        else:
            label.add_css_class("card")
        return label


class SettingsApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.freedesktop.IBus.AIPinyin.Settings")

    def do_activate(self):
        window = self.props.active_window or SettingsWindow(self)
        window.present()


def main():
    if "--check" in sys.argv:
        config = load_config()
        store = LLMStatsStore(config.get("stats", {}).get("path", config["cache"]["path"]))
        print(json.dumps({
            "gtk": Gtk.get_major_version(),
            "profiles": len(config.get("llm_profiles", [])),
            "active": config.get("active_llm_profile"),
            "stats": store.summary(30),
        }, ensure_ascii=False))
        return 0
    return SettingsApplication().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
