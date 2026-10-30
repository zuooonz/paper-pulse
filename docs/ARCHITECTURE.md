# 架构说明

Paper Pulse 分为网页应用、API、后台 worker/调度器和共享存储四层。

## 请求路径

1. 浏览器访问 Next.js 页面。认证由 NextAuth 的 httpOnly 会话完成；页面通过 `apiClient` 携带短期 API JWT 调用后端。
2. FastAPI 在依赖层解析 JWT、校验角色和论文可见性，再进入业务路由。
3. 长任务（文献检索、PDF 下载/处理、草稿生成、发布、Word 汇总）写入 `jobs` 表，由 worker 消费。
4. 调度器通过 `scheduler_leases` 选主，生成定时发布、检索、PDF 标记核对和过期清理任务。

## 数据与文件

- 默认 SQLite 位于 `data/app.db`；多进程部署使用 PostgreSQL。
- PDF、图片、导出文件和生成内容存放在 `STORAGE_ROOT`、`PDF_ROOT`、`OUTPUT_ROOT` 指向的共享卷。
- 论文生命周期拆成 `processing_status`、`review_outcome`、`has_pdf`、`metadata_complete` 四个独立字段，避免把处理失败、缺 PDF 和审核结论混成一个状态。
- 配额预留/确认/释放使用事务；SQLite 使用 `BEGIN IMMEDIATE`，PostgreSQL 使用 advisory lock。

## 前端演示模式

`frontend/demo/` 是 GitHub Pages 的静态入口。Vite 用别名把 `next/navigation`、`next-auth/react`、`next/image` 等 Next 运行时替换成 demo stub，把 `@/lib/api` 替换成读取 `frontend/demo/fixtures/` 的只读 mock，然后直接渲染真实的 `PapersWorkspace` 组件和真实样式。这样 Pages 上的页面不是手写复制品，但仍然不需要后端。

生产构建仍使用 `frontend/next.config.ts` 的 standalone 输出；静态演示只在 `npm run demo:build` 时启用。

## 迁移与测试

Alembic 迁移位于 `migrations/`。后端测试使用隔离 SQLite/PostgreSQL schema；CI 同时运行 SQLite 和 PostgreSQL 两套测试，并执行前端 lint/test/build 和凭证扫描。
