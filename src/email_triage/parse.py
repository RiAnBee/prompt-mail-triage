"""模型输出解析：只接受严格 JSON，不做容错恢复。"""

import json
from typing import Optional, Tuple

MAX_REASON_CHARS = 160


class ParseError(ValueError):
    def __init__(self, kind: str, message: str):
        super().__init__("%s: %s" % (kind, message))
        self.kind = kind
        self.message = message


def _strip_wrapping(text: str) -> str:
    stripped = text.strip()
    # 只容忍整体被代码围栏包住这一种情况，其他"修复"一律不做。
    if stripped.startswith("```") and stripped.endswith("```"):
        inner = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = inner.rsplit("```", 1)[0].strip()
    return stripped


def parse_classification(text: str, allowed: list) -> Tuple[str, Optional[str]]:
    if not text or not text.strip():
        raise ParseError("empty", "模型返回空内容")
    candidate = _strip_wrapping(text)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        raise ParseError("not_json", "输出不是单个 JSON 对象")
    if not isinstance(parsed, dict):
        raise ParseError("not_object", "输出不是 JSON 对象")
    category = parsed.get("category")
    if not isinstance(category, str) or not category.strip():
        raise ParseError("missing_category", "缺少 category 字段")
    category = category.strip()
    if category not in allowed:
        raise ParseError("unknown_category", "类别不在给定范围: %s" % category[:40])
    reason = parsed.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ParseError("bad_reason", "reason 不是字符串")
    if reason is not None:
        reason = " ".join(reason.split())[:MAX_REASON_CHARS]
    return category, reason
