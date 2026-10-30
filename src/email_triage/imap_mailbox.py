"""IMAP mailbox names: modified UTF-7 and quoted-string arguments."""

import base64
import re


def encode_mailbox(name: str) -> str:
    """Encode a Unicode mailbox using RFC 3501 modified UTF-7."""
    out, buffer = [], []

    def flush():
        if buffer:
            raw = "".join(buffer).encode("utf-16-be")
            encoded = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
            out.append("&%s-" % encoded)
            buffer.clear()

    for char in name:
        if 0x20 <= ord(char) <= 0x7E:
            flush()
            out.append("&-" if char == "&" else char)
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            raise ValueError("Mailbox name contains a control character")
        else:
            buffer.append(char)
    flush()
    return "".join(out)


def quote_mailbox(wire_name: str) -> str:
    """Quote an already encoded mailbox for imaplib command arguments."""
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in wire_name):
        raise ValueError("Mailbox name contains a control character")
    return '"' + wire_name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def list_mailbox_name(response: bytes) -> str:
    """Extract a quoted or atom mailbox from an IMAP LIST response."""
    text = response.decode("ascii")
    match = re.fullmatch(r'(?:\* LIST )?\([^)]*\) (?:"(?:\\.|[^"\\])*"|NIL) (.+)', text)
    if not match:
        raise ValueError("Unsupported IMAP LIST response")
    name = match.group(1)
    if name.startswith('"') and name.endswith('"'):
        return re.sub(r'\\(.)', r'\1', name[1:-1])
    return name
