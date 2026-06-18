import json
import logging
import os
import re
import time

import requests


SYSTEM_PROMPT = (
    "你是一个中文拼音输入法转换器。你的任务是把用户输入的拼音转换成最可能的中文候选。"
    "输出要求：必须调用 submit_pinyin_candidates 工具函数提交 5 个中文候选词。"
    "不要在普通文本里输出候选，不要解释，不要 Markdown，不要代码块。"
)
USER_TEMPLATE = "当前拼音：{pinyin}\n请调用 submit_pinyin_candidates 工具函数，提交 5 个最可能的中文候选词。"
SUBMIT_CANDIDATES_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_pinyin_candidates",
        "description": "提交中文拼音输入法候选词列表。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "candidates": {
                    "type": "array",
                    "description": "按优先级排序的中文候选词，第一项是最可能的输入结果。",
                    "items": {"type": "string"},
                    "minItems": 5,
                    "maxItems": 5,
                }
            },
            "required": ["candidates"],
        },
    },
}
SUBMIT_CANDIDATES_TOOL_CHOICE = {
    "type": "function",
    "function": {"name": "submit_pinyin_candidates"},
}


class LLMClient:
    def __init__(self, config):
        api = config.get("api", {})
        self.base_url = api.get("base_url", "http://127.0.0.1:8080/v1").rstrip("/")
        self.endpoint = api.get("endpoint", "/chat/completions")
        self.model = api.get("model", "qwen3-0.6b")
        self.timeout = api.get("timeout_ms", 800) / 1000
        self.temperature = api.get("temperature", 0.1)
        self.top_p = api.get("top_p", 0.8)
        self.max_tokens = api.get("max_tokens", 64)
        self.stream = api.get("stream", False)
        self.proxy_enabled = api.get("proxy_enabled", False)
        self.thinking = api.get("thinking", {})
        self.extra_body = api.get("extra_body", {})
        self.cache_optimization = api.get("cache_optimization", {})

        api_key = api.get("api_key", "")
        api_key_env = api.get("api_key_env", "OPENAI_API_KEY")
        self.api_key = api_key or os.environ.get(api_key_env, "sk-local")

        self.system_prompt = SYSTEM_PROMPT
        self.user_template = USER_TEMPLATE

    def build_request_body(
        self,
        pinyin,
        dictionary_context=None,
        recent_committed_text=None,
        recent_committed_turns=None,
        surrounding_context=None,
        stream=None,
    ):
        messages = self.build_messages(
            pinyin,
            dictionary_context=dictionary_context or [],
            recent_committed_text=recent_committed_text or "",
            recent_committed_turns=recent_committed_turns or [],
            surrounding_context=surrounding_context or {},
        )
        return self.build_chat_body(messages, stream=stream)

    def build_messages(
        self,
        pinyin,
        dictionary_context=None,
        recent_committed_text=None,
        recent_committed_turns=None,
        surrounding_context=None,
    ):
        system_content = self.build_system_content(
            recent_committed_text=recent_committed_text or "",
            recent_committed_turns=recent_committed_turns or [],
            surrounding_context=surrounding_context or {},
        )
        messages = [{"role": "system", "content": system_content}]

        user_content = self.build_user_content(
            pinyin,
            dictionary_context=dictionary_context or [],
        )
        messages.append({"role": "user", "content": user_content})
        return messages

    def build_user_content(
        self,
        pinyin,
        dictionary_context=None,
        recent_committed_text=None,
        surrounding_context=None,
    ):
        if self.should_use_deepseek_cache_layout():
            return self.build_deepseek_cache_user_content(
                pinyin,
                dictionary_context=dictionary_context or [],
            )

        user_content = self.user_template.format(pinyin=pinyin)
        context_text = self.format_dictionary_context(dictionary_context or [])
        if context_text:
            user_content = f"{user_content}\n\n{context_text}"
        return user_content

    def build_deepseek_cache_user_content(
        self,
        pinyin,
        dictionary_context=None,
        recent_committed_text=None,
        surrounding_context=None,
    ):
        sections = [
            # "任务：将当前拼音转换为最可能的中文候选。",
            # "输出要求：必须调用 submit_pinyin_candidates 工具函数提交 5 个中文候选词。",
        ]
        context_text = self.format_dictionary_context(dictionary_context or [])
        if context_text:
            sections.append(context_text)
        sections.append(f"{pinyin}")
        return "\n\n".join(sections)

    def build_chat_body(self, user_content, stream=None, system_content=None):
        if isinstance(user_content, list):
            messages = user_content
        else:
            messages = [
                {"role": "system", "content": system_content or self.system_prompt},
                {"role": "user", "content": user_content},
            ]
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if isinstance(self.extra_body, dict):
            body.update(self.extra_body)
        body["tools"] = [SUBMIT_CANDIDATES_TOOL]
        body["tool_choice"] = SUBMIT_CANDIDATES_TOOL_CHOICE
        if isinstance(self.thinking, dict) and self.thinking.get("enabled") is False:
            body["thinking"] = {"type": self.thinking.get("type", "disabled")}
        elif isinstance(self.thinking, dict) and self.thinking.get("enabled") is True:
            body["thinking"] = {"type": self.thinking.get("type", "enabled")}
        if body.get("stream") and self.should_log_deepseek_cache_usage():
            stream_options = body.get("stream_options")
            if not isinstance(stream_options, dict):
                stream_options = {}
            stream_options["include_usage"] = True
            body["stream_options"] = stream_options
        return body

    def log_request_body(self, label, body):
        try:
            payload = json.dumps(body, ensure_ascii=False, sort_keys=True)
        except TypeError:
            payload = repr(body)
        logging.info("%s request body=%s", label, payload)

    def build_system_content(
        self,
        recent_committed_text=None,
        recent_committed_turns=None,
        surrounding_context=None,
    ):
        sections = [self.system_prompt]
        history_text = self.format_history_context(
            recent_committed_text=recent_committed_text or "",
            recent_committed_turns=recent_committed_turns or [],
        )
        if history_text:
            sections.append(history_text)
        surrounding_text = self.format_surrounding_context(surrounding_context or {})
        if surrounding_text:
            sections.append(surrounding_text)
        return "\n\n".join(sections)

    def format_history_context(self, recent_committed_text=None, recent_committed_turns=None):
        sections = []
        recent_text = self.format_recent_committed_text(recent_committed_text or "")
        if recent_text:
            sections.append(recent_text)

        turns = self.format_recent_committed_turns(recent_committed_turns or [])
        if turns:
            text = "".join(turn["text"] for turn in turns)
            sections.append(
                f"历史输入：\"{text}\"\n"
                "这些内容是用户已经选择并上屏的历史输入，不是当前拼音；请只把它作为语境参考。"
            )
        return "\n\n".join(sections)

    def format_surrounding_context(self, surrounding_context):
        if not isinstance(surrounding_context, dict):
            return ""
        before = self.normalize_context_text(surrounding_context.get("before", ""))
        after = self.normalize_context_text(surrounding_context.get("after", ""))
        if not before and not after:
            return ""
        lines = ["当前输入框上下文："]
        if before:
            lines.append(f"光标前文本：{before}")
        if after:
            lines.append(f"光标后文本：{after}")
        lines.append("请结合这些上下文判断当前拼音最可能对应的中文，但不要输出上下文本身。")
        return "\n".join(lines)

    def normalize_context_text(self, text):
        return " ".join(str(text or "").split())

    def should_use_deepseek_cache_layout(self):
        if not self.is_deepseek_request():
            return False
        setting = self.cache_optimization
        if isinstance(setting, bool):
            return setting
        if not isinstance(setting, dict):
            return True
        enabled = setting.get("enabled", "auto")
        return enabled in (True, "auto")

    def should_log_deepseek_cache_usage(self):
        if not self.is_deepseek_request():
            return False
        setting = self.cache_optimization
        if isinstance(setting, dict):
            return setting.get("log_usage", True)
        return True

    def is_deepseek_request(self):
        provider = ""
        if isinstance(self.cache_optimization, dict):
            provider = str(self.cache_optimization.get("provider", "")).lower()
        if provider:
            return provider == "deepseek"
        return "deepseek" in self.base_url.lower() or "deepseek" in self.model.lower()

    def log_usage(self, usage, label="LLM"):
        if not isinstance(usage, dict):
            return
        hit = usage.get("prompt_cache_hit_tokens")
        miss = usage.get("prompt_cache_miss_tokens")
        if hit is None and miss is None:
            return
        prompt_tokens = usage.get("prompt_tokens")
        total_tokens = usage.get("total_tokens")
        logging.info(
            "%s DeepSeek cache usage prompt_tokens=%s cache_hit=%s cache_miss=%s total_tokens=%s",
            label,
            prompt_tokens,
            hit,
            miss,
            total_tokens,
        )

    def get_candidates(
        self,
        pinyin,
        max_candidates=5,
        dictionary_context=None,
        recent_committed_text=None,
        recent_committed_turns=None,
        surrounding_context=None,
    ):
        body = self.build_request_body(
            pinyin,
            dictionary_context=dictionary_context or [],
            recent_committed_text=recent_committed_text or "",
            recent_committed_turns=recent_committed_turns or [],
            surrounding_context=surrounding_context or {},
        )
        self.log_request_body("LLM", body)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Connection": "close",
        }

        session = requests.Session()
        session.trust_env = bool(self.proxy_enabled)
        url = self.base_url + self.endpoint
        start = time.monotonic()
        try:
            resp = session.post(
                url,
                headers=headers,
                json=body,
                timeout=self.timeout,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logging.info(
                "LLM response received model=%s status=%s elapsed_ms=%s",
                self.model,
                resp.status_code,
                elapsed_ms,
            )
            try:
                resp.raise_for_status()
            except requests.HTTPError:
                logging.error(
                    "LLM HTTP error model=%s status=%s response=%r",
                    self.model,
                    resp.status_code,
                    resp.text[:1000],
                )
                raise
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logging.exception(
                "LLM request failed model=%s elapsed_ms=%s url=%s",
                self.model,
                elapsed_ms,
                url,
            )
            raise
        finally:
            session.close()
        data = resp.json()
        self.log_usage(data.get("usage"), label="LLM")
        message = data["choices"][0]["message"]
        content = message.get("content") or ""
        if not content:
            content = message.get("reasoning_content") or ""
            logging.info("LLM content empty, using reasoning_content fallback")
        logging.info("LLM raw output elapsed_ms=%s content=%r", elapsed_ms, content)
        candidates = self.extract_message_candidates(message, max_candidates=max_candidates)
        if not candidates:
            candidates = self.parse_candidates(content, max_candidates=max_candidates)
        candidates = self.rank_candidates_by_context(
            candidates, dictionary_context or []
        )
        logging.info(
            "LLM parsed candidates elapsed_ms=%s candidates=%r", elapsed_ms, candidates
        )
        return candidates

    def refine_candidates(
        self, pinyin, current_candidates, instruction, max_candidates=5, surrounding_context=None
    ):
        current_text = "\n".join(
            f"{index + 1}. {candidate}"
            for index, candidate in enumerate(current_candidates or [])
        )
        user_content = (
            f"拼音：{pinyin}\n\n"
            # f"错误候选参考：\n{current_text}\n\n"
            f"用户补充拼音提示：{instruction}\n\n"
            "用户补充的是对输入拼音的额外描述,来给你更好了解用户想输入的每个拼音对应的文字,按照用户补充的说明输出候选词。"
            # "用户继续输入补充拼音提示，是因为这些候选不符合预期。"
            # "请不要简单复述或优先保留错误候选，除非它能被补充拼音提示明确支持。\n\n"
            # "规则："
            # "用户补充提示是连续拼音，不一定有空格，也不一定有固定格式；"
            # "请自行识别其中的拼音片段、同音字定位、词语提示或逐字提示；"
            # "补充提示用于说明原始拼音应该对应哪些汉字，不是要直接输出补充提示对应的完整词语；"
            # "如果补充提示包含用于定位同音字的词语，请只提取被定位出来的目标汉字；"
            # "请按原始拼音的音节顺序组合被定位出来的汉字，不要按补充提示自身的词序或完整词义输出；"
            # "输出必须仍然符合原始拼音；"
            # "最符合补充提示的候选必须放在第一位；"
            # "必须调用 submit_pinyin_candidates 工具函数提交 5 个候选词，不要解释，不要 Markdown。"
        )
        body = self.build_chat_body(
            user_content,
            stream=False,
            system_content=self.build_system_content(surrounding_context=surrounding_context or {}),
        )
        logging.info("LLM refinement user content=%r", user_content)
        self.log_request_body("LLM refinement", body)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Connection": "close",
        }
        session = requests.Session()
        session.trust_env = bool(self.proxy_enabled)
        url = self.base_url + self.endpoint
        start = time.monotonic()
        try:
            resp = session.post(url, headers=headers, json=body, timeout=self.timeout)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logging.info(
                "LLM refinement response received model=%s status=%s elapsed_ms=%s",
                self.model,
                resp.status_code,
                elapsed_ms,
            )
            try:
                resp.raise_for_status()
            except requests.HTTPError:
                logging.error(
                    "LLM refinement HTTP error model=%s status=%s response=%r",
                    self.model,
                    resp.status_code,
                    resp.text[:1000],
                )
                raise
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logging.exception(
                "LLM refinement request failed model=%s elapsed_ms=%s url=%s",
                self.model,
                elapsed_ms,
                url,
            )
            raise
        finally:
            session.close()

        data = resp.json()
        self.log_usage(data.get("usage"), label="LLM refinement")
        message = data["choices"][0]["message"]
        content = message.get("content") or message.get("reasoning_content") or ""
        logging.info(
            "LLM refinement raw output elapsed_ms=%s content=%r", elapsed_ms, content
        )
        candidates = self.extract_message_candidates(message, max_candidates=max_candidates)
        if not candidates:
            candidates = self.parse_candidates(content, max_candidates=max_candidates)
        logging.info(
            "LLM refinement parsed candidates elapsed_ms=%s candidates=%r",
            elapsed_ms,
            candidates,
        )
        return candidates

    def get_more_candidates(self, pinyin, excluded_candidates, max_candidates=5, surrounding_context=None):
        excluded_text = "\n".join(
            f"{index + 1}. {candidate}"
            for index, candidate in enumerate(excluded_candidates or [])
        )
        user_content = (
            f"拼音：{pinyin}\n"
            f"排除这些候选：\n{excluded_text}\n"
        )
        body = self.build_chat_body(
            user_content,
            stream=False,
            system_content=self.build_system_content(surrounding_context=surrounding_context or {}),
        )
        logging.info("LLM more candidates user content=%r", user_content)
        self.log_request_body("LLM more candidates", body)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Connection": "close",
        }
        session = requests.Session()
        session.trust_env = bool(self.proxy_enabled)
        url = self.base_url + self.endpoint
        start = time.monotonic()
        try:
            resp = session.post(url, headers=headers, json=body, timeout=self.timeout)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logging.info(
                "LLM more candidates response received model=%s status=%s elapsed_ms=%s",
                self.model,
                resp.status_code,
                elapsed_ms,
            )
            try:
                resp.raise_for_status()
            except requests.HTTPError:
                logging.error(
                    "LLM more candidates HTTP error model=%s status=%s response=%r",
                    self.model,
                    resp.status_code,
                    resp.text[:1000],
                )
                raise
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logging.exception(
                "LLM more candidates request failed model=%s elapsed_ms=%s url=%s",
                self.model,
                elapsed_ms,
                url,
            )
            raise
        finally:
            session.close()

        data = resp.json()
        self.log_usage(data.get("usage"), label="LLM more candidates")
        message = data["choices"][0]["message"]
        content = message.get("content") or message.get("reasoning_content") or ""
        logging.info(
            "LLM more candidates raw output elapsed_ms=%s content=%r", elapsed_ms, content
        )
        candidates = self.extract_message_candidates(message, max_candidates=max_candidates)
        if not candidates:
            candidates = self.parse_candidates(content, max_candidates=max_candidates)
        excluded = set(excluded_candidates or [])
        candidates = [candidate for candidate in candidates if candidate not in excluded]
        logging.info(
            "LLM more candidates parsed elapsed_ms=%s candidates=%r",
            elapsed_ms,
            candidates,
        )
        return candidates

    def stream_candidates(
        self,
        pinyin,
        max_candidates=5,
        dictionary_context=None,
        recent_committed_text=None,
        recent_committed_turns=None,
        surrounding_context=None,
    ):
        for candidate in self.get_candidates(
            pinyin,
            max_candidates=max_candidates,
            dictionary_context=dictionary_context or [],
            recent_committed_text=recent_committed_text or "",
            recent_committed_turns=recent_committed_turns or [],
            surrounding_context=surrounding_context or {},
        ):
            yield candidate

    def extract_complete_candidates(self, content):
        text = content.strip()
        if "[" in text:
            text = text[text.find("[") :]

        candidates = []
        in_string = False
        escaped = False
        current = []
        for ch in text:
            if not in_string:
                if ch == '"':
                    in_string = True
                    escaped = False
                    current = []
                continue

            if escaped:
                current.append(ch)
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                candidates.append("".join(current).strip())
                in_string = False
                current = []
                continue
            current.append(ch)
        return [candidate for candidate in candidates if candidate]

    def extract_message_candidates(self, message, max_candidates=5):
        if not isinstance(message, dict):
            return []
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") if isinstance(tool_call, dict) else None
            if not isinstance(function, dict):
                continue
            if function.get("name") != "submit_pinyin_candidates":
                continue
            arguments = function.get("arguments") or "{}"
            if isinstance(arguments, str):
                try:
                    payload = json.loads(arguments)
                except json.JSONDecodeError:
                    logging.warning("LLM tool call arguments invalid JSON arguments=%r", arguments)
                    continue
            elif isinstance(arguments, dict):
                payload = arguments
            else:
                continue
            return self.normalize_candidates(payload.get("candidates", []), max_candidates=max_candidates)
        return []

    def rank_candidates_by_context(self, candidates, items):
        context_terms = []
        seen = set()
        for item in items:
            text = item.get("text") if isinstance(item, dict) else str(item)
            if text and text not in seen:
                seen.add(text)
                weight = item.get("weight", 50) if isinstance(item, dict) else 50
                context_terms.append((text, weight))
        if not context_terms:
            return candidates

        indexed = []
        for index, candidate in enumerate(candidates):
            score = 0
            for term, weight in context_terms:
                if term in candidate:
                    score += 1000 + int(weight)
            indexed.append((score, -index, candidate))
        indexed.sort(reverse=True)
        return [candidate for _score, _index, candidate in indexed]

    def format_dictionary_context(self, items):
        lines = []
        seen = set()
        for item in items:
            text = item.get("text") if isinstance(item, dict) else str(item)
            if not text or text in seen:
                continue
            seen.add(text)
            pinyin = item.get("pinyin", "") if isinstance(item, dict) else ""
            item_type = item.get("type", "") if isinstance(item, dict) else ""
            detail = f"{text}"
            if pinyin:
                detail += f" ({pinyin})"
            if item_type and item_type != "other":
                detail += f" [{item_type}]"
            lines.append(f"- {detail}")
        if not lines:
            return ""
        return (
            "领域词库命中：\n"
            + "\n".join(lines)
            + "\n请优先使用这些领域词转换拼音；如果用户输入的是长拼音短语，请把这些词自然组合进完整中文候选。"
            + "\n命中项的拼音对应输入片段时，候选中必须使用命中词文本，不要替换成同音词、近义词或常见词。"
        )

    def format_recent_committed_text(self, text):
        text = " ".join(str(text or "").split())
        if not text:
            return ""
        return (
            "最近已输入中文："
            + text
            + "\n这些内容是用户已经选择并上屏的候选词，不是当前拼音；请只把它作为语境参考，保持当前拼音仍按用户输入转换。"
        )

    def format_recent_committed_turns(self, turns):
        result = []
        for turn in turns or []:
            if not isinstance(turn, dict):
                continue
            pinyin = " ".join(str(turn.get("pinyin") or "").split())
            text = " ".join(str(turn.get("text") or "").split())
            if not pinyin or not text:
                continue
            result.append({"pinyin": pinyin, "text": text})
        return result

    def parse_candidates(self, content, max_candidates=5):
        text = content.strip()
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"^```\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            text = text[start : end + 1]

        payload = json.loads(text)
        if isinstance(payload, dict):
            arr = payload.get("candidates", [])
        else:
            arr = payload
        return self.normalize_candidates(arr, max_candidates=max_candidates)

    def normalize_candidates(self, arr, max_candidates=5):
        if not isinstance(arr, list):
            return []
        result = []
        seen = set()
        for item in arr:
            if not isinstance(item, str):
                continue
            item = item.strip()
            if not item or item in seen or not self.is_valid_candidate(item):
                continue
            seen.add(item)
            result.append(item)
            if len(result) >= max_candidates:
                break
        return result

    def is_valid_candidate(self, candidate):
        if re.search(r"[\u3400-\u9fff]", candidate):
            return True
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+#._-]{0,31}", candidate):
            return True
        return False
