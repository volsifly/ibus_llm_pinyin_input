#!/usr/bin/env python3
import argparse
import os
import sys

from ibus_ai_pinyin.cache import CandidateCache
from ibus_ai_pinyin.candidate_ranker import merge_candidates
from ibus_ai_pinyin.config import load_config
from ibus_ai_pinyin.dictionary_store import DomainDictionaryStore
from ibus_ai_pinyin.local_candidates import get_local_candidates
from ibus_ai_pinyin.llm_client import LLMClient
from ibus_ai_pinyin.user_memory import UserMemoryStore


def create_stores(config):
    cache_cfg = config.get("cache", {})
    db_path = cache_cfg.get("path", "~/.config/ibus-ai-pinyin/cache.sqlite3")
    cache = CandidateCache(db_path)
    dictionary = DomainDictionaryStore(config.get("dictionary", {}).get("path", db_path))
    memory = UserMemoryStore(config.get("memory_dictionary", {}).get("path", db_path))
    return cache, dictionary, memory


def candidates(args):
    config = load_config()
    max_candidates = args.limit or config.get("candidate", {}).get("max_candidates", 5)
    pinyin = " ".join(args.pinyin.split())
    cache, dictionary, memory = create_stores(config)

    memory_cfg = config.get("memory_dictionary", {})
    dict_cfg = config.get("dictionary", {})

    user_exact = []
    user_context = []
    if memory_cfg.get("enabled", True):
        if memory_cfg.get("exact_match_candidate", True):
            user_exact = memory.get_exact_candidates(pinyin, limit=max_candidates)
        if memory_cfg.get("send_to_llm", True):
            user_context = memory.get_context_items(
                pinyin, limit=memory_cfg.get("max_context_terms", 8)
            )

    dictionary_context = []
    if dict_cfg.get("enabled", True):
        dictionary_candidates = dictionary.get_candidates(
            pinyin, limit=max_candidates
        )
        dictionary_context = dictionary.get_context_items(
            pinyin, limit=dict_cfg.get("max_candidates", 5)
        )
    else:
        dictionary_candidates = []

    cached = cache.get(pinyin, limit=max_candidates) if config.get("cache", {}).get("enabled", True) else []
    local = get_local_candidates(pinyin, limit=max_candidates)
    immediate = merge_candidates(user_exact, dictionary_candidates, cached, local, limit=max_candidates)
    context = user_context + dictionary_context

    if immediate:
        if args.metadata:
            print("__source__:instant")
        for item in immediate:
            print(item)
        return

    llm_candidates = []
    if len(immediate) < max_candidates or context:
        try:
            llm_candidates = LLMClient(config).get_candidates(
                pinyin,
                max_candidates=max_candidates,
                dictionary_context=context,
            )
        except Exception:
            llm_candidates = []

    if args.metadata:
        print("__source__:llm")
    for item in merge_candidates(user_exact, dictionary_candidates, cached, local, llm_candidates, limit=max_candidates):
        print(item)


def commit(args):
    config = load_config()
    cache, _dictionary, memory = create_stores(config)
    pinyin = " ".join(args.pinyin.split())
    text = args.text.strip()
    if not pinyin or not text:
        return
    if config.get("cache", {}).get("enabled", True):
        cache.put_many(pinyin, [text], source="user_selected")
        cache.promote(pinyin, text)

    memory_cfg = config.get("memory_dictionary", {})
    if memory_cfg.get("enabled", True) and memory_cfg.get("auto_learn", True):
        if memory.should_auto_learn(
            text,
            min_han=memory_cfg.get("auto_learn_min_han", 2),
            max_han=memory_cfg.get("auto_learn_max_han", 12),
        ):
            memory.learn_term(
                text,
                pinyin,
                weight=memory_cfg.get("default_weight", 80),
                max_weight=memory_cfg.get("max_weight", 120),
            )


def main():
    parser = argparse.ArgumentParser(description="fcitx5 ai-pinyin backend")
    sub = parser.add_subparsers(dest="command", required=True)
    cand = sub.add_parser("candidates")
    cand.add_argument("pinyin")
    cand.add_argument("--limit", type=int, default=20)
    cand.add_argument("--metadata", action="store_true")
    cand.set_defaults(func=candidates)

    commit_parser = sub.add_parser("commit")
    commit_parser.add_argument("pinyin")
    commit_parser.add_argument("text")
    commit_parser.set_defaults(func=commit)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.getcwd())
    main()
