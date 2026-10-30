# Changelog

## 0.2.0 — standalone public release

- 从已有邮件分类子项目提取独立发布副本，不导入原项目 Git 历史、真实邮件、运行日志或个人环境。
- 包含 prompt 分类 CLI、只读 IMAP/JSONL 源、OpenAI 兼容模型接口、stdio MCP server、Skill。
- 保留可选显式 CLI 目录整理、review/score 核对工具。
- 补充独立安装、Thunderbird 核对、安全限制与公开验证说明。
- 发布检查中修正非 INBOX 源目录选择、写连接 UIDVALIDITY 复核、目录名转义及 literal `&` 编码；apply 主题使用脱敏函数。
- 测试改用仓库内合成样例和通用配置，不依赖原私有项目目录。
