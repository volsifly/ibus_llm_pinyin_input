import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ibus_ai_pinyin.cache import CandidateCache
from ibus_ai_pinyin.candidate_ranker import merge_candidates
from ibus_ai_pinyin.dictionary_format import normalize_dictionary
from ibus_ai_pinyin.dictionary_store import DomainDictionaryStore
from ibus_ai_pinyin.keybindings import matches_keybinding
from ibus_ai_pinyin.local_candidates import get_local_candidates
from ibus_ai_pinyin.llm_client import LLMClient
from ibus_ai_pinyin.user_memory import UserMemoryStore, count_han, pinyin_short
from engine import AIPinyinEngine

import gi

gi.require_version("IBus", "1.0")
from gi.repository import IBus


def test_parse_candidates():
    client = LLMClient({"api": {}, "prompt": {}})
    assert client.parse_candidates('候选：```json\n["你好", "你号", "你好", 1]\n```') == [
        "你好",
        "你号",
    ]
    assert client.parse_candidates('说明文字 ["中文候选"] 其他文字') == ["中文候选"]
    assert client.parse_candidates('["bào cuò", "报错", "bug"]') == [
        "报错",
        "bug",
    ]
    assert client.rank_candidates_by_context(
        ["鸿灵知识库检索功能", "鸿灵知识库搜索功能"],
        [{"text": "鸿灵", "weight": 85}, {"text": "知识库", "weight": 90}, {"text": "搜索", "weight": 85}],
    ) == ["鸿灵知识库搜索功能", "鸿灵知识库检索功能"]


def test_extract_complete_candidates_from_partial_json():
    client = LLMClient({"api": {}, "prompt": {}})
    assert client.extract_complete_candidates('["你好", "泥') == ["你好"]
    assert client.extract_complete_candidates('说明：["你好", "泥好", "你\\"号"') == [
        "你好",
        "泥好",
        '你"号',
    ]


def test_build_request_body_includes_recent_committed_text():
    client = LLMClient({"api": {}, "prompt": {}})
    body = client.build_request_body(
        "jixu",
        recent_committed_turns=[
            {"pinyin": "hongling", "text": "鸿灵"},
            {"pinyin": "zhishiku", "text": "知识库"},
        ],
    )

    assert body["messages"][1] == {
        "role": "user",
        "content": "拼音：hongling\n请输出中文候选 JSON 数组。",
    }
    assert body["messages"][2] == {"role": "assistant", "content": "[\"鸿灵\"]"}
    assert body["messages"][3] == {
        "role": "user",
        "content": "拼音：zhishiku\n请输出中文候选 JSON 数组。",
    }
    assert body["messages"][4] == {"role": "assistant", "content": "[\"知识库\"]"}
    assert body["messages"][-1]["role"] == "user"
    assert "拼音：jixu" in body["messages"][-1]["content"]


def test_deepseek_request_body_uses_cache_friendly_layout():
    client = LLMClient(
        {
            "api": {
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
                "stream": True,
            },
            "prompt": {},
        }
    )
    body = client.build_request_body(
        "hongling",
        dictionary_context=[{"text": "鸿灵知识库", "pinyin": "hong ling zhi shi ku"}],
        recent_committed_turns=[{"pinyin": "jixu", "text": "继续"}],
        stream=True,
    )
    user_content = body["messages"][-1]["content"]

    assert body["messages"][1] == {
        "role": "user",
        "content": "拼音：jixu\n请输出中文候选 JSON 数组。",
    }
    assert body["messages"][2] == {"role": "assistant", "content": "[\"继续\"]"}
    assert user_content.index("输出要求") < user_content.index("领域词库命中")
    assert user_content.index("领域词库命中") < user_content.index("当前拼音：hongling")
    assert body["stream_options"] == {"include_usage": True}


def test_deepseek_cache_layout_can_be_disabled():
    client = LLMClient(
        {
            "api": {
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
                "cache_optimization": False,
            },
            "prompt": {},
        }
    )
    body = client.build_request_body(
        "hongling",
        recent_committed_turns=[{"pinyin": "jixu", "text": "继续"}],
    )
    user_content = body["messages"][-1]["content"]

    assert body["messages"][1] == {
        "role": "user",
        "content": "拼音：jixu\n请输出中文候选 JSON 数组。",
    }
    assert body["messages"][2] == {"role": "assistant", "content": "[\"继续\"]"}
    assert "拼音：hongling" in user_content


def test_cache_promote():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = CandidateCache(os.path.join(tmpdir, "cache.sqlite3"))
        cache.put_many("nihao", ["你好", "你号"])
        cache.promote("nihao", "你号")
        assert cache.get("nihao", limit=2) == ["你号", "你好"]
        assert cache.has("nihao", "你号")
        assert cache.delete("nihao", "你号") == 1
        assert not cache.has("nihao", "你号")
        assert cache.get("nihao", limit=2) == ["你好"]


def test_local_candidates():
    assert get_local_candidates("ni hao", limit=1) == ["你好"]
    assert get_local_candidates("meiyou", limit=5) == ["没有"]
    assert get_local_candidates("haishi meiyou", limit=5) == ["还是没有"]
    assert get_local_candidates("baocuo", limit=5) == ["报错"]
    assert get_local_candidates("xiufu", limit=5) == ["修复"]
    assert get_local_candidates("unknown", limit=5) == []


def test_merge_candidates_keeps_source_order_and_dedupes():
    assert merge_candidates(["鸿灵MCP工具", "你好"], ["你好", "世界"], limit=3) == [
        "鸿灵MCP工具",
        "你好",
        "世界",
    ]
    assert merge_candidates(
        [],
        ["鸿灵知识库搜索功能"],
        ["红领巾知识库检索功能"],
        limit=5,
    ) == ["鸿灵知识库搜索功能", "红领巾知识库检索功能"]


def test_dictionary_normalize_merges_duplicate_terms():
    dictionary, errors, warnings = normalize_dictionary(
        {
            "version": "1.0",
            "name": "测试词库",
            "entries": [
                {
                    "term": " 鸿灵MCP工具 ",
                    "pinyin": "Hong Ling MCP Gong Ju",
                    "short": "HL-MCP",
                    "weight": 95,
                },
                {
                    "term": "鸿灵MCP工具",
                    "pinyin": ["hong ling mcp"],
                    "short": ["hlmcp"],
                    "weight": 120,
                },
            ],
        }
    )
    assert errors == []
    assert any("term 重复" in warning for warning in warnings)
    assert len(dictionary["entries"]) == 1
    entry = dictionary["entries"][0]
    assert entry["term"] == "鸿灵MCP工具"
    assert entry["weight"] == 100
    assert entry["pinyin"] == ["hong ling mcp gong ju", "hong ling mcp"]
    assert entry["short"] == ["hlmcp"]


def test_dictionary_store_import_and_query():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = DomainDictionaryStore(os.path.join(tmpdir, "cache.sqlite3"))
        dictionary, errors, _warnings = normalize_dictionary(
            {
                "version": "1.0",
                "name": "测试词库",
                "entries": [
                    {
                        "term": "鸿灵MCP工具",
                        "pinyin": ["hong ling mcp gong ju"],
                        "short": ["hlmcp", "hlmcpgj"],
                        "type": "module",
                        "weight": 95,
                        "enabled": True,
                    },
                    {
                        "term": "禁用词",
                        "pinyin": ["jin yong ci"],
                        "weight": 99,
                        "enabled": False,
                    },
                ],
            }
        )
        assert errors == []
        report = store.import_dictionary(dictionary)
        assert report["inserted_terms"] == 2
        assert store.get_candidates("hong ling mcp gong ju", limit=5) == ["鸿灵MCP工具"]
        assert store.get_candidates("honglingmcpgongju", limit=5) == ["鸿灵MCP工具"]
        assert store.get_candidates("hlmcp", limit=5) == ["鸿灵MCP工具"]
        assert store.get_candidates("jin yong ci", limit=5) == []
        context = store.get_context_items("honglingmcpgongjuchajian", limit=5)
        assert [item["text"] for item in context] == ["鸿灵MCP工具"]


def test_user_memory_learns_and_queries_corrections():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = UserMemoryStore(os.path.join(tmpdir, "cache.sqlite3"))
        store.record_correction("hongling", "红灵", "鸿灵")
        assert store.should_auto_learn("鸿灵", min_han=2, max_han=12)
        assert not store.should_auto_learn("鸿", min_han=2, max_han=12)
        assert not store.should_auto_learn("今天讨论鸿灵知识库功能还需要再看一下", min_han=2, max_han=12)

        assert store.learn_term("鸿灵", "hong ling", weight=80, max_weight=120)
        assert store.get_exact_candidates("hong ling", limit=5) == ["鸿灵"]
        assert store.get_exact_candidates("hongling", limit=5) == ["鸿灵"]
        assert store.get_exact_candidates("hl", limit=5) == ["鸿灵"]
        assert [item["text"] for item in store.get_context_items("honglingzhishiku", limit=5)] == ["鸿灵"]

        for _ in range(10):
            store.learn_term("鸿灵", "hong ling", weight=80, max_weight=120)
        cur = store.conn.execute("SELECT weight, confirm_count FROM user_memory_terms WHERE term = ?", ("鸿灵",))
        row = cur.fetchone()
        assert row["weight"] == 120
        assert row["confirm_count"] == 11
        exported = store.export_dictionary()
        assert exported["name"] == "用户动态词库"
        assert exported["entries"][0]["term"] == "鸿灵"
        assert store.set_enabled("鸿灵", False) == 1
        assert store.get_exact_candidates("hongling", limit=5) == []
        assert store.delete_term("鸿灵") == 1


def test_user_memory_helpers():
    assert count_han("A鸿灵1") == 2
    assert pinyin_short("hong ling zhi shi ku") == "hlzsk"
    assert pinyin_short("hongling") == ""


def test_toggle_keybinding():
    binding = {"enabled": True, "key": "space", "modifiers": ["Control"]}
    assert matches_keybinding(IBus, IBus.KEY_space, IBus.ModifierType.CONTROL_MASK, binding)
    assert not matches_keybinding(IBus, IBus.KEY_space, 0, binding)
    assert not matches_keybinding(IBus, IBus.KEY_a, IBus.ModifierType.CONTROL_MASK, binding)


def test_inline_symbol_detection():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    assert engine.accept_inline_symbol(",")
    assert engine.accept_inline_symbol("?")
    assert engine.accept_inline_symbol("-")
    assert not engine.accept_inline_symbol("a")
    assert not engine.accept_inline_symbol("1")
    assert not engine.accept_inline_symbol(" ")


def test_input_char_detection_accepts_digits():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    assert engine.accept_char("a")
    assert engine.accept_char("1")
    assert engine.accept_char("'")
    assert not engine.accept_char(",")


def test_initial_char_passthrough_detection():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    assert engine.should_passthrough_initial_char("1")
    assert not engine.should_passthrough_initial_char("a")
    assert not engine.should_passthrough_initial_char("'")


def test_caps_lock_state_detection():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    assert engine.is_caps_lock_active(IBus.ModifierType.LOCK_MASK)
    assert not engine.is_caps_lock_active(0)


def test_caps_lock_char_detection():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    assert engine.is_caps_lock_char("A")
    assert not engine.is_caps_lock_char("a")
    assert not engine.is_caps_lock_char("1")


def test_move_selection_wraps_candidates():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    engine.candidates = ["你好", "你号", "拟好"]
    engine.selected_index = 0
    engine.show_candidates = lambda candidates: None
    engine.move_selection(1)
    assert engine.selected_index == 1
    engine.move_selection(-1)
    assert engine.selected_index == 0
    engine.move_selection(-1)
    assert engine.selected_index == 2


def test_candidate_note_appends_without_replacing_candidates():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    engine.config = {"input": {"max_buffer_length": 4}}
    engine.buffer = "nihao"
    engine.candidates = ["你好"]
    engine.candidate_note_buffer = ""
    updates = []
    engine.update_composition_ui = lambda suffix="": updates.append(suffix)

    engine.append_candidate_note("x")

    assert engine.candidates == ["你好"]
    assert engine.candidate_note_buffer == "x"
    assert updates == [""]


def test_refined_candidates_clear_note_and_keep_lookup():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    engine.buffer = "nihao"
    engine.candidate_note_buffer = "不是问候"
    engine.request_id = 3
    engine.is_requesting = True
    shown = []
    engine.show_candidates = lambda candidates: shown.append(candidates)

    assert engine.on_refined_candidates_ready(3, "nihao", "不是问候", ["拟好", "你好"]) is False

    assert engine.is_requesting is False
    assert engine.candidate_note_buffer == ""
    assert shown == [["拟好", "你好"]]


def test_more_candidates_excludes_current_page():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    engine.buffer = "nihao"
    engine.request_id = 4
    engine.is_requesting = True
    shown = []
    engine.show_candidates = lambda candidates: shown.append(candidates)

    assert engine.on_more_candidates_ready(4, "nihao", ["你好", "你号"], ["你好", "拟好"]) is False

    assert engine.is_requesting is False
    assert shown == [["拟好"]]


def test_more_candidates_keeps_current_page_when_empty():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    engine.buffer = "nihao"
    engine.request_id = 5
    engine.is_requesting = True
    shown = []
    engine.show_candidates = lambda candidates: shown.append(candidates)

    assert engine.on_more_candidates_ready(5, "nihao", ["你好", "你号"], []) is False

    assert engine.is_requesting is False
    assert shown == [["你好", "你号"]]


def test_more_candidates_appends_page_history_and_filters_all_previous():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    engine.buffer = "nihao"
    engine.request_id = 6
    engine.is_requesting = True
    engine.candidates = ["你好", "你号"]
    engine.candidate_pages_pinyin = "nihao"
    engine.candidate_pages = [["你好", "你号"], ["拟好"]]
    engine.candidate_page_index = 1
    engine.selected_index = 1
    shown = []
    engine.show_candidates = lambda candidates: shown.append(candidates)

    assert engine.get_all_candidate_page_items() == ["你好", "你号", "拟好"]
    assert engine.on_more_candidates_ready(
        6,
        "nihao",
        ["你好", "你号", "拟好"],
        ["你好", "你好啊", "拟好"],
    ) is False

    assert engine.is_requesting is False
    assert engine.candidate_pages == [["你好", "你号"], ["拟好"], ["你好啊"]]
    assert engine.candidate_page_index == 2
    assert engine.selected_index == 0
    assert shown == [["你好啊"]]


def test_previous_candidate_page_reads_history_without_request():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    engine.buffer = "nihao"
    engine.candidates = ["拟好"]
    engine.candidate_note_buffer = "x"
    engine.candidate_pages_pinyin = "nihao"
    engine.candidate_pages = [["你好", "你号"], ["拟好"]]
    engine.candidate_page_index = 1
    engine.selected_index = 1
    shown = []
    engine.show_candidates = lambda candidates: shown.append(candidates)

    engine.show_previous_candidate_page()

    assert engine.candidate_page_index == 0
    assert engine.candidate_note_buffer == ""
    assert engine.selected_index == 0
    assert shown == [["你好", "你号"]]


def test_cached_candidate_label_and_delete_selected():
    class FakeCache:
        def __init__(self):
            self.deleted = []

        def delete(self, pinyin, candidate):
            self.deleted.append((pinyin, candidate))
            return 1

    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    engine.buffer = "nihao"
    engine.candidates = ["你好", "你号", "拟好"]
    engine.selected_index = 1
    engine.cache_enabled = True
    engine.cache = FakeCache()
    engine.cached_candidate_labels = {"你好", "你号"}
    engine.candidate_pages = [["你好", "你号", "拟好"]]
    engine.candidate_page_index = 0
    shown = []
    hidden = []
    engine.show_candidates = lambda candidates: shown.append(list(candidates))
    engine.hide_lookup_table = lambda: hidden.append(True)
    engine.update_composition_ui = lambda suffix="": None

    assert engine.format_candidate_label("你好") == "你好 *"
    assert engine.format_candidate_label("拟好") == "拟好"
    assert engine.delete_selected_cached_candidate() is True

    assert engine.cache.deleted == [("nihao", "你号")]
    assert engine.candidates == ["你好", "拟好"]
    assert engine.candidate_pages == [["你好", "拟好"]]
    assert "你号" not in engine.cached_candidate_labels
    assert shown == [["你好", "拟好"]]
    assert hidden == []


def test_cache_writes_only_after_user_selection():
    class FakeCache:
        def __init__(self):
            self.puts = []
            self.promotes = []

        def put_many(self, pinyin, candidates, source="llm"):
            self.puts.append((pinyin, list(candidates), source))

        def promote(self, pinyin, candidate):
            self.promotes.append((pinyin, candidate))

    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    engine.buffer = "nihao"
    engine.request_id = 7
    engine.is_requesting = True
    engine.cache_enabled = True
    engine.cache = FakeCache()
    engine.candidate_pages_pinyin = ""
    engine.candidate_pages = []
    engine.candidate_page_index = 0
    shown = []
    committed = []

    def show_candidates(candidates):
        engine.candidates = list(candidates)
        shown.append(list(candidates))

    engine.show_candidates = show_candidates
    engine.commit_text = lambda text: committed.append(text.get_text())
    engine.record_recent_committed_candidate = lambda pinyin, text: None
    engine.clear_all = lambda: None

    assert engine.on_candidates_ready(7, "nihao", ["你好", "你号"]) is False
    assert engine.cache.puts == []
    assert engine.cache.promotes == []
    assert shown == [["你好", "你号"]]

    engine.commit_candidate(1)

    assert committed == ["你号"]
    assert engine.cache.puts == [("nihao", ["你号"], "user_selected")]
    assert engine.cache.promotes == [("nihao", "你号")]


def test_recent_committed_context_records_candidates_only():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    engine.config = {"input": {"recent_context_items": 3, "recent_context_chars": 40}}
    engine.recent_committed_turns = []

    engine.record_recent_committed_candidate("nihao", "你好")
    engine.record_recent_committed_candidate("shijie", "世界")
    engine.record_recent_committed_candidate("jixu", "继续")

    assert engine.get_recent_committed_context() == [
        {"pinyin": "nihao", "text": "你好"},
        {"pinyin": "shijie", "text": "世界"},
        {"pinyin": "jixu", "text": "继续"},
    ]


def test_recent_committed_context_respects_limits():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    engine.config = {"input": {"recent_context_items": 2, "recent_context_chars": 10}}
    engine.recent_committed_turns = []

    engine.record_recent_committed_candidate("nihao", "你好")
    engine.record_recent_committed_candidate("shijie", "世界")
    engine.record_recent_committed_candidate("jixu", "继续")

    assert engine.recent_committed_turns == [
        {"pinyin": "shijie", "text": "世界"},
        {"pinyin": "jixu", "text": "继续"},
    ]
    assert engine.get_recent_committed_context() == [{"pinyin": "jixu", "text": "继续"}]


def test_recent_committed_context_defaults_to_30_turns_without_char_limit():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    engine.config = {"input": {"recent_context_items": 30, "recent_context_chars": 0}}
    engine.recent_committed_turns = []

    for index in range(35):
        engine.record_recent_committed_candidate(
            f"pinyin{index}",
            f"很长的中文选择结果{index}",
        )

    context = engine.get_recent_committed_context()
    assert len(context) == 30
    assert context[0] == {"pinyin": "pinyin5", "text": "很长的中文选择结果5"}
    assert context[-1] == {"pinyin": "pinyin34", "text": "很长的中文选择结果34"}


def test_recent_committed_context_clears_after_idle_timeout():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    engine.config = {"input": {"recent_context_idle_timeout_seconds": 1800}}
    engine.recent_committed_turns = [{"pinyin": "nihao", "text": "你好"}]
    engine.last_input_activity_at = time.monotonic() - 1801

    engine.note_input_activity()

    assert engine.recent_committed_turns == []
    assert engine.last_input_activity_at > 0


def test_candidate_page_char_shortcut_detects_plus_minus():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    assert engine.is_candidate_page_char("=")
    assert engine.is_candidate_page_char("+")
    assert engine.is_candidate_page_char("-")
    assert not engine.is_candidate_page_char("/")


def test_candidate_page_key_accepts_shift_equal():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    assert engine.is_candidate_page_key(IBus.KEY_plus, 0)
    assert engine.is_candidate_page_key(IBus.KEY_minus, 0)
    assert engine.is_candidate_page_key(IBus.KEY_equal, 0)
    assert engine.is_candidate_page_key(IBus.KEY_KP_Add, 0)
    assert engine.is_candidate_page_key(IBus.KEY_equal, IBus.ModifierType.SHIFT_MASK)
    assert not engine.is_candidate_page_key(IBus.KEY_slash, 0)


def test_ctrl_digit_index_accepts_number_rows_and_keypad():
    engine = AIPinyinEngine.__new__(AIPinyinEngine)
    state = IBus.ModifierType.CONTROL_MASK
    assert engine.ctrl_digit_index(IBus.KEY_1, state=state) == 0
    assert engine.ctrl_digit_index(IBus.KEY_9, state=state) == 8
    assert engine.ctrl_digit_index(IBus.KEY_KP_1, state=state) == 0
    assert engine.ctrl_digit_index(0, keycode=10, state=state) == 0
    assert engine.ctrl_digit_index(0, keycode=18, state=state) == 8
    assert engine.ctrl_digit_index(0, keycode=2, state=state) == 0
    assert engine.ctrl_digit_index(0, keycode=10, state=state) == 0
    assert engine.ctrl_digit_index(IBus.KEY_1, state=0) is None


if __name__ == "__main__":
    test_parse_candidates()
    test_extract_complete_candidates_from_partial_json()
    test_build_request_body_includes_recent_committed_text()
    test_deepseek_request_body_uses_cache_friendly_layout()
    test_deepseek_cache_layout_can_be_disabled()
    test_cache_promote()
    test_local_candidates()
    test_merge_candidates_keeps_source_order_and_dedupes()
    test_dictionary_normalize_merges_duplicate_terms()
    test_dictionary_store_import_and_query()
    test_user_memory_learns_and_queries_corrections()
    test_user_memory_helpers()
    test_toggle_keybinding()
    test_inline_symbol_detection()
    test_input_char_detection_accepts_digits()
    test_initial_char_passthrough_detection()
    test_caps_lock_state_detection()
    test_caps_lock_char_detection()
    test_move_selection_wraps_candidates()
    test_candidate_note_appends_without_replacing_candidates()
    test_refined_candidates_clear_note_and_keep_lookup()
    test_more_candidates_excludes_current_page()
    test_more_candidates_keeps_current_page_when_empty()
    test_more_candidates_appends_page_history_and_filters_all_previous()
    test_previous_candidate_page_reads_history_without_request()
    test_cached_candidate_label_and_delete_selected()
    test_cache_writes_only_after_user_selection()
    test_recent_committed_context_records_candidates_only()
    test_recent_committed_context_respects_limits()
    test_recent_committed_context_defaults_to_30_turns_without_char_limit()
    test_recent_committed_context_clears_after_idle_timeout()
    test_candidate_page_char_shortcut_detects_plus_minus()
    test_candidate_page_key_accepts_shift_equal()
    test_ctrl_digit_index_accepts_number_rows_and_keypad()
    print("ok")
