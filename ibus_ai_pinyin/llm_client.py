import json
import logging
import os
import re
import time

import requests


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

        prompt = config.get("prompt", {})
        self.system_prompt = prompt.get(
            "system",
            "你是一个中文拼音输入法转换器。只输出 JSON 字符串数组。",
        )
        self.user_template = prompt.get(
            "user_template",
            "拼音：{pinyin}\n请输出中文候选 JSON 数组，必须正好 5 个字符串。",
        )

    def build_request_body(
        self,
        pinyin,
        dictionary_context=None,
        recent_committed_text=None,
        recent_committed_turns=None,
        stream=None,
    ):
        messages = self.build_messages(
            pinyin,
            dictionary_context=dictionary_context or [],
            recent_committed_text=recent_committed_text or "",
            recent_committed_turns=recent_committed_turns or [],
        )
        return self.build_chat_body(messages, stream=stream)

    def build_messages(
        self,
        pinyin,
        dictionary_context=None,
        recent_committed_text=None,
        recent_committed_turns=None,
    ):
        messages = [{"role": "system", "content": self.system_prompt}]
        for turn in self.format_recent_committed_turns(recent_committed_turns or []):
            messages.append({"role": "user", "content": self.user_template.format(pinyin=turn["pinyin"])})
            messages.append({"role": "assistant", "content": json.dumps([turn["text"]], ensure_ascii=False)})

        user_content = self.build_user_content(
            pinyin,
            dictionary_context=dictionary_context or [],
            recent_committed_text=recent_committed_text or "",
        )
        messages.append({"role": "user", "content": user_content})
        return messages

    def build_user_content(
        self,
        pinyin,
        dictionary_context=None,
        recent_committed_text=None,
    ):
        if self.should_use_deepseek_cache_layout():
            return self.build_deepseek_cache_user_content(
                pinyin,
                dictionary_context=dictionary_context or [],
                recent_committed_text=recent_committed_text or "",
            )

        user_content = self.user_template.format(pinyin=pinyin)
        recent_text = self.format_recent_committed_text(recent_committed_text or "")
        if recent_text:
            user_content = f"{user_content}\n\n{recent_text}"
        context_text = self.format_dictionary_context(dictionary_context or [])
        if context_text:
            user_content = f"{user_content}\n\n{context_text}"
        return user_content

    def build_deepseek_cache_user_content(
        self,
        pinyin,
        dictionary_context=None,
        recent_committed_text=None,
    ):
        sections = [
            "任务：将当前拼音转换为最可能的中文候选。",
            "输出要求：只输出 JSON 字符串数组，不要解释，不要 Markdown，不要代码块；必须正好输出 5 个候选字符串。",
        ]
        recent_text = self.format_recent_committed_text(recent_committed_text or "")
        if recent_text:
            sections.append(recent_text)
        context_text = self.format_dictionary_context(dictionary_context or [])
        if context_text:
            sections.append(context_text)
        sections.append(f"当前拼音：{pinyin}\n请输出中文候选 JSON 数组，必须正好 5 个字符串。")
        return "\n\n".join(sections)

    def build_chat_body(self, user_content, stream=None):
        if isinstance(user_content, list):
            messages = user_content
        else:
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_content},
            ]
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "stream": self.stream if stream is None else stream,
        }
        if isinstance(self.extra_body, dict):
            body.update(self.extra_body)
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
    ):
        body = self.build_request_body(
            pinyin,
            dictionary_context=dictionary_context or [],
            recent_committed_text=recent_committed_text or "",
            recent_committed_turns=recent_committed_turns or [],
        )
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
        candidates = self.parse_candidates(content, max_candidates=max_candidates)
        candidates = self.rank_candidates_by_context(
            candidates, dictionary_context or []
        )
        logging.info(
            "LLM parsed candidates elapsed_ms=%s candidates=%r", elapsed_ms, candidates
        )
        return candidates

    def refine_candidates(
        self, pinyin, current_candidates, instruction, max_candidates=5
    ):
        current_text = "\n".join(
            f"{index + 1}. {candidate}"
            for index, candidate in enumerate(current_candidates or [])
        )
        user_content = (
            f"拼音：{pinyin}\n"
            # f"当前候选：\n{current_text}\n"
            f"用户补充说明：{instruction}\n"
            "请结合补充说明，输出新的中文候选 JSON 数组。"
            "优先保留正确候选，只调整不符合说明的候选，不要解释，不要 Markdown。"
        )
        body = self.build_chat_body(user_content, stream=False)
        logging.info("LLM refinement user content=%r", user_content)

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
        candidates = self.parse_candidates(content, max_candidates=max_candidates)
        logging.info(
            "LLM refinement parsed candidates elapsed_ms=%s candidates=%r",
            elapsed_ms,
            candidates,
        )
        return candidates

    def get_more_candidates(self, pinyin, excluded_candidates, max_candidates=5):
        excluded_text = "\n".join(
            f"{index + 1}. {candidate}"
            for index, candidate in enumerate(excluded_candidates or [])
        )
        user_content = (
            f"拼音：{pinyin}\n"
            f"排除这些已有候选：\n{excluded_text}\n"
            "请生成一组新的中文候选 JSON 数组。"
            "不要输出排除列表里已有的候选，不要解释，不要 Markdown。"
        )
        body = self.build_chat_body(user_content, stream=False)
        logging.info("LLM more candidates user content=%r", user_content)

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
    ):
        body = self.build_request_body(
            pinyin,
            dictionary_context=dictionary_context or [],
            recent_committed_text=recent_committed_text or "",
            recent_committed_turns=recent_committed_turns or [],
            stream=True,
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Connection": "close",
        }

        session = requests.Session()
        session.trust_env = bool(self.proxy_enabled)
        url = self.base_url + self.endpoint
        start = time.monotonic()
        content = ""
        emitted = []
        seen = set()
        try:
            resp = session.post(
                url,
                headers=headers,
                json=body,
                timeout=self.timeout,
                stream=True,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logging.info(
                "LLM stream response received model=%s status=%s elapsed_ms=%s",
                self.model,
                resp.status_code,
                elapsed_ms,
            )
            try:
                resp.raise_for_status()
            except requests.HTTPError:
                logging.error(
                    "LLM stream HTTP error model=%s status=%s response=%r",
                    self.model,
                    resp.status_code,
                    resp.text[:1000],
                )
                raise

            for raw_line in resp.iter_lines(decode_unicode=False):
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    logging.debug("LLM stream ignored non-json payload=%r", payload)
                    continue
                self.log_usage(data.get("usage"), label="LLM stream")
                for choice in data.get("choices", []):
                    delta = choice.get("delta") or {}
                    message = choice.get("message") or {}
                    chunk = (
                        delta.get("content")
                        or message.get("content")
                        or data.get("content")
                        or ""
                    )
                    if not chunk:
                        continue
                    content += chunk
                    for candidate in self.extract_complete_candidates(content):
                        if candidate in seen or not self.is_valid_candidate(candidate):
                            continue
                        seen.add(candidate)
                        emitted.append(candidate)
                        yield candidate
                        if len(emitted) >= max_candidates:
                            return
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logging.exception(
                "LLM stream request failed model=%s elapsed_ms=%s url=%s",
                self.model,
                elapsed_ms,
                url,
            )
            raise
        finally:
            session.close()

        if not emitted and content:
            for candidate in self.parse_candidates(
                content, max_candidates=max_candidates
            ):
                if candidate not in seen:
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

        arr = json.loads(text)
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
