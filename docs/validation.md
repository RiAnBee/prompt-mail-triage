# 验证记录与结论边界

## 来源试验（2026-10-30）

本公开副本来自一次“按 prompt 分拣真实校园邮件，并封装 Skill + MCP”的试验。以下仅保留聚合事实，不公开真实邮件、账号、原始日志或私人服务器配置。

- 使用 Qwen/Strata 的 OpenAI 兼容服务完成真实邮件分类；更换策略可以改变分类规则。
- 一轮 14 封：14 封返回可解析分类，类别分布为账号访问 9、校园 IT 3、其他 2。
- copy 写入对应的三个服务器目录，Thunderbird 界面计数一致；原件留在收件箱。
- stdio MCP 握手、工具发现和真实邮件分类调用已执行；独立 host 会话可发现 Skill 和 MCP 工具。

这些是历史试验事实，不是任意环境下的效果保证。14/14 返回分类不是准确率 100%；没有公开人工标注集，不能据此声明分类准确率。真实邮箱中只执行过 copy，未执行 move。

## 公开副本验证

本仓库包含合成 JSONL、可独立运行的协议/行为测试，以及尚未启用的 [GitHub Actions 模板](../ci/README.md)。可在没有邮箱凭据、没有模型服务的环境运行：

```bash
python -m unittest discover -s tests -v
email-triage classify --config examples/config.example.json \
  --input examples/emails.example.jsonl --client mock
```

测试覆盖解析、预算、IMAP 协议、模拟 OpenAI HTTP 端点和 stdio MCP 等路径。模拟端点用于验证客户端契约，不作为真实模型能力证据。

2026-10-30，本公开副本在独立 Python 3.9 虚拟环境安装后：

- 67 项 unittest 通过，不依赖原项目配置或私人实验目录。
- 实际 CLI 对本地 IMAP/HTTP 模拟服务运行分类、dry-run、copy：目标增加 1 封，源目录保留原件，INBOX 中相同 UID 的另一封邮件未被操作。
- 实际 stdio MCP 进程完成握手、三工具发现与 `classify_imap` 调用，产物路径正确。
- 本轮发布验证不连接真实邮箱、不读取真实邮件、不使用真实模型；真实试验事实单独列于上节。

## 不包含

真实邮件数据、账号与凭据、模型权重、私有服务器脚本、原项目聊天记录、原项目 Git 历史。公开样例使用保留的 example 域名，是合成数据。
