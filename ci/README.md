# 可选 GitHub Actions 模板

`github-actions-tests.yml` 提供 Python 3.9 / 3.12 测试矩阵，但当前不在 `.github/workflows/` 下，**不会自动运行**。

首次发布时，GitHub OAuth 凭据缺少 `workflow` scope，拒绝上传 workflow；发布过程没有扩大账号权限。因此将模板作为普通文件保留，代码发布不依赖启用 CI。

需要启用时，由仓库维护者使用具备相应权限的凭据或 GitHub 网页，将该文件放入 `.github/workflows/tests.yml` 后提交即可。该步骤不是安装或使用本工具的前提。

本地检查：

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

本次实际验证为本地 Python 3.9 下 67 项通过；未运行该 GitHub Actions 矩阵，不能宣称远端 CI 已通过。
