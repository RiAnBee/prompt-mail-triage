# 可选 GitHub Actions 模板

`github-actions-tests.yml` 提供 Python 3.9 / 3.12 测试矩阵，但当前不在 `.github/workflows/` 下，**不会自动运行**。

首次发布时，GitHub OAuth 凭据缺少 `workflow` scope，上传 workflow 被拒绝；发布时没有扩大账号权限，因此模板保留为普通文件。

如需启用，仓库维护者可使用具备相应权限的凭据或 GitHub 网页，将该文件放入 `.github/workflows/tests.yml` 并提交。安装和使用本工具不依赖启用 CI。

本地检查：

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

此前本地 Python 3.9 验证为 67 项通过；GitHub Actions 矩阵尚未运行，不能据此声称远端 CI 已通过。
