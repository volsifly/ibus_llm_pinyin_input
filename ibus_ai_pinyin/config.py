import copy
import json
import os


CONFIG_DIR = "~/.config/ibus-ai-pinyin"
CONFIG_PATH = "~/.config/ibus-ai-pinyin/config.json"

DEFAULT_SYSTEM_PROMPT = "拼音转中文。输出18个按概率排序的候选，以|分隔，无解释。可保留英文、数字和符号。\n\n{history}"
DEFAULT_USER_TEMPLATE = "现在输入:{pinyin}"
LLM_PROFILE_API_KEYS = ("base_url", "model", "api_key", "api_key_env")


DEFAULT_CONFIG = {
    "api": {
        "base_url": "http://127.0.0.1:8080/v1",
        "api_key": "",
        "api_key_env": "OPENAI_API_KEY",
        "model": "qwen3-0.6b",
        "endpoint": "/chat/completions",
        "timeout_ms": 5000,
        "temperature": 0.1,
        "top_p": 0.8,
        "max_tokens": 64,
        "output_protocol": "csv",
        "max_history_chars": 48,
        "stream": True,
        "stream_timeout_ms": 5000,
        "proxy_enabled": False,
        "thinking": {
            "enabled": None,
            "type": "disabled",
        },
        "cache_optimization": {
            "enabled": "auto",
            "provider": "",
            "log_usage": True,
        },
        "extra_body": {},
    },
    "active_llm_profile": "default",
    "llm_profiles": [],
    "prompt": {
        "system": DEFAULT_SYSTEM_PROMPT,
        "user_template": DEFAULT_USER_TEMPLATE,
    },
    "input": {
        "max_buffer_length": 120,
        "candidate_page_size": 9,
        "recent_context_items": 6,
        "recent_context_chars": 80,
        "recent_context_idle_timeout_seconds": 1800,
        "surrounding_context_enabled": True,
        "surrounding_context_before_chars": 80,
        "surrounding_context_after_chars": 40,
        "default_mode": "zh",
        "toggle_key": {
            "enabled": True,
            "key": "space",
            "modifiers": ["Control"],
        },
    },
    "candidate": {
        "max_candidates": 18,
        "fallback_to_raw_pinyin": True,
    },
    "cache": {
        "enabled": True,
        "path": "~/.config/ibus-ai-pinyin/cache.sqlite3",
    },
    "stats": {
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
        os.chmod(expanded_path, 0o600)
        return copy.deepcopy(DEFAULT_CONFIG)

    with open(expanded_path, "r", encoding="utf-8") as f:
        user_config = json.load(f)
    config = deep_merge(DEFAULT_CONFIG, user_config)
    config["api"]["endpoint"] = DEFAULT_CONFIG["api"]["endpoint"]
    active_id = config.get("active_llm_profile", "default")
    for profile in config.get("llm_profiles", []):
        if isinstance(profile, dict) and profile.get("id") == active_id:
            profile_api = profile.get("api", {})
            if not isinstance(profile_api, dict):
                profile_api = {}
            connection_config = {
                key: profile_api[key]
                for key in LLM_PROFILE_API_KEYS
                if key in profile_api
            }
            config["api"] = deep_merge(config.get("api", {}), connection_config)
            break
    return config


def load_user_config(path=CONFIG_PATH):
    expanded_path = os.path.expanduser(path)
    if not os.path.exists(expanded_path):
        load_config(path)
    with open(expanded_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_user_config(config, path=CONFIG_PATH):
    expanded_path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(expanded_path), exist_ok=True)
    temp_path = expanded_path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temp_path, expanded_path)
    os.chmod(expanded_path, 0o600)
