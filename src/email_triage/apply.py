"""把一次运行的分类结果落到邮箱目录结构上（显式写操作，默认 dry-run）。

一次腾讯企业邮测试中，自定义关键字未持久化、嵌套目录创建被拒；
因此默认用顶级目录。不同服务商/账号的能力仍需实测。

约定：
- `export_plan()` 只读，算出「哪封邮件 → 哪个目录」，不碰服务器。
- `apply_plan()` 才写：CREATE 目录、COPY 或 MOVE。默认 `copy`，
  原邮件留在收件箱，只多做一份副本；`move` 才真正搬走。
- 不发送独立 EXPUNGE；copy 保留原件，MOVE 会从源目录移走邮件。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .models import EmailRecord
from .redaction import message_ref, sanitize_text
from .imap_mailbox import encode_mailbox, list_mailbox_name, quote_mailbox

SAFE_MODES = ("copy", "move")

# 类别 → 目录后缀（中文更直观；目录名随后按 RFC 3501 编码成 modified UTF-7）
DEFAULT_LABELS = {
    "account-access": "账号访问",
    "campus-it": "校园IT",
    "personal-inquiry": "个人咨询",
    "other": "其他",
}


class ApplyError(Exception):
    pass




def folder_name(prefix: str, category: str, labels: Optional[Dict[str, str]] = None) -> str:
    table = labels or DEFAULT_LABELS
    return "%s-%s" % (prefix, table.get(category, category))


@dataclass
class Action:
    uid: str
    source_id: str
    category: str
    target: str
    subject_label: str


def _load_categories(run_dir: Path) -> Dict[str, str]:
    """source_ref -> category（只取成功分类的）。"""

    path = Path(run_dir) / "summary.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ApplyError("读取运行结果失败: %s" % error)
    mapping: Dict[str, str] = {}
    for outcome in data.get("outcomes", []):
        if outcome.get("status") == "classified" and outcome.get("category"):
            mapping[outcome["source_ref"]] = outcome["category"]
    if not mapping:
        raise ApplyError("%s 里没有成功分类的邮件" % path)
    return mapping


def export_plan(run_dir, records: Iterable[EmailRecord], folder_prefix: str = "分拣",
                labels: Optional[Dict[str, str]] = None) -> List[Action]:
    """纯函数：按与 runner 相同的口径重算 source_ref，匹配到类别。"""

    if "/" in folder_prefix:
        raise ApplyError("目录名不能带 '/'：该服务器不允许在自建目录下再建子目录")
    mapping = _load_categories(Path(run_dir))
    actions: List[Action] = []
    for record in records:
        ref = message_ref(record.source_id, record.subject, record.body)
        category = mapping.get(ref)
        if not category:
            continue
        uid = record.source_id.rsplit(":", 1)[-1]
        actions.append(
            Action(
                uid=uid,
                source_id=record.source_id,
                category=category,
                target=folder_name(folder_prefix, category, labels),
                subject_label=sanitize_text(record.subject, 60),
            )
        )
    if not actions:
        raise ApplyError(
            "没有任何邮件匹配上本次运行结果：运行与本次重取的正文/预算可能不一致"
        )
    return actions


def _folders(connection) -> set:
    status, data = connection.list()
    if status != "OK":
        raise ApplyError("LIST 失败")
    names = set()
    for item in data or []:
        if not isinstance(item, bytes):
            continue
        try:
            names.add(list_mailbox_name(item))
        except (UnicodeDecodeError, ValueError) as error:
            raise ApplyError("无法解析 LIST 目录: %s" % error)
    return names


def _ensure_folder(connection, name: str, existing: set, dry_run: bool) -> None:
    if name in existing:
        return
    if dry_run:
        existing.add(name)
        return
    status, _ = connection.create(quote_mailbox(name))
    if status != "OK":
        raise ApplyError("创建目录失败: %s" % name)
    try:
        connection.subscribe(quote_mailbox(name))
    except Exception:  # 订阅失败不影响分拣本身
        pass
    existing.add(name)


def apply_plan(connection, actions: List[Action], mode: str = "copy", dry_run: bool = True,
               mailbox: str = "INBOX") -> List[dict]:
    """仅对与计划具有相同 UIDVALIDITY 的源目录执行操作。"""

    if mode not in SAFE_MODES:
        raise ApplyError("mode 只能是 copy 或 move")
    status, _ = connection.select(quote_mailbox(encode_mailbox(mailbox)), readonly=dry_run)
    if status != "OK":
        raise ApplyError("选择源目录失败: %s" % mailbox)
    _, validity_data = connection.response("UIDVALIDITY") or (None, None)
    current_validity = next(
        (value.decode("ascii") for value in validity_data or [] if isinstance(value, bytes)), ""
    )
    expected = {action.source_id.split(":")[1] for action in actions}
    if not current_validity.isdecimal() or current_validity == "0" or expected != {current_validity}:
        raise ApplyError("UIDVALIDITY 缺失或变化；未执行写入，请重新分类")
    existing = _folders(connection)
    results = []
    for action in actions:
        record = {
            "uid": action.uid,
            "category": action.category,
            "target": action.target,
            "subject": action.subject_label,
            "mode": mode,
            "dry_run": dry_run,
        }
        try:
            wire_name = encode_mailbox(action.target)
            _ensure_folder(connection, wire_name, existing, dry_run)
            if dry_run:
                record["status"] = "planned"
            else:
                command = "move" if mode == "move" else "copy"
                status, _ = connection.uid(command, action.uid, quote_mailbox(wire_name))
                record["status"] = "applied" if status == "OK" else "failed"
                if status != "OK":
                    record["error"] = "IMAP %s 返回 %s" % (command, status)
        except Exception as error:  # 单封失败不中断其余邮件
            record["status"] = "failed"
            record["error"] = str(error)
        results.append(record)
    return results


def write_log(run_dir, results: List[dict]) -> Path:
    path = Path(run_dir) / "apply.jsonl"
    with open(path, "w", encoding="utf-8") as handle:
        for item in results:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return path


def connect(creds: dict):
    import imaplib

    if creds.get("ssl", True):
        return imaplib.IMAP4_SSL(creds["host"], creds["port"])
    return imaplib.IMAP4(creds["host"], creds["port"])


def run_apply(
    run_dir,
    config,
    mailbox: str = "INBOX",
    limit: int = 20,
    mode: str = "copy",
    folder_prefix: str = "分拣",
    dry_run: bool = True,
    credentials: Optional[dict] = None,
    connection_factory=None,
) -> dict:
    """端到端：重取邮件 → 匹配分类 → 建目录 → 复制/移动。"""

    from .imap_source import credentials_from_env, fetch_recent

    creds = credentials or credentials_from_env()
    records = fetch_recent(
        host=creds["host"],
        port=creds["port"],
        user=creds["user"],
        password=creds["password"],
        use_ssl=creds["ssl"],
        mailbox=mailbox,
        limit=limit,
        max_body_bytes=config.budget.max_body_bytes_per_message,
    )
    actions = export_plan(run_dir, records, folder_prefix)
    factory = connection_factory or connect
    connection = factory(creds)
    try:
        connection.login(creds["user"], creds["password"])
        results = apply_plan(connection, actions, mode=mode, dry_run=dry_run, mailbox=mailbox)
    finally:
        try:
            connection.logout()
        except Exception:
            pass
    log_path = write_log(run_dir, results)
    return {
        "matched": len(actions),
        "applied": sum(1 for r in results if r["status"] == "applied"),
        "planned": sum(1 for r in results if r["status"] == "planned"),
        "failed": [r for r in results if r["status"] == "failed"],
        "log": str(log_path),
        "mode": mode,
        "dry_run": dry_run,
    }
