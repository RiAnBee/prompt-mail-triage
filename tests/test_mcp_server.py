"""MCP server 的协议与只读边界测试。"""

import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from email_triage import mcp_server  # noqa: E402

SAMPLE = ROOT / "samples" / "sample-emails.jsonl"


class StubModel(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        payload = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"category":"other","reason":"stub"}',
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class McpProtocolTests(unittest.TestCase):
    def _tmp(self):
        import tempfile

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config_path = Path(directory.name) / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "task_instruction": "把邮件归入给定类别。",
                    "categories": [
                        {"name": "account-access", "description": "账号相关"},
                        {"name": "other", "description": "其他"},
                    ],
                    "budget": {"max_model_calls": 3, "per_call_timeout_s": 10,
                               "wall_clock_timeout_s": 30, "max_body_bytes_per_message": 4096,
                               "max_total_input_bytes": 65536, "max_tokens": 128},
                    "model": {
                        "base_url": "http://127.0.0.1:%d/v1" % self.server.server_address[1],
                        "model": "stub",
                        "reasoning_effort": None,
                        "use_env_proxy": False,
                    },
                }
            ),
            encoding="utf-8",
        )
        return str(config_path)

    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), StubModel)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.config = self._tmp()

    def _env(self):
        return mock.patch.dict(os.environ, {"EMAIL_TRIAGE_CONFIG": self.config,
                                            "EMAIL_TRIAGE_RUN_ROOT": str(Path(self.config).parent)},
                               clear=False)

    def test_initialize_reports_tool_capability(self):
        response = mcp_server.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}}
        )
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", response["result"]["capabilities"])
        self.assertEqual(response["result"]["serverInfo"]["name"], "email-triage")

    def test_initialize_falls_back_to_supported_version(self):
        response = mcp_server.handle_request(
            {"jsonrpc": "2.0", "id": 9, "method": "initialize",
             "params": {"protocolVersion": "1999-01-01"}}
        )
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")

    def test_tools_are_read_only(self):
        response = mcp_server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertEqual(names, {"list_categories", "classify_jsonl", "classify_imap"})
        for forbidden in ("move", "label", "mark", "send", "delete", "append", "copy"):
            self.assertFalse([n for n in names if forbidden in n.lower()],
                             "%s 不应出现在只读 server 里" % forbidden)

    def test_list_categories_returns_config(self):
        with self._env():
            response = mcp_server.handle_request(
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "list_categories", "arguments": {}}}
            )
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual([c["name"] for c in payload["categories"]], ["account-access", "other"])
        self.assertTrue(payload["read_only"])

    def test_classify_jsonl_runs_against_model(self):
        with self._env():
            response = mcp_server.handle_request(
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                 "params": {"name": "classify_jsonl",
                            "arguments": {"path": str(SAMPLE), "limit": 2}}}
            )
        self.assertFalse(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(len(payload["outcomes"]), 2)
        self.assertEqual(payload["outcomes"][0]["category"], "other")

    def test_unknown_tool_is_refused(self):
        with self._env():
            response = mcp_server.handle_request(
                {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                 "params": {"name": "move_message", "arguments": {}}}
            )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("只读", response["result"]["content"][0]["text"])

    def test_unknown_method_returns_error(self):
        response = mcp_server.handle_request({"jsonrpc": "2.0", "id": 6, "method": "resources/list"})
        self.assertEqual(response["error"]["code"], -32601)

    def test_missing_config_is_reported(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            response = mcp_server.handle_request(
                {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                 "params": {"name": "list_categories", "arguments": {}}}
            )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("EMAIL_TRIAGE_CONFIG", response["result"]["content"][0]["text"])

    def test_stdio_end_to_end(self):
        env = dict(os.environ)
        env["EMAIL_TRIAGE_CONFIG"] = self.config
        env["EMAIL_TRIAGE_RUN_ROOT"] = str(Path(self.config).parent)
        env["PYTHONPATH"] = str(ROOT / "src")
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        stdin = "\n".join(json.dumps(m) for m in messages) + "\n"
        result = subprocess.run(
            [sys.executable, "-m", "email_triage.mcp_server"],
            input=stdin, capture_output=True, text=True, timeout=60, env=env,
        )
        lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["id"], 1)
        self.assertEqual(lines[1]["result"]["tools"][0]["name"], "list_categories")


if __name__ == "__main__":
    unittest.main(verbosity=2)
