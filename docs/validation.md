# 验证记录与结论边界

## 来源试验（2026-10-30）

以下记录一次真实校园邮件分类、目录整理和 Skill / MCP 接入试验的聚合结果，不公开邮件内容、账号、原始日志或私人服务器配置。

- 使用 Qwen/Strata 的 OpenAI 兼容服务完成真实邮件分类；更换策略可以改变分类规则。
- 一轮 14 封：14 封返回可解析分类，类别分布为账号访问 9、校园 IT 3、其他 2。
- copy 写入对应的三个服务器目录，Thunderbird 界面计数一致；原件留在收件箱。
- stdio MCP 握手、工具发现和真实邮件分类调用已执行；独立 host 会话可发现 Skill 和 MCP 工具。

这些是历史试验结果，不保证其他环境有相同效果。14 封都返回分类不等于准确率 100%；没有公开人工标注集，不能据此声明分类准确率。真实邮箱只执行过 copy，未执行 move。

## 仓库验证

仓库包含合成 JSONL、可独立运行的协议与行为测试，以及尚未启用的 [GitHub Actions 模板](../ci/README.md)。以下命令无需邮箱凭据或模型服务：

```bash
python -m unittest discover -s tests -v
email-triage classify --config examples/config.example.json \
  --input examples/emails.example.jsonl --client mock
```

测试覆盖解析、预算、IMAP 协议、模拟 OpenAI HTTP 端点和 stdio MCP 等路径。模拟端点用于验证客户端契约，不作为真实模型能力证据。

2026-10-30，在独立 Python 3.9 虚拟环境安装本仓库后：

- 67 项 unittest 通过，不依赖私人配置或实验目录。
- 实际 CLI 对本地 IMAP/HTTP 模拟服务运行分类、dry-run、copy：目标增加 1 封，源目录保留原件，INBOX 中相同 UID 的另一封邮件未被操作。
- 实际 stdio MCP 进程完成握手、三工具发现与 `classify_imap` 调用，产物路径正确。
- 这次仓库验证未连接真实邮箱、未读取真实邮件、未使用真实模型；真实试验结果见上节。

## 仓库不包含的数据

真实邮件数据、账号与凭据、模型权重、私有服务器脚本、私人交流记录和来源项目的 Git 历史均不包含在仓库中。公开样例使用保留的 example 域名，是合成数据。
