"""数据模型：邮件样本、分类结果、单封处理结局。"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class EmailRecord:
    """一封邮件的只读视图。正文已按字节上限截断。"""

    source_id: str
    folder: str
    subject: str
    sender: str
    received_at: str
    body: str
    truncated: bool = False

    def as_prompt_payload(self) -> dict:
        return {
            "folder": self.folder,
            "from": self.sender,
            "received_at": self.received_at,
            "subject": self.subject,
            "body": self.body,
        }


@dataclass(frozen=True)
class Category:
    name: str
    description: str


@dataclass
class Outcome:
    """单封邮件的处理结局。status 取 classified / failed。"""

    source_ref: str
    subject_label: str
    status: str
    category: Optional[str] = None
    reason: Optional[str] = None
    failure: Optional[str] = None
    latency_s: float = 0.0
    truncated: bool = False

    def to_audit(self) -> dict:
        return {
            "source_ref": self.source_ref,
            "subject_label": self.subject_label,
            "status": self.status,
            "category": self.category,
            "reason": self.reason,
            "failure": self.failure,
            "latency_s": round(self.latency_s, 3),
            "truncated": self.truncated,
        }


@dataclass
class RunSummary:
    run_id: str
    started_at: str
    finished_at: str = ""
    model_calls: int = 0
    classified: int = 0
    failed: int = 0
    aborted: bool = False
    abort_reason: str = ""
    outcomes: List[Outcome] = field(default_factory=list)

    def to_audit(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "model_calls": self.model_calls,
            "classified": self.classified,
            "failed": self.failed,
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
        }
