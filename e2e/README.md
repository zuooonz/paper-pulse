# E2E、压测与队列故障演练

本目录提供真实 HTTP/浏览器端到端流程、混合读写压测、页面级走查，以及数据库任务队列的进程崩溃演练。E2E 测试默认跳过；只有同时设置 `E2E_BASE_URL` 和 `E2E_API_URL` 才会访问服务。

## 准备

请使用专用测试数据库。脚本会创建测试论文、草稿、账号和队列任务；检索导入步骤可能向测试库写入少量外部论文。支持 PostgreSQL `DATABASE_URL`，本地也可使用 SQLite URL。不要指向生产数据库。

安装 Python 依赖和 Playwright Chromium：

```bash
python -m pip install pytest httpx playwright PyMuPDF
python -m playwright install chromium
```

前端默认由 `npm run start` 启动，因此先在 `frontend/` 执行 `npm ci` 和 `npm run build`。如要使用开发服务器：

```bash
E2E_FRONTEND_MODE=dev ./e2e/run_stack.sh start
```

## 启动与停止

```bash
export DATABASE_URL='<专用测试数据库的 SQLAlchemy URL>'
export E2E_LOG_DIR=/tmp/paper-pulse-e2e-logs
./e2e/run_stack.sh start
```

`run_stack.sh` 启动 mock LLM、两个 Uvicorn worker、两个队列 worker、调度器和 Next.js 前端。服务日志、PID、浏览器截图和 JSON 报告保存在 `E2E_LOG_DIR`。默认端口为 API `8000`、前端 `3000`、mock LLM `8011`，可通过 `E2E_API_PORT`、`E2E_FRONTEND_PORT`、`E2E_LLM_PORT` 修改。

前端凭证登录通过 `BACKEND_API_URL` 访问 API；脚本默认将它指向本地 API。API 和前端地址可通过 `E2E_API_URL`、`E2E_BASE_URL` 指定。若更改 API 端口，构建前端时也要设置相同的 `BACKEND_API_URL`，让 Next.js rewrites 使用正确地址。

脚本使用四个仅供本地测试的角色账号，并为这些临时账号启用明文密码兼容。缺少 `JWT_SECRET`、`AUTH_SECRET` 和 `NEXTAUTH_SECRET` 时会使用 E2E 测试值。该测试环境不得用于生产。未设置 `CREDENTIALS_ENCRYPTION_KEY` 时，脚本生成仅用于测试的 Fernet 密钥，并以权限 `0600` 保存在 `$E2E_LOG_DIR/.e2e-credentials-fernet-key`，供同一日志目录下重启服务时复用。不要将测试值用于生产。

## 运行端到端流程

```bash
E2E_MOCK_PUBLISHER=1 \
E2E_BASE_URL=http://localhost:3000 \
E2E_API_URL=http://127.0.0.1:8000 \
E2E_LOG_DIR=/tmp/paper-pulse-e2e-logs \
E2E_REPORT=/tmp/paper-pulse-e2e-logs/e2e-report.json \
PYTHONPATH=e2e:src python -m pytest -q e2e/test_e2e_flows.py
```

测试运行时通过 PyMuPDF 生成两页虚构论文 PDF，含摘要、方法、合成实体和图片。覆盖四种角色登录、关键 API 权限、PDF 文本提取、实体识别和领域标记、审核员审核论文（查看者无权审核）、单篇与批量草稿生成、草稿审核、作者/统计/设置页面截图、钉钉关闭状态及期刊匹配。

领域检索调用 `scripts/search_domain_papers.py` 的 PubMed 数据源，对最近两个自然年的每年设置少量返回上限，分别执行预览和自动入库；若有 DOI 候选，再通过论文 DOI 导入 API 触发 Crossref 补全。外部网络或文献服务失败会记录在报告的 `external_dependency_failures` 中，不会单独导致 E2E 失败。只验证本地流程时可设置 `E2E_SKIP_EXTERNAL_SEARCH=1`。

浏览器截图位于 `$E2E_LOG_DIR/screens/`。浏览器 console error、页面异常、失败请求（权限测试预期的 HTTP 401/403 除外）和步骤结果写入 `$E2E_REPORT`。`run_stack.sh` 设置 `E2E_MOCK_PUBLISHER=1`，由 `e2e/sitecustomize.py` 仅在 E2E 进程中替换测试账号的 CSDN 发布器。E2E 通过账号 API 创建 `e2e-` 前缀账号，使用测试凭据，经 `publish_draft` job 发布并检查 `$E2E_LOG_DIR/published/` 中的输出文件；随后还会安排定时发布并检查第二个输出文件。

## 页面级走查

`walkthrough.py` 是独立 Playwright 脚本，会从 `frontend/src/app/` 发现应用页面，逐角色登录并检查按钮、链接、Tab 和下拉控件。脚本默认 headless，记录浏览器错误、页面异常、HTTP 错误、错误提示和权限不一致，并为问题保存截图。删除、清空、退出登录、发布、取消发布和重置操作会跳过；浏览器确认框会自动取消，应用内对话框会尝试取消或关闭。

先设置角色密码 JSON。`limited` 使用受限用户账号：

```bash
export E2E_USERS='{"admin":"<管理员测试密码>","reviewer":"<审核员测试密码>","viewer":"<查看者测试密码>","limited":"<受限用户测试密码>"}'
python -m pip install playwright
python -m playwright install chromium
python e2e/walkthrough.py \
  --base-url http://localhost:3000 \
  --api-url http://127.0.0.1:8000 \
  --report /tmp/paper-pulse-e2e-logs/walkthrough.json \
  --screens /tmp/paper-pulse-e2e-logs/walkthrough-screens
```

`--expect-empty` 会额外检查论文、作者、工作台和视频页面的空状态提示、页面错误与白屏：

```bash
python e2e/walkthrough.py \
  --base-url http://localhost:3000 \
  --api-url http://127.0.0.1:8000 \
  --report /tmp/paper-pulse-e2e-logs/walkthrough-empty.json \
  --screens /tmp/paper-pulse-e2e-logs/walkthrough-empty-screens \
  --expect-empty
```

走查还会检查论文分页、实体与四维生命周期快捷筛选的组合、论文库和作者库搜索、临时修改并恢复论文字段，以及模拟论文列表接口错误。快捷筛选覆盖待处理、待审核、失败、已审核、已排除、无 PDF 和元数据不全；若页面没有某种筛选或搜索控件，报告会说明跳过原因。问题分类汇总和截图路径写入报告；加 `--headed` 可显示浏览器窗口。

### 纵向布局检查

`layout_check.py` 使用无头 Chromium 检查登录、论文库、论文详情对话框、选中草稿后的工作台、作者库、统计、设置和视频库。每个页面状态会在 1440×900、1280×720、768×1024、390×844、360×640 视口下截图，并检查整页溢出、底栏位置和嵌套纵向滚动容器。

密码可通过参数传入，也可从 `E2E_USERS` 的 `admin` 项读取：

```bash
python e2e/layout_check.py \
  --base-url http://localhost:3000 \
  --api-url http://127.0.0.1:8000 \
  --user admin \
  --password '<管理员测试密码>' \
  --out /tmp/paper-pulse-e2e-logs/layout
```

也可省略 `--user` 和 `--password`，脚本会读取 `E2E_USERS` 中的 `admin` 密码。截图按视口保存在输出目录下，`layout-report.json` 包含所有检查结果和失败列表；存在失败时退出码为非零。需要安装 Python `playwright` 包并运行 `python -m playwright install chromium`。

## 压测

```bash
E2E_API_URL=http://127.0.0.1:8000 \
E2E_LOG_DIR=/tmp/paper-pulse-e2e-logs \
python e2e/load_test.py --concurrency 30 --duration 180
```

`--concurrency` 接受 `20` 到 `50`，默认 `30`；`--duration` 默认 `180` 秒。请求包括论文列表、JSON 字段筛选、统计、作者地理数据、论文字段更新、草稿创建和更新，以及通过 E2E no-op worker 入队的任务。压测创建的草稿和临时论文会在结束时清理，已有论文的测试字段会恢复。JSON 报告包含分位延迟、平均延迟、错误率、操作与状态码计数，默认写入 `$E2E_LOG_DIR/load-test.json`。

## 队列故障演练

```bash
E2E_LOG_DIR=/tmp/paper-pulse-e2e-logs \
python e2e/queue_chaos.py --jobs 60 --lease-timeout 5 --timeout 180
```

脚本向 `e2e_noop` 类型队列入队，启动三个专用 worker，并在某个 worker 持有运行中任务后对其执行 `SIGKILL`。其余 worker 会在租约超时后回收任务。handler 按任务 ID 去重，最后验证每个任务均成功且完成记录无重复、缺失或额外 ID。报告默认写入 `$E2E_LOG_DIR/queue-chaos.json`。

## 停止

```bash
./e2e/run_stack.sh stop
```
