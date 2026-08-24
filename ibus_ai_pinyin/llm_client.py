import json
import logging
import os
import re
import time

import requests



SYSTEM_PROMPT = "拼音转中文。输出18个按概率排序的候选，以|分隔，无解释。可保留英文、数字和符号。"
USER_TEMPLATE = "现在输入:{pinyin}"
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
        self.output_protocol = api.get("output_protocol", "csv")
        self.max_history_chars = max(0, int(api.get("max_history_chars", 48)))
        self.stream = api.get("stream", False)
        self.stream_timeout = api.get("stream_timeout_ms", 5000) / 1000
        self.proxy_enabled = api.get("proxy_enabled", False)
        self.thinking = api.get("thinking", {})
        self.extra_body = api.get("extra_body", {})
        self.cache_optimization = api.get("cache_optimization", {})
        debug = config.get("debug", {})
        self.log_user_input = bool(debug.get("log_user_input", False))
        self.log_model_output = bool(debug.get("log_model_output", False))

        api_key = api.get("api_key", "")
        api_key_env = api.get("api_key_env", "OPENAI_API_KEY")
        self.api_key = api_key or os.environ.get(api_key_env, "sk-local")

        self.system_prompt = SYSTEM_PROMPT
        self.user_template = USER_TEMPLATE
        if self.output_protocol == "tool_call":
            self.system_prompt = "拼音转中文。调用submit_pinyin_candidates提交候选，无解释。"

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
        sections.append(f"现在输入:{pinyin}")
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
            "stream": bool(self.stream if stream is None else stream),
        }
        if isinstance(self.extra_body, dict):
            body.update(self.extra_body)
        if self.output_protocol == "tool_call":
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
        if not self.log_user_input:
            return
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
            text = ",".join(turn["text"] for turn in turns)
            if self.max_history_chars:
                text = text[-self.max_history_chars :]
            sections.append(f"之前输入的内容:{text}")
        return "\n\n".join(sections)

    def format_surrounding_context(self, surrounding_context):
        if not isinstance(surrounding_context, dict):
            return ""
        before = self.normalize_context_text(surrounding_context.get("before", ""))
        after = self.normalize_context_text(surrounding_context.get("after", ""))
        if not before and not after:
            return ""
        lines = []
        if before:
            lines.append(f"B:{before}")
        if after:
            lines.append(f"A:{after}")
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
            stream=False,
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
        if self.log_model_output:
            logging.info("LLM raw output elapsed_ms=%s content=%r", elapsed_ms, content)
        candidates = self.extract_message_candidates(message, max_candidates=max_candidates)
        if not candidates:
            candidates = self.parse_candidates(content, max_candidates=max_candidates)
        candidates = self.rank_candidates_by_context(
            candidates, dictionary_context or []
        )
        logging.info("LLM parsed candidates elapsed_ms=%s count=%s", elapsed_ms, len(candidates))
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
        if self.log_user_input:
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
        if self.log_model_output:
            logging.info(
                "LLM refinement raw output elapsed_ms=%s content=%r", elapsed_ms, content
            )
        candidates = self.extract_message_candidates(message, max_candidates=max_candidates)
        if not candidates:
            candidates = self.parse_candidates(content, max_candidates=max_candidates)
        logging.info(
            "LLM refinement parsed candidates elapsed_ms=%s count=%s",
            elapsed_ms,
            len(candidates),
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
        if self.log_user_input:
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
        if self.log_model_output:
            logging.info(
                "LLM more candidates raw output elapsed_ms=%s content=%r", elapsed_ms, content
            )
        candidates = self.extract_message_candidates(message, max_candidates=max_candidates)
        if not candidates:
            candidates = self.parse_candidates(content, max_candidates=max_candidates)
        excluded = set(excluded_candidates or [])
        candidates = [candidate for candidate in candidates if candidate not in excluded]
        logging.info(
            "LLM more candidates parsed elapsed_ms=%s count=%s",
            elapsed_ms,
            len(candidates),
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
        body = self.build_request_body(
            pinyin,
            dictionary_context=dictionary_context or [],
            recent_committed_text=recent_committed_text or "",
            recent_committed_turns=recent_committed_turns or [],
            surrounding_context=surrounding_context or {},
            stream=True,
        )
        self.log_request_body("LLM stream", body)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        session = requests.Session()
        session.trust_env = bool(self.proxy_enabled)
        url = self.base_url + self.endpoint
        response = None
        buffer = ""
        emitted = set()
        start = time.monotonic()

        def take_complete_candidates(final=False):
            nonlocal buffer
            pieces = re.split(r"([|\n])", buffer)
            if final:
                complete = pieces
                buffer = ""
            else:
                last_delimiter = -1
                for index, piece in enumerate(pieces):
                    if piece in ("|", "\n"):
                        last_delimiter = index
                if last_delimiter < 0:
                    return []
                complete = pieces[: last_delimiter + 1]
                buffer = "".join(pieces[last_delimiter + 1 :])
            text = "".join(complete)
            return self.parse_candidates(text, max_candidates=max_candidates)

        try:
            response = session.post(
                url,
                headers=headers,
                json=body,
                stream=True,
                timeout=(self.timeout, max(self.stream_timeout, self.timeout)),
            )
            response.raise_for_status()
            response.encoding = "utf-8"
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if line.startswith(":") or line.startswith(("event:", "id:", "retry:")):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if not line or line == "[DONE]":
                    if line == "[DONE]":
                        break
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    buffer += line
                else:
                    self.log_usage(event.get("usage"), label="LLM stream")
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or choice.get("message") or {}
                    buffer += delta.get("content") or delta.get("reasoning_content") or ""
                for candidate in take_complete_candidates():
                    if candidate in emitted:
                        continue
                    emitted.add(candidate)
                    yield candidate
                    if len(emitted) >= max_candidates:
                        return

            for candidate in take_complete_candidates(final=True):
                if candidate in emitted:
                    continue
                emitted.add(candidate)
                yield candidate
                if len(emitted) >= max_candidates:
                    return
            logging.info(
                "LLM stream completed elapsed_ms=%s count=%s",
                int((time.monotonic() - start) * 1000),
                len(emitted),
            )
        finally:
            if response is not None:
                response.close()
            session.close()

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
                    if self.log_model_output:
                        logging.warning("LLM tool call arguments invalid JSON arguments=%r", arguments)
                    else:
                        logging.warning("LLM tool call arguments invalid JSON")
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
        return "D:" + ";".join(line[2:] for line in lines) + "\nD命中须原样使用。"

    def format_recent_committed_text(self, text):
        text = " ".join(str(text or "").split())
        if not text:
            return ""
        if self.max_history_chars:
            text = text[-self.max_history_chars :]
        return "之前输入的内容:" + text

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

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            arr = payload.get("candidates", [])
        elif isinstance(payload, list):
            arr = payload
        else:
            arr = re.split(r"\s*[|\n]\s*", text)
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
        if re.fullmatch(r'''[A-Za-z0-9][A-Za-z0-9 !?.,:;@#%&+*/_='"()\-]{0,63}''', candidate):
            return True
        return False
