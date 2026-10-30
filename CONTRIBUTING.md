# 贡献指南

欢迎提交问题修复、文档改进和功能变更。请保持改动聚焦，描述用户可见行为，并提供相关测试或复现步骤。

## 开发环境

要求：Python 3.11 或更新版本、Node.js 22 和 npm。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,postgres]"
cp .env.example .env
cp config/runtime.example.yaml config/runtime.yaml
cp frontend/.env.example frontend/.env.local
cd frontend
npm ci
```

启动 API 前，在本地配置 `JWT_SECRET` 和运行时用户的密码哈希。可运行 `PYTHONPATH=src python -m api.hash_password` 生成 PBKDF2 哈希。使用模型功能时再填写 `LLM_*` 变量。`config/runtime.yaml`、`.env` 和 `frontend/.env.local` 均只应保存在本地。

## 测试与检查

后端默认使用 SQLite 测试库。在仓库根目录运行：

```bash
PYTHONPATH=src python -m pytest -q -p no:cacheprovider
```

如需 PostgreSQL 覆盖，将 `TEST_DATABASE_URL` 设置为专用测试数据库的 SQLAlchemy URL，再运行相同命令。测试会在该数据库中建立并清理隔离 schema；不要指向生产或含有重要数据的数据库。

前端检查在 `frontend/` 目录运行：

```bash
npm run lint
npx tsc --noEmit
npm test
npm run build
```

`npm test` 运行前端存储模块的 Node 测试。CI 还会运行后端 Ruff 和 SQLite/PostgreSQL 测试，以及前端 lint、测试和生产构建。需要浏览器与真实服务进程的 E2E、页面走查、压测和队列演练见 [`e2e/README.md`](e2e/README.md)。

## 代码与提交规范

- 遵循相邻 Python、TypeScript 和 React 代码的风格；Python 检查使用 Ruff。本仓库没有独立格式化器。
- 优先采用小而清晰的变更。API、配置或用户工作流变化时，同步更新相关文档。
- 用户可见文案和注释沿用所在区域的语言风格；生成式模型输出必须保留人工审核步骤。
- 提交信息建议遵循 Conventional Commits，以类型和简短说明表达改动，例如 `docs: 更新部署说明`、`fix: 修复元数据补全`。

## 数据与密钥

- 不要提交密码、API 密钥、JWT 密钥、加密密钥、Cookie、本地 `.env`、`frontend/.env.local` 或 `config/runtime.yaml`。
- 不要提交私人论文集、下载的 PDF、生成文件、应用数据库、截图中泄露的私人内容或其他私有文件。
- 测试使用合成数据；不要依赖私人账号、Cookie 或在线服务凭据。
- `data/reference/` 中的期刊参考数据采用独立 GPL-3.0 许可。修改或再分发前请阅读 [`NOTICE.md`](data/reference/NOTICE.md)。

## 提交 PR 前检查

- [ ] 说明变更范围、用户可见行为及新增配置项。
- [ ] 运行 Ruff：`ruff check .`。
- [ ] 在 `frontend/` 运行 ESLint（`npm run lint`）、`npx tsc --noEmit`、`npm test` 和 `npm run build`。
- [ ] 运行 pytest SQLite 和 PostgreSQL 测试；PostgreSQL 使用指向专用测试库的 `TEST_DATABASE_URL`。
- [ ] 按变更影响运行相关 E2E 或页面走查，并记录结果。
- [ ] 检查差异中没有密钥、私人数据、生成文件、无关改动或失效的文档链接。

请只提交可复核的代码与文档，并确认没有包含密钥或私有文件。
