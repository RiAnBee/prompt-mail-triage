"""脱敏与稳定引用。日志默认不写正文、完整地址和原始 prompt。"""

import hashlib
import re

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_DIGITS_RE = re.compile(r"\b\d{12,}\b")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def mask_email(value: str) -> str:
    return _EMAIL_RE.sub("***@***", value)


def sanitize_text(value: str, limit: int = 200) -> str:
    """去掉控制字符与地址后截断，供日志和展示使用。"""

    cleaned = _CONTROL_RE.sub(" ", _LONG_DIGITS_RE.sub("***", mask_email(value)))
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        return cleaned[: limit - 1] + "…"
    return cleaned


def message_ref(*parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]
