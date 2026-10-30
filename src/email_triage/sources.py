"""只读输入源。

本轮只提供 JSONL 文件源：由人工/外部只读抓取导出后在本地读取，
程序本身不接触邮箱凭据，也不改变邮箱状态。样本文件不应放进仓库。
"""

import json
from pathlib import Path
from typing import Iterator, List, Optional

from .models import EmailRecord

_REQUIRED = ("id", "subject", "body")


class SourceError(ValueError):
    pass


def _truncate_bytes(text: str, limit: int) -> (str, bool):
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    return encoded[:limit].decode("utf-8", "ignore"), True


def read_jsonl(path: str, max_body_bytes: int, limit: Optional[int] = None) -> List[EmailRecord]:
    records: List[EmailRecord] = []
    for index, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise SourceError("第 %d 行不是合法 JSON: %s" % (index, error))
        missing = [key for key in _REQUIRED if not str(item.get(key, "")).strip()]
        if missing:
            raise SourceError("第 %d 行缺少字段: %s" % (index, ", ".join(missing)))
        body, truncated = _truncate_bytes(str(item["body"]), max_body_bytes)
        records.append(
            EmailRecord(
                source_id=str(item["id"]),
                folder=str(item.get("folder", "")),
                subject=str(item["subject"]),
                sender=str(item.get("sender", "")),
                received_at=str(item.get("received_at", "")),
                body=body,
                truncated=truncated,
            )
        )
        if limit is not None and len(records) >= limit:
            break
    if not records:
        raise SourceError("输入样本为空: %s" % path)
    return records


def iter_records(records: List[EmailRecord]) -> Iterator[EmailRecord]:
    for record in records:
        yield record
