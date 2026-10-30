"""IMAP 只读连接器。

只做三件事：EXAMINE（只读选择目录）、UID SEARCH、UID FETCH BODY.PEEK。
不调用 STORE / COPY / MOVE / EXPUNGE，因此不会改变已读标记或邮箱结构。
凭据只从环境变量读取，不进配置、日志或 prompt。
"""

import email
import imaplib
import os
import subprocess
from email.header import decode_header, make_header
from typing import List, Optional

from .models import EmailRecord
from .sources import SourceError
from .imap_mailbox import encode_mailbox, quote_mailbox

ENV_HOST = "EMAIL_IMAP_HOST"
ENV_PORT = "EMAIL_IMAP_PORT"
ENV_USER = "EMAIL_IMAP_USER"
ENV_PASSWORD = "EMAIL_IMAP_PASSWORD"
ENV_PASSWORD_FILE = "EMAIL_IMAP_PASSWORD_FILE"
ENV_KEYCHAIN_ITEM = "EMAIL_IMAP_KEYCHAIN_ITEM"
ENV_SSL = "EMAIL_IMAP_SSL"

ALLOWED_COMMANDS = ("EXAMINE", "UID SEARCH", "UID FETCH", "LOGOUT", "CAPABILITY", "LOGIN")


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return value


def _text_body(message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                part.get("Content-Disposition", "")
            ).lower():
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, "replace")
        return ""
    payload = message.get_payload(decode=True) or b""
    charset = message.get_content_charset() or "utf-8"
    return payload.decode(charset, "replace")


def _password_from_file(path: str) -> str:
    try:
        mode = os.stat(path).st_mode & 0o777
        if mode & 0o077:
            raise SourceError("密码文件权限过宽（%o），先 chmod 600 %s" % (mode, path))
        with open(path, "r", encoding="utf-8") as handle:
            return handle.readline().strip()
    except FileNotFoundError:
        raise SourceError("密码文件不存在: %s" % path)


def _password_from_keychain(service: str, account: str) -> str:
    """macOS 钥匙串取密码；值不落盘、不进 shell 历史。"""

    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SourceError("读取钥匙串失败: %s" % error)
    if result.returncode != 0:
        raise SourceError("钥匙串里没有该项: service=%s account=%s" % (service, account))
    return result.stdout.decode("utf-8", "ignore").strip()


def credentials_from_env() -> dict:
    host = os.environ.get(ENV_HOST, "").strip()
    user = os.environ.get(ENV_USER, "").strip()
    password = os.environ.get(ENV_PASSWORD, "")
    password_file = os.environ.get(ENV_PASSWORD_FILE, "").strip()
    keychain_item = os.environ.get(ENV_KEYCHAIN_ITEM, "").strip()
    if not password and password_file:
        password = _password_from_file(password_file)
    if not password and keychain_item:
        password = _password_from_keychain(keychain_item, user)
    if not host or not user or not password:
        raise SourceError(
            "缺少 IMAP 凭据：设置 %s / %s，再用 %s、%s 或 %s 提供密码"
            % (ENV_HOST, ENV_USER, ENV_PASSWORD, ENV_PASSWORD_FILE, ENV_KEYCHAIN_ITEM)
        )
    use_ssl = os.environ.get(ENV_SSL, "1").strip().lower() not in ("0", "false", "no")
    port = int(os.environ.get(ENV_PORT, "993" if use_ssl else "143"))
    return {"host": host, "port": port, "user": user, "password": password, "ssl": use_ssl}


def _connect(host: str, port: int, use_ssl: bool) -> imaplib.IMAP4:
    if use_ssl:
        return imaplib.IMAP4_SSL(host, port)
    return imaplib.IMAP4(host, port)


def fetch_recent(
    host: str,
    port: int,
    user: str,
    password: str,
    use_ssl: bool = True,
    mailbox: str = "INBOX",
    limit: int = 5,
    max_body_bytes: int = 32768,
    connection_factory=None,
) -> List[EmailRecord]:
    factory = connection_factory or _connect
    try:
        connection = factory(host, port, use_ssl)
    except OSError as error:
        raise SourceError("IMAP 连接失败: %s" % error)
    try:
        connection.login(user, password)
        status, _ = connection.select(quote_mailbox(encode_mailbox(mailbox)), readonly=True)
        if status != "OK":
            raise SourceError("只读选择目录失败: %s" % mailbox)

        uid_validity = "0"
        _typ, validity_data = connection.response("UIDVALIDITY") or (None, None)
        for item in validity_data or []:
            if isinstance(item, bytes):
                uid_validity = item.decode("ascii", "ignore")
                break

        status, data = connection.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            raise SourceError("UID SEARCH 未返回结果")
        uids = data[0].split()
        selected = uids[-limit:] if limit > 0 else uids

        records: List[EmailRecord] = []
        for uid in selected:
            status, payload = connection.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK":
                raise SourceError("UID FETCH 失败: %s" % uid.decode("ascii", "ignore"))
            raw = None
            for item in payload or []:
                if isinstance(item, tuple) and len(item) >= 2:
                    raw = item[1]
                    break
            if raw is None:
                continue
            message = email.message_from_bytes(raw)
            body = _text_body(message)
            encoded = body.encode("utf-8")
            truncated = len(encoded) > max_body_bytes
            if truncated:
                body = encoded[:max_body_bytes].decode("utf-8", "ignore")
            uid_text = uid.decode("ascii", "ignore")
            records.append(
                EmailRecord(
                    source_id="imap:%s:%s" % (uid_validity, uid_text),
                    folder=mailbox,
                    subject=_decode(message.get("Subject")),
                    sender=_decode(message.get("From")),
                    received_at=_decode(message.get("Date")),
                    body=body,
                    truncated=truncated,
                )
            )
        if not records:
            raise SourceError("没有取到任何邮件")
        return records
    except (imaplib.IMAP4.error, ValueError) as error:
        raise SourceError("IMAP 协议错误: %s" % error)
    finally:
        try:
            connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
