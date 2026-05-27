import copy
import json
import os


CONFIG_DIR = "~/.config/ibus-ai-pinyin"
CONFIG_PATH = "~/.config/ibus-ai-pinyin/config.json"


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
        "thinking": {
            "enabled": None,
            "type": "disabled",
        },
        "extra_body": {},
    },
    "input": {
        "max_buffer_length": 120,
        "candidate_page_size": 5,
        "default_mode": "zh",
        "toggle_key": {
            "enabled": True,
            "key": "space",
            "modifiers": ["Control"],
        },
        "shuangpin": {
            "enabled": False,
            "scheme": "xiaohe",
        },
        "auto_request": {
            "enabled": False,
            "delay_ms": 1500,
        },
    },
    "candidate": {
        "max_candidates": 5,
        "fallback_to_raw_pinyin": True,
    },
    "cache": {
        "enabled": True,
        "path": "~/.config/ibus-ai-pinyin/cache.sqlite3",
    },
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
    "debug": {
        "log_user_input": False,
        "log_model_output": False,
    },
    "prompt": {
        "system": (
            "你是一个中文拼音输入法转换器。你的任务是把用户输入的拼音转换成最可能的中文候选。"
            "只输出 JSON 字符串数组，不要解释，不要 Markdown，不要代码块。最多输出 5 个候选。"
        ),
        "user_template": "拼音：{pinyin}\n请输出中文候选 JSON 数组。",
    },
}


def deep_merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path=CONFIG_PATH):
    expanded_path = os.path.expanduser(path)
    if not os.path.exists(expanded_path):
        os.makedirs(os.path.dirname(expanded_path), exist_ok=True)
        with open(expanded_path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        return copy.deepcopy(DEFAULT_CONFIG)

    with open(expanded_path, "r", encoding="utf-8") as f:
        user_config = json.load(f)
    return deep_merge(DEFAULT_CONFIG, user_config)


def save_config(config_dict, path=CONFIG_PATH):
    """Save a complete config dict to disk, creating directories as needed."""
    expanded_path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(expanded_path), exist_ok=True)
    with open(expanded_path, "w", encoding="utf-8") as f:
        json.dump(config_dict, f, ensure_ascii=False, indent=2)
