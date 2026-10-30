# 前端

网页客户端使用 Next.js、React 和 TypeScript 构建。项目概览与后端配置见[仓库说明](../README.md)。

## 本地运行

使用 Node.js 22 和 npm。在仓库根目录执行：

```bash
cp frontend/.env.example frontend/.env.local
cd frontend
npm ci
npm run dev
```

打开 `http://localhost:3000`。请另行启动 API；开发环境默认后端地址为 `http://127.0.0.1:8000`。

## 环境变量

| 变量 | 用途 |
| --- | --- |
| `BACKEND_API_URL` | Next.js 服务端路由和重写规则使用的后端来源地址。本地默认值为 `http://127.0.0.1:8000`。 |
| `NEXT_PUBLIC_API_URL` | 可选的浏览器端 API 基础地址。留空时使用同源 API 路径和 Next.js 重写规则。 |
| `NEXT_PUBLIC_TARGET_LABEL` | 可选的应用显示名称。 |
| `NEXT_PUBLIC_DOMAIN_NAME` | 可选的领域显示名称。 |
| `NEXT_PUBLIC_APP_TIMEZONE` | 页面日期显示时区，应与后端 `APP_TIMEZONE` 一致。 |
| `AUTH_SECRET`、`NEXTAUTH_SECRET` | NextAuth 密钥；本地设置随机且私密的值。 |
| `NEXTAUTH_URL` | NextAuth 使用的前端来源地址。 |
| `AUTH_TRUST_HOST` | NextAuth 部署时是否信任转发主机；按部署拓扑设置。 |

请妥善保管 `.env.local`，不要提交到版本控制。密钥处理说明见根目录的 [SECURITY.md](../SECURITY.md)。

## 检查命令

在 `frontend/` 目录运行：

```bash
npm run lint
npx tsc --noEmit
npm test
npm run build
```

`npm test` 运行存储键、日期/时区和安全回归的 Node 测试。TypeScript 检查通过 `tsc --noEmit` 执行；目前没有单独的 npm 类型检查脚本。

## GitHub Pages 静态演示

`demo/` 使用真实页面组件和 `fixtures/` 中的只读 API 响应构建静态演示：

```bash
npm run demo:build
```

输出在 `demo-dist/`。GitHub Pages 工作流会以 `/paper-pulse/` 为 base path 构建并部署；不需要 FastAPI、数据库或 NextAuth。写操作会被禁用并显示只读提示。
