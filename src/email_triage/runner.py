"""编排：逐封邮件调用模型，做预算、绑定与失败闭合。"""

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from .client import ModelError
from .config import TriageConfig
from .models import EmailRecord, Outcome, RunSummary
from .parse import ParseError, parse_classification
from .prompt import build_messages, prompt_fingerprint
from .redaction import message_ref, sanitize_text


class BudgetExceeded(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class BudgetState:
    max_model_calls: int
    per_call_timeout_s: float
    wall_clock_timeout_s: float
    max_total_input_bytes: int
    model_calls: int = 0
    input_bytes: int = 0
    started_monotonic: float = 0.0

    def start(self) -> None:
        self.started_monotonic = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.started_monotonic

    def check_call(self, next_input_bytes: int) -> None:
        if self.model_calls >= self.max_model_calls:
            raise BudgetExceeded("达到调用上限 %d" % self.max_model_calls)
        if self.elapsed() > self.wall_clock_timeout_s:
            raise BudgetExceeded("超过整轮墙钟上限 %.0f 秒" % self.wall_clock_timeout_s)
        if self.input_bytes + next_input_bytes > self.max_total_input_bytes:
            raise BudgetExceeded("超过累计输入上限 %d 字节" % self.max_total_input_bytes)

    def record_call(self, input_bytes: int) -> None:
        self.model_calls += 1
        self.input_bytes += input_bytes


class Runner:
    def __init__(self, config: TriageConfig, client, audit_sink=None):
        self.config = config
        self.client = client
        self.audit_sink = audit_sink
        self.budget = BudgetState(
            max_model_calls=config.budget.max_model_calls,
            per_call_timeout_s=config.budget.per_call_timeout_s,
            wall_clock_timeout_s=config.budget.wall_clock_timeout_s,
            max_total_input_bytes=config.budget.max_total_input_bytes,
        )
        self._last_transport_error: Optional[str] = None

    def _emit(self, entry: dict) -> None:
        if self.audit_sink is not None:
            self.audit_sink(entry)

    def run(self, records: List[EmailRecord], run_id: str) -> RunSummary:
        summary = RunSummary(run_id=run_id, started_at=utc_now())
        self.budget.start()
        self._emit(
            {
                "event": "run_start",
                "run_id": run_id,
                "ts": summary.started_at,
                "model": self.config.model.model,
                "base_url_host": self.config.model.base_url,
                "categories": self.config.category_names(),
                "prompt_fingerprint": prompt_fingerprint(self.config),
                "budget": {
                    "max_model_calls": self.budget.max_model_calls,
                    "per_call_timeout_s": self.budget.per_call_timeout_s,
                    "wall_clock_timeout_s": self.budget.wall_clock_timeout_s,
                    "max_total_input_bytes": self.budget.max_total_input_bytes,
                },
                "sample_count": len(records),
            }
        )

        for record in records:
            messages = build_messages(self.config, record)
            payload_bytes = sum(len(m["content"].encode("utf-8")) for m in messages)
            ref = message_ref(record.source_id, record.subject, record.body)
            label = sanitize_text(record.subject, 60) or "(无主题)"
            try:
                self.budget.check_call(payload_bytes)
            except BudgetExceeded as error:
                summary.aborted = True
                summary.abort_reason = str(error)
                break

            started = time.monotonic()
            try:
                response = self.client.complete(
                    messages,
                    timeout_s=self.budget.per_call_timeout_s,
                    max_tokens=self.config.budget.max_tokens,
                )
            except ModelError as error:
                outcome = Outcome(
                    source_ref=ref,
                    subject_label=label,
                    status="failed",
                    failure="model_%s" % error.kind,
                    latency_s=time.monotonic() - started,
                    truncated=record.truncated,
                )
                summary.outcomes.append(outcome)
                summary.failed += 1
                self._emit_result(run_id, outcome)
                summary.aborted = True
                summary.abort_reason = "模型端点不可用: %s" % error.kind
                break

            self.budget.record_call(payload_bytes)
            try:
                category, reason = parse_classification(response.content, self.config.category_names())
                outcome = Outcome(
                    source_ref=ref,
                    subject_label=label,
                    status="classified",
                    category=category,
                    reason=reason,
                    latency_s=response.latency_s,
                    truncated=record.truncated,
                )
                summary.classified += 1
            except ParseError as error:
                outcome = Outcome(
                    source_ref=ref,
                    subject_label=label,
                    status="failed",
                    failure="parse_%s" % error.kind,
                    latency_s=response.latency_s,
                    truncated=record.truncated,
                )
                summary.failed += 1
            summary.outcomes.append(outcome)
            self._emit_result(run_id, outcome)

        summary.model_calls = self.budget.model_calls
        summary.finished_at = utc_now()
        self._emit(dict(summary.to_audit(), event="run_end", elapsed_s=round(self.budget.elapsed(), 3)))
        return summary

    def _emit_result(self, run_id: str, outcome: Outcome) -> None:
        entry = {"event": "message_result", "run_id": run_id, "ts": utc_now()}
        entry.update(outcome.to_audit())
        self._emit(entry)


def summary_as_json(summary: RunSummary) -> str:
    return json.dumps(
        {"summary": summary.to_audit(), "outcomes": [o.to_audit() for o in summary.outcomes]},
        ensure_ascii=False,
        indent=2,
    )
