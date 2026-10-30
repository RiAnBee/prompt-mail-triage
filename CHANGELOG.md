# Changelog

## 0.2.0 — standalone public release

- 从已有邮件分类子项目提取独立发布副本，不导入原项目 Git 历史、真实邮件、运行日志或个人环境。
- 包含 prompt 分类 CLI、只读 IMAP/JSONL 源、OpenAI 兼容模型接口、stdio MCP server、Skill。
- 保留可选显式 CLI 目录整理、review/score 核对工具。
- 补充独立安装、Thunderbird 核对、安全限制与公开验证说明。
- 发布检查中修正非 INBOX 源目录选择、写连接 UIDVALIDITY 复核、目录名转义及 literal `&` 编码；apply 主题使用脱敏函数。
- 测试改用仓库内合成样例和通用配置，不依赖原私有项目目录。
- README 默认中文，提供完整英文版和双向语言入口；补充安装、改规则、接模型/邮箱、目录整理、Skill 与 MCP 的逐步指导。
- 按 renhua 保义整理中文文档，区分 CLI 与 MCP 的实际产物及可选功能。
- 调整中英文 README 的项目定位：突出给定 prompt、Qwen/Strata 真实邮箱分拣及 Skill + MCP 复用；说明 Thunderbird 的核对作用和南大邮箱的验证范围，保留现有详细教程。
