"""命令行入口。

用法：
  PYTHONPATH=src python3 -m email_triage classify
      --config examples/config.example.json
      --input examples/emails.example.jsonl --client mock
"""

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .audit import AuditLog
from .client import MockClient, OpenAICompatClient
from .config import ConfigError, load_config
from .models import Category
from .prompt import build_messages
from .runner import Runner, summary_as_json
from .sources import SourceError, read_jsonl

DEFAULT_RUN_ROOT = Path(os.environ.get("EMAIL_TRIAGE_RUN_ROOT") or Path.cwd() / "runs")


def _run_id(mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    return "%s_email-triage-%s" % (stamp, mode)


def _build_client(config, mode: str):
    if mode == "mock":
        return MockClient(config.category_names())
    api_key = None
    if config.model.api_key_env:
        api_key = os.environ.get(config.model.api_key_env, "")
        if not api_key:
            raise ConfigError(
                "配置要求从环境变量 %s 读取 API key，但该变量为空" % config.model.api_key_env
            )
    return OpenAICompatClient(
        base_url=config.model.base_url,
        model=config.model.model,
        temperature=config.model.temperature,
        reasoning_effort=config.model.reasoning_effort,
        use_env_proxy=config.model.use_env_proxy,
        api_key=api_key,
    )


def _apply_policy(config, policy_path):
    """用外部策略文件覆盖 task_instruction 与 categories。"""

    if not policy_path:
        return config
    raw = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    instruction = str(raw.get("task_instruction", "")).strip()
    categories_raw = raw.get("categories")
    if not instruction:
        raise ConfigError("策略文件缺少 task_instruction")
    if not isinstance(categories_raw, list) or not categories_raw:
        raise ConfigError("策略文件缺少 categories")
    categories = []
    for item in categories_raw:
        name = str((item or {}).get("name", "")).strip()
        if not name:
            raise ConfigError("策略文件的 category 缺少 name")
        categories.append(Category(name=name, description=str(item.get("description", "")).strip()))
    names = [c.name for c in categories]
    if len(set(names)) != len(names):
        raise ConfigError("策略文件里存在重复类别")
    return replace(config, categories=categories, task_instruction=instruction)


def _print_table(summary) -> None:
    rows = [("subject", "status", "category", "latency_s")]
    for outcome in summary.outcomes:
        rows.append(
            (
                outcome.subject_label[:44],
                outcome.status,
                outcome.category or (outcome.failure or ""),
                "%.2f" % outcome.latency_s,
            )
        )
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    for index, row in enumerate(rows):
        line = "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
        print(line)
        if index == 0:
            print("  ".join("-" * widths[i] for i in range(len(widths))))


def _load_records(args, config):
    if args.source == "imap":
        from .imap_source import credentials_from_env, fetch_recent

        creds = credentials_from_env()
        return fetch_recent(
            host=creds["host"],
            port=creds["port"],
            user=creds["user"],
            password=creds["password"],
            use_ssl=creds["ssl"],
            mailbox=args.mailbox,
            limit=args.limit or 5,
            max_body_bytes=config.budget.max_body_bytes_per_message,
        )
    return read_jsonl(args.input, config.budget.max_body_bytes_per_message, args.limit)


def command_classify(args) -> int:
    if args.source == "jsonl" and not args.input:
        print("--source jsonl 需要 --input 指定样本文件", file=sys.stderr)
        return 1
    try:
        config = load_config(args.config)
        config = _apply_policy(config, getattr(args, "policy", None))
    except (OSError, json.JSONDecodeError, ConfigError) as error:
        print("配置错误: %s" % error, file=sys.stderr)
        return 1
    try:
        records = _load_records(args, config)
    except (OSError, SourceError) as error:
        print("输入错误: %s" % error, file=sys.stderr)
        return 1

    if args.dry_run:
        total = 0
        for record in records:
            messages = build_messages(config, record)
            size = sum(len(m["content"].encode("utf-8")) for m in messages)
            total += size
            print("%s  %6d bytes  truncated=%s" % (record.source_id, size, record.truncated))
        print("样本 %d 封，总输入 %d 字节；未调用模型" % (len(records), total))
        return 0

    run_id = args.run_id or _run_id(args.client)
    run_dir = Path(args.run_dir) if args.run_dir else DEFAULT_RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        client = _build_client(config, args.client)
    except ConfigError as error:
        print("配置错误: %s" % error, file=sys.stderr)
        return 1
    with AuditLog(run_dir / "audit.jsonl") as audit:
        runner = Runner(config, client, audit_sink=audit.write)
        summary = runner.run(records, run_id=run_id)

    (run_dir / "summary.json").write_text(summary_as_json(summary) + "\n", encoding="utf-8")
    print("run: %s" % run_dir)
    _print_table(summary)
    counts = Counter(o.category for o in summary.outcomes if o.status == "classified")
    if counts:
        print("分布: %s" % ", ".join("%s=%d" % item for item in sorted(counts.items())))
    if summary.aborted:
        print("中止: %s" % summary.abort_reason, file=sys.stderr)
        return 3
    if summary.failed:
        print("失败: %d 封" % summary.failed, file=sys.stderr)
        return 2
    return 0


def _read_outcomes(run_dir: Path):
    data = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    return data["outcomes"]


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def command_review(args) -> int:
    run_dir = Path(args.run_dir)
    try:
        outcomes = _read_outcomes(run_dir)
    except (OSError, KeyError, ValueError) as error:
        print("读取运行结果失败: %s" % error, file=sys.stderr)
        return 1
    lines = [
        "# 人工核对表",
        "",
        "在最后一列填正确类别；认可模型判断就填 `✓`。填完运行 `email-triage score --run-dir <dir>`。",
        "",
        "| # | 主题 | 建议类别 | 模型理由 | 你的判断 |",
        "|---|---|---|---|---|",
    ]
    for index, outcome in enumerate(outcomes, 1):
        lines.append(
            "| %d | %s | `%s` | %s | |"
            % (
                index,
                _cell(outcome.get("subject_label", "")),
                outcome.get("category") or outcome.get("failure") or "-",
                _cell((outcome.get("reason") or "")[:80]),
            )
        )
    path = run_dir / "review.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(path)
    return 0


def _split_row(line: str):
    body = line.strip().strip("|")
    parts, buffer = [], ""
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body) and body[index + 1] == "|":
            buffer += "|"
            index += 2
            continue
        if char == "|":
            parts.append(buffer.strip())
            buffer = ""
            index += 1
            continue
        buffer += char
        index += 1
    parts.append(buffer.strip())
    return parts


_AGREE = {"✓", "√", "ok", "okay", "对", "正确", "yes", "1"}


def command_score(args) -> int:
    run_dir = Path(args.run_dir)
    try:
        outcomes = _read_outcomes(run_dir)
    except (OSError, KeyError, ValueError) as error:
        print("读取运行结果失败: %s" % error, file=sys.stderr)
        return 1
    review_path = Path(args.review) if args.review else run_dir / "review.md"
    try:
        text = review_path.read_text(encoding="utf-8")
    except OSError as error:
        print("读取核对表失败: %s（先运行 email-triage review --run-dir <dir>）" % error, file=sys.stderr)
        return 1

    rows = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        rows.append(cells)

    if not rows:
        print("核对表里没有可评分的行", file=sys.stderr)
        return 1

    labeled = correct = 0
    mismatches = []
    for cells in rows:
        index = int(cells[0]) - 1
        if index < 0 or index >= len(outcomes):
            continue
        verdict = cells[4].strip().strip("`").strip()
        if not verdict:
            continue
        labeled += 1
        predicted = outcomes[index].get("category") or outcomes[index].get("failure")
        if verdict.lower() in _AGREE:
            correct += 1
            continue
        if verdict == predicted:
            correct += 1
        else:
            mismatches.append((index + 1, outcomes[index].get("subject_label", ""), predicted, verdict))

    if not labeled:
        print("还没有填写任何判断", file=sys.stderr)
        return 1

    print("已标注 %d / %d 封；一致 %d 封；准确率 %.1f%%"
          % (labeled, len(outcomes), correct, 100.0 * correct / labeled))
    if mismatches:
        print("\n不一致：")
        for index, subject, predicted, verdict in mismatches:
            print("  #%d %s\n     模型=%s  你的=%s" % (index, subject[:50], predicted, verdict))
    return 0


def command_apply(args) -> int:
    from .apply import ApplyError, run_apply

    try:
        config = load_config(args.config)
    except (OSError, json.JSONDecodeError, ConfigError) as error:
        print("配置错误: %s" % error, file=sys.stderr)
        return 1
    try:
        result = run_apply(
            run_dir=Path(args.run_dir),
            config=config,
            mailbox=args.mailbox,
            limit=args.limit,
            mode=args.mode,
            folder_prefix=args.folder_prefix,
            dry_run=not args.apply,
        )
    except (ApplyError, SourceError, OSError) as error:
        print("无法执行: %s" % error, file=sys.stderr)
        return 1
    print(
        "匹配 %d 封；%s %d 封%s"
        % (
            result["matched"],
            "预演（未写入）" if result["dry_run"] else "已写入",
            result["planned"] if result["dry_run"] else result["applied"],
            "" if not result["failed"] else "；失败 %d 封" % len(result["failed"]),
        )
    )
    for item in result["failed"]:
        print("  失败 uid=%s %s" % (item["uid"], item.get("error", "")), file=sys.stderr)
    print("日志: %s" % result["log"])
    if not args.apply:
        print("确认无误后加 --apply 真正写入；安全模式 --mode copy 会保留原邮件")
    return 0 if not result["failed"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="email_triage", description="只读邮件分类试验")
    sub = parser.add_subparsers(dest="command", required=True)

    classify = sub.add_parser("classify", help="对少量邮件做一次只读分类")
    classify.add_argument("--config", required=True, help="分类规则与预算配置")
    classify.add_argument("--policy", default=None,
                          help="可选：JSON 策略文件，覆盖 task_instruction 与 categories")
    classify.add_argument("--source", choices=("jsonl", "imap"), default="jsonl",
                          help="jsonl=本地样本文件；imap=直接只读拉取（凭据仅从环境变量读取）")
    classify.add_argument("--input", default=None, help="--source jsonl 时的样本文件（含正文，勿入库）")
    classify.add_argument("--mailbox", default="INBOX", help="--source imap 时的目录名")
    classify.add_argument("--client", choices=("mock", "openai", "strata"), default="mock",
                          help="mock=离线关键词；openai=任意 OpenAI 兼容端点；strata 是 openai 的别名")
    classify.add_argument("--limit", type=int, default=None, help="最多处理多少封")
    classify.add_argument("--run-dir", default=None, help="产物目录，默认 email-triage/runs/<run-id>")
    classify.add_argument("--run-id", default=None)
    classify.add_argument("--dry-run", action="store_true", help="只计算输入规模，不调用模型")
    classify.set_defaults(func=command_classify)

    review = sub.add_parser("review", help="为一次运行生成人工核对表 review.md")
    review.add_argument("--run-dir", required=True, help="包含 summary.json 的运行目录")
    review.set_defaults(func=command_review)

    score = sub.add_parser("score", help="按填好的核对表算准确率")
    score.add_argument("--run-dir", required=True)
    score.add_argument("--review", default=None, help="核对表路径，默认 <run-dir>/review.md")
    score.set_defaults(func=command_score)

    apply_cmd = sub.add_parser(
        "apply",
        help="把一次运行的结果落到邮箱目录（默认只预演；copy 模式保留原邮件）",
    )
    apply_cmd.add_argument("--run-dir", required=True, help="包含 summary.json 的运行目录")
    apply_cmd.add_argument("--config", required=True, help="与 classify 相同的配置（决定正文截断口径）")
    apply_cmd.add_argument("--mailbox", default="INBOX")
    apply_cmd.add_argument("--limit", type=int, default=20, help="重新取多少封来匹配（要覆盖运行时的范围）")
    apply_cmd.add_argument("--mode", choices=("copy", "move"), default="copy",
                           help="copy=复制到分类目录并保留原邮件；move=搬走")
    apply_cmd.add_argument("--folder-prefix", default="分拣", help="分类目录名前缀，如 `分拣` → `分拣-账号访问`")
    apply_cmd.add_argument("--apply", action="store_true", help="真正写入；不加则只预演")
    apply_cmd.set_defaults(func=command_apply)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
