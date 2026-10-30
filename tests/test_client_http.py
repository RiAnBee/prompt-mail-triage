"""HTTP 边界测试：用本地 stub server 验证真实 socket 上的请求/响应与代理旁路。"""

import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from email_triage.client import ModelError, OpenAICompatClient  # noqa: E402


class StubHandler(BaseHTTPRequestHandler):
    mode = "ok"
    captured = {}

    def log_message(self, fmt, *args):  # 静音
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        StubHandler.captured = {"path": self.path, "body": body,
                                "authorization": self.headers.get("Authorization")}
        if StubHandler.mode == "slow":
            time.sleep(3)
        if StubHandler.mode == "error":
            payload = b'{"error":"boom"}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if StubHandler.mode == "truncated":
            payload = json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": None},
                              "finish_reason": "length"}]}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        payload = json.dumps(
            {
                "choices": [{"message": {"role": "assistant", "content": '{"category":"other"}'},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class HttpBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), StubHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        StubHandler.mode = "ok"
        StubHandler.captured = {}
        self.base = "http://127.0.0.1:%d/v1" % self.port

    def _client(self, **kwargs):
        return OpenAICompatClient(self.base, "stub-model", **kwargs)

    def test_posts_chat_completions_and_reads_content(self):
        response = self._client().complete([{"role": "user", "content": "hi"}], timeout_s=5, max_tokens=32)
        self.assertEqual(response.content, '{"category":"other"}')
        self.assertEqual(StubHandler.captured["path"], "/v1/chat/completions")
        body = StubHandler.captured["body"]
        self.assertEqual(body["model"], "stub-model")
        self.assertFalse(body["stream"])
        self.assertEqual(body["max_tokens"], 32)

    def test_api_key_is_sent_as_bearer(self):
        client = OpenAICompatClient(self.base, "stub-model", api_key="secret-key")
        client.complete([{"role": "user", "content": "hi"}], timeout_s=5, max_tokens=8)
        self.assertEqual(StubHandler.captured["authorization"], "Bearer secret-key")

    def test_no_api_key_means_no_authorization_header(self):
        self._client().complete([{"role": "user", "content": "hi"}], timeout_s=5, max_tokens=8)
        self.assertIsNone(StubHandler.captured["authorization"])

    def test_http_error_is_mapped(self):
        StubHandler.mode = "error"
        with self.assertRaises(ModelError) as ctx:
            self._client().complete([{"role": "user", "content": "hi"}], timeout_s=5, max_tokens=8)
        self.assertEqual(ctx.exception.kind, "http_500")

    def test_truncated_output_is_distinguished(self):
        StubHandler.mode = "truncated"
        with self.assertRaises(ModelError) as ctx:
            self._client().complete([{"role": "user", "content": "hi"}], timeout_s=5, max_tokens=8)
        self.assertEqual(ctx.exception.kind, "truncated_output")

    def test_timeout_is_enforced(self):
        StubHandler.mode = "slow"
        started = time.monotonic()
        with self.assertRaises(ModelError) as ctx:
            self._client().complete([{"role": "user", "content": "hi"}], timeout_s=0.5, max_tokens=8)
        self.assertIn(ctx.exception.kind, ("timeout", "transport"))
        self.assertLess(time.monotonic() - started, 2.5)

    def test_loopback_ignores_env_proxy(self):
        # 上一个会话的阻塞正是本机代理掐断连接；这里固定该回归。
        os.environ["http_proxy"] = "http://127.0.0.1:9"
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
        try:
            response = self._client(use_env_proxy=False).complete(
                [{"role": "user", "content": "hi"}], timeout_s=5, max_tokens=8
            )
            self.assertEqual(response.content, '{"category":"other"}')
            with self.assertRaises(ModelError):
                self._client(use_env_proxy=True).complete(
                    [{"role": "user", "content": "hi"}], timeout_s=2, max_tokens=8
                )
        finally:
            os.environ.pop("http_proxy", None)
            os.environ.pop("HTTP_PROXY", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
