"""模型客户端：OpenAI 兼容 Chat Completions，加一个离线 mock。

约束：
- 回环地址默认不使用环境变量里的代理（本机代理曾直接掐断这类连接）。
- 单次调用有硬超时；失败区分 transport / http / shape，交给上层 fail closed。
"""

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional

from .redaction import sanitize_text


class ModelError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__("%s: %s" % (kind, message))
        self.kind = kind
        self.message = message


@dataclass
class ChatResponse:
    content: str
    latency_s: float
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None


class MockClient:
    """离线路径测试用：按关键词命中类别名，未命中用最后一个类别。

    它只验证编排、解析、绑定和预算，不代表模型能力。
    """

    def __init__(self, category_names: List[str]):
        if not category_names:
            raise ValueError("MockClient 需要至少一个类别")
        self.category_names = list(category_names)

    def complete(self, messages: List[Dict[str, str]], timeout_s: float, max_tokens: int) -> ChatResponse:
        started = time.monotonic()
        user = messages[-1]["content"]
        payload_line = ""
        marker = '<untrusted-email nonce="'
        if marker in user:
            tail = user.split(marker, 1)[1]
            payload_line = tail.split("\n", 2)[1]
        try:
            email = json.loads(payload_line)
        except (json.JSONDecodeError, IndexError):
            email = {}
        haystack = " ".join(str(email.get(k, "")) for k in ("subject", "body", "folder")).lower()
        chosen = self.category_names[-1]
        for name in self.category_names[:-1]:
            if name.lower().replace("_", " ").replace("-", " ") in haystack.replace("-", " ").replace("_", " "):
                chosen = name
                break
        content = json.dumps(
            {"category": chosen, "reason": "mock: keyword match on %s" % chosen},
            ensure_ascii=False,
        )
        return ChatResponse(content=content, latency_s=time.monotonic() - started, finish_reason="stop")


class OpenAICompatClient:
    def __init__(self, base_url: str, model: str, temperature: float = 0.0,
                 reasoning_effort: Optional[str] = "low", use_env_proxy: bool = False,
                 api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.use_env_proxy = use_env_proxy
        self._api_key = api_key or None
        handlers = [] if use_env_proxy else [urllib.request.ProxyHandler({})]
        self._opener = urllib.request.build_opener(*handlers)

    def _url(self) -> str:
        if self.base_url.endswith("/v1"):
            return self.base_url + "/chat/completions"
        return self.base_url + "/v1/chat/completions"

    def complete(self, messages: List[Dict[str, str]], timeout_s: float, max_tokens: int) -> ChatResponse:
        body: Dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer %s" % self._api_key
        request = urllib.request.Request(
            self._url(),
            data=data,
            headers=headers,
            method="POST",
        )
        started = time.monotonic()
        try:
            with self._opener.open(request, timeout=timeout_s) as response:
                raw = response.read(4 * 1024 * 1024)
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = sanitize_text(error.read(2048).decode("utf-8", "ignore"), 200)
            except OSError:
                detail = ""
            raise ModelError("http_%s" % error.code, detail or error.reason)
        except (socket.timeout, TimeoutError):
            raise ModelError("timeout", "超过 %.1f 秒未返回" % timeout_s)
        except urllib.error.URLError as error:
            raise ModelError("transport", sanitize_text(str(error.reason), 200))
        except OSError as error:
            raise ModelError("transport", sanitize_text(str(error), 200))

        latency = time.monotonic() - started
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ModelError("shape", "响应不是 JSON")
        choices = parsed.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelError("shape", "响应缺少 choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        finish_reason = choices[0].get("finish_reason")
        if not isinstance(content, str) or not content.strip():
            if finish_reason == "length":
                raise ModelError("truncated_output", "达到 max_tokens 上限，正文为空；先看 reasoning 预算")
            raise ModelError("shape", "message.content 为空")
        usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else None
        return ChatResponse(
            content=content,
            latency_s=latency,
            finish_reason=finish_reason,
            usage={k: v for k, v in usage.items() if isinstance(v, int)} if usage else None,
        )
