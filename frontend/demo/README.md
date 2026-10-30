# GitHub Pages 静态演示

这个目录把真实的前端组件渲染成只读静态站点：

- `main.tsx`：挂载真实的 `PapersWorkspace` 和 `CollapsibleSidebar`；
- `stubs/`：替换 `next/navigation`、`next-auth/react`、`next/link` 等 Next 运行时；
- `api-mock.ts`：把 API 调用映射到 `fixtures/` 中的静态响应；
- `fixtures/`：从脱敏后的 `[demo]` 论文库导出的 API 响应；
- `demo.css`：引入真实样式并由 Tailwind 扫描真实前端源码。

从 `frontend/` 运行：

```bash
npm ci
npm run demo:build
```

构建结果在 `frontend/demo-dist`。GitHub Pages 工作流会以 `/paper-pulse/` 作为 base path 构建并部署该目录。

这是只读演示：筛选、搜索、详情查看可用；导入、审核、发布等写操作会提示“静态演示只读”。完整应用见仓库根目录 README 的自部署说明。
