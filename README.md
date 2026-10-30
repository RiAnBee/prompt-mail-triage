# prompt-mail-triage

Prompt-driven email sorting with Qwen/Strata, Thunderbird, Skill and MCP.

用你给定的 prompt 分类邮件；通过 IMAP 读取，用 OpenAI 兼容模型判断，在 Thunderbird 中核对。提供 Python CLI、只读 MCP server 和可复用 Skill。Qwen/Strata 是已试用的模型服务组合，不是必装依赖；本项目不包含模型权重或 Strata 本身。

- **分类默认只读**：`EXAMINE` + `BODY.PEEK`，不设置已读。
- **规则来自配置**：`task_instruction` + `categories`，可用 `--policy` 更换。
- **MCP 只有三个只读工具**：模型无法通过本 server 移动、发送或删除邮件。
- **可选目录整理**：独立 CLI `apply`，默认预演，显式 `--apply` 后才写邮箱。

仓库名是 `prompt-mail-triage`；Python 包和命令仍叫 `email-triage`，导入名为 `email_triage`。

## 安装

Python ≥ 3.9；运行时仅依赖标准库。

```bash
git clone https://github.com/RiAnBee/prompt-mail-triage.git
cd prompt-mail-triage
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools
python -m pip install -e .
```

## 先跑合成样例

```bash
email-triage classify --config examples/config.example.json \
  --input examples/emails.example.jsonl --client mock
```

`mock` 只验证本地编排，不代表模型能力。仓库中的邮件样例全部是合成数据，不含真实邮箱导出。

## 接入 Qwen/Strata 或其他模型

启动你自己的 OpenAI 兼容模型服务，然后设置**真实服务地址和模型 ID**：

```bash
export EMAIL_TRIAGE_BASE_URL=http://127.0.0.1:8000/v1
export EMAIL_TRIAGE_MODEL=your-model-id
email-triage classify --config examples/config.example.json \
  --input examples/emails.example.jsonl --client openai
```

端点必须支持 `/v1/chat/completions`。`--client strata` 是 `openai` 的兼容别名，不会自动安装、启动或发现 Strata。模型 API key 通过配置中的 `model.api_key_env` 指定环境变量名，不把密钥写进 JSON。

**邮件内容会发送给配置的模型服务。使用真实邮箱前，确认该端点的归属、访问权限和数据处理方式。**

## 读取真实邮箱并更换 prompt

```bash
export EMAIL_IMAP_HOST=imap.example.com
export EMAIL_IMAP_USER=you@example.com
export EMAIL_IMAP_PASSWORD_FILE=/absolute/private/path/imap-password
# 密码文件由你在本机创建；chmod 600 /absolute/private/path/imap-password

email-triage classify --config examples/config.example.json \
  --source imap --mailbox INBOX --limit 5 --client openai

email-triage classify --config examples/config.example.json \
  --policy examples/policy.reply-today.json \
  --source imap --mailbox INBOX --limit 5 --client openai
```

`EMAIL_IMAP_PORT` 默认 993，`EMAIL_IMAP_SSL` 默认 1。也支持 `EMAIL_IMAP_PASSWORD` 或 macOS 钥匙串 `EMAIL_IMAP_KEYCHAIN_ITEM`。具体认证方式由邮箱服务商决定；不是所有服务都允许普通 IMAP 密码登录。

`task_instruction` 写判断规则，`categories` 写允许返回的类别及说明。样例规则不是固定需求，可以直接替换。IMAP 正文目前只提取 `text/plain`，不读附件，不抓网页链接；纯 HTML 邮件的正文可能为空。

## Thunderbird 核对与可选整理

Thunderbird 是人工核对端，不是本项目插件。它与本工具连接同一个 IMAP 账号；本工具不读取或改写 Thunderbird profile 数据库。

分类结果在 `runs/<run-id>/summary.json`。要将结果显示成服务器上的分类目录：

```bash
# 与 classify 使用相同配置、邮箱和覆盖范围
email-triage apply --run-dir runs/<run-id> \
  --config examples/config.example.json --mailbox INBOX --limit 5

# 检查预演日志后才执行；默认 copy，收件箱原件保留
email-triage apply --run-dir runs/<run-id> \
  --config examples/config.example.json --mailbox INBOX --limit 5 --apply
```

默认生成 `分拣-<类别>` 顶级目录。Thunderbird 同步后可查看；同一封邮件在收件箱和分类目录出现，是复制模式的预期结果。

**不要重复执行同一批写入**：`apply` 目前不去重，重跑 `copy` 会产生副本；也不能在已复制后直接再跑 `move` 当作清理。`--mode move --apply` 会从源目录移走邮件，要求服务器支持 UID MOVE，没有 COPY+DELETE 回退；真实邮箱试验只验证过 copy。详见[运维说明](docs/operations.md)。

## Skill + MCP

Skill 正文：[skill/SKILL.md](skill/SKILL.md)。按 agent host 的技能发现规则安装，目录名使用 `email-triage`；无需安装个人配置或本项目原始实验环境。

```bash
export EMAIL_TRIAGE_CONFIG=/absolute/path/to/config.json
email-triage-mcp
```

MCP host 配置示例：

```json
{
  "mcpServers": {
    "email-triage": {
      "command": "/absolute/path/to/.venv/bin/email-triage-mcp",
      "env": {
        "EMAIL_TRIAGE_CONFIG": "/absolute/path/to/config.json",
        "EMAIL_TRIAGE_RUN_ROOT": "/absolute/path/to/runs",
        "EMAIL_IMAP_HOST": "imap.example.com",
        "EMAIL_IMAP_USER": "you@example.com",
        "EMAIL_IMAP_PASSWORD_FILE": "/absolute/private/path/imap-password"
      }
    }
  }
}
```

| 工具 | 作用 |
|---|---|
| `list_categories` | 当前规则与类别，不访问邮箱 |
| `classify_jsonl` | 分类本地 JSONL 样例 |
| `classify_imap` | 只读获取并分类最近 N 封邮件（最多 20） |

工具参数与协议见 [docs/mcp.md](docs/mcp.md)。CLI `apply` 不在 MCP 工具表中。

## 核对分类质量（可选）

```bash
email-triage review --run-dir runs/<run-id>
# 在 review.md 最后一列填 ✓ 或正确类别
email-triage score --run-dir runs/<run-id>
```

没有人工标注就没有准确率；合法 JSON 仅代表结构可用。这个核对工具不是开始使用的门槛。

## 安全与限制

- 邮件正文视为不可信数据；注入缓解不能保证模型一定判断正确。
- `summary.json`、审计和 `apply.jsonl` 仍可能包含主题、模型理由等敏感信息，不应公开上传。
- `classify_jsonl` 可以读取 server 进程可访问的本地文件，MCP host 必须可信。
- 提供的是一次性任务，不含后台监听、自动回复、附件解析或 Thunderbird 扩展。
- 不随仓库分发账号、密钥、真实邮件、模型权重或实测原始日志。

## 文档与开发

- [架构](docs/architecture.md) · [MCP](docs/mcp.md) · [运维与 Thunderbird](docs/operations.md)
- [安全边界](docs/security.md) · [验证记录与结论边界](docs/validation.md) · [变更记录](CHANGELOG.md)
- [可选 CI 模板](ci/README.md)：尚未启用 GitHub Actions；本次验证在本地完成。

```bash
python -m unittest discover -s tests -v
```

MIT License，见 [LICENSE](LICENSE)。
