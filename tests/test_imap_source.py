"""IMAP 只读连接器的协议级测试：用本地 stub server 记录实际发出的命令。"""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from email_triage.imap_source import credentials_from_env, fetch_recent  # noqa: E402
from email_triage.sources import SourceError  # noqa: E402

MESSAGES = {
    b"1": (
        b"From: it@example.edu\r\nSubject: =?utf-8?b?VlBOIOmAmuefpQ==?=\r\n"
        b"Date: Mon, 20 Oct 2026 09:12:00 +0800\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n\r\n"
        b"\xe8\xaf\xb7\xe5\xae\x89\xe8\xa3\x85\xe6\x96\xb0\xe7\x89\x88 VPN\r\n"
    ),
    b"2": (
        b"From: student@example.edu\r\nSubject: =?utf-8?b?5ZKo6K+i?=\r\n"
        b"Date: Tue, 21 Oct 2026 10:00:00 +0800\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"\xe6\x83\xb3\xe7\xa1\xae\xe8\xae\xa4\xe9\x9d\xa2\xe8\xaf\x95\xe6\x97\xb6\xe9\x97\xb4\r\n"
    ),
}


class StubImapServer:
    """最小 IMAP：只回这个连接器需要的命令。"""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.commands = []
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def _serve(self):
        conn, _ = self.sock.accept()
        with conn:
            conn.sendall(b"* OK stub IMAP4rev1 ready\r\n")
            buffer = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buffer += chunk
                while b"\r\n" in buffer:
                    line, buffer = buffer.split(b"\r\n", 1)
                    if not line.strip():
                        continue
                    parts = line.decode("utf-8", "replace").split(" ")
                    tag, command = parts[0], parts[1].upper()
                    self.commands.append(" ".join(parts[1:]))
                    if not self._respond(conn, tag, command, parts):
                        return

    def _respond(self, conn, tag, command, parts):
        if command == "CAPABILITY":
            conn.sendall(b"* CAPABILITY IMAP4rev1\r\n" + tag.encode() + b" OK CAPABILITY\r\n")
            return True
        if command == "LOGIN":
            conn.sendall(tag.encode() + b" OK LOGIN completed\r\n")
            return True
        if command == "EXAMINE":
            conn.sendall(
                b"* 2 EXISTS\r\n* OK [UIDVALIDITY 4242] UIDs valid\r\n"
                + tag.encode()
                + b" OK [READ-ONLY] EXAMINE completed\r\n"
            )
            return True
        if command == "UID" and len(parts) >= 3 and parts[2].upper() == "SEARCH":
            conn.sendall(b"* SEARCH 1 2\r\n" + tag.encode() + b" OK SEARCH completed\r\n")
            return True
        if command == "UID" and len(parts) >= 3 and parts[2].upper() == "FETCH":
            uid = parts[3].encode()
            raw = MESSAGES[uid]
            conn.sendall(
                b"* 1 FETCH (UID " + uid + b" BODY[] {" + str(len(raw)).encode() + b"}\r\n"
                + raw + b")\r\n" + tag.encode() + b" OK FETCH completed\r\n"
            )
            return True
        if command in ("STORE", "COPY", "MOVE", "EXPUNGE", "APPEND"):
            conn.sendall(tag.encode() + b" NO stub refuses state changes\r\n")
            return True
        if command == "LOGOUT":
            conn.sendall(b"* BYE\r\n" + tag.encode() + b" OK LOGOUT completed\r\n")
            return False
        conn.sendall(tag.encode() + b" BAD unknown command\r\n")
        return True


def connect_factory(port):
    def factory(host, port_number, use_ssl):
        import imaplib

        return imaplib.IMAP4("127.0.0.1", port)

    return factory


class ImapReadOnlyTests(unittest.TestCase):
    def test_fetch_is_read_only_and_peek_only(self):
        server = StubImapServer().start()
        try:
            records = fetch_recent(
                host="127.0.0.1",
                port=server.port,
                user="u",
                password="p",
                use_ssl=False,
                limit=2,
                connection_factory=connect_factory(server.port),
            )
        finally:
            server.stop()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].source_id, "imap:4242:1")
        self.assertIn("VPN", records[0].subject)
        self.assertIn("VPN", records[0].body)
        self.assertEqual(records[1].subject, "咨询")

        joined = " ".join(server.commands)
        self.assertIn("EXAMINE", joined)
        self.assertIn("BODY.PEEK", joined)
        for forbidden in ("STORE", "COPY", "MOVE", "EXPUNGE", "APPEND"):
            self.assertNotIn(forbidden, joined)

    def test_respects_limit_and_truncation(self):
        server = StubImapServer().start()
        try:
            records = fetch_recent(
                host="127.0.0.1",
                port=server.port,
                user="u",
                password="p",
                use_ssl=False,
                limit=1,
                max_body_bytes=8,
                connection_factory=connect_factory(server.port),
            )
        finally:
            server.stop()
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0].truncated)
        self.assertLessEqual(len(records[0].body.encode("utf-8")), 8)

    def test_connection_failure_is_reported(self):
        with self.assertRaises(SourceError):
            fetch_recent(host="127.0.0.1", port=1, user="u", password="p", use_ssl=False, limit=1)


class CredentialSourceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _env(self, **extra):
        base = {"EMAIL_IMAP_HOST": "imap.example.edu", "EMAIL_IMAP_USER": "u@example.edu"}
        base.update(extra)
        return mock.patch.dict(os.environ, base, clear=True)

    def test_reads_password_file_with_strict_permissions(self):
        path = Path(self._tmp.name) / "pw"
        path.write_text("s3cret\n", encoding="utf-8")
        path.chmod(0o600)
        with self._env(EMAIL_IMAP_PASSWORD_FILE=str(path)):
            creds = credentials_from_env()
        self.assertEqual(creds["password"], "s3cret")

    def test_rejects_world_readable_password_file(self):
        path = Path(self._tmp.name) / "pw"
        path.write_text("s3cret\n", encoding="utf-8")
        path.chmod(0o644)
        with self._env(EMAIL_IMAP_PASSWORD_FILE=str(path)):
            with self.assertRaises(SourceError):
                credentials_from_env()

    def test_reads_password_from_keychain(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"kc-secret\n", stderr=b"")
        with self._env(EMAIL_IMAP_KEYCHAIN_ITEM="email-triage-imap"):
            with mock.patch("email_triage.imap_source.subprocess.run", return_value=completed) as run:
                creds = credentials_from_env()
        self.assertEqual(creds["password"], "kc-secret")
        self.assertEqual(run.call_args[0][0][:2], ["security", "find-generic-password"])

    def test_missing_keychain_item_is_reported(self):
        completed = subprocess.CompletedProcess(args=[], returncode=44, stdout=b"", stderr=b"not found")
        with self._env(EMAIL_IMAP_KEYCHAIN_ITEM="missing"):
            with mock.patch("email_triage.imap_source.subprocess.run", return_value=completed):
                with self.assertRaises(SourceError):
                    credentials_from_env()

    def test_missing_everything_is_reported(self):
        with self._env():
            with self.assertRaises(SourceError):
                credentials_from_env()


if __name__ == "__main__":
    unittest.main(verbosity=2)
