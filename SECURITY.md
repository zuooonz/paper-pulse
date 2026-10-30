# 安全

## 漏洞报告

请通过 GitHub 仓库的 Security Advisories 私下提交漏洞报告。请说明受影响的组件、影响范围和复现步骤，并避免在公开 issue、讨论区或 PR 中披露可利用细节。维护者确认并处理问题后，再协商公开披露时间。

## 密码与渠道凭据

本地用户密码应保存为 `python -m api.hash_password` 生成的 PBKDF2-SHA256 哈希。`ALLOW_PLAINTEXT_PASSWORDS` 默认关闭；仅在迁移旧配置时短暂启用，完成哈希替换后立即关闭。

发布渠道凭据由 Fernet 加密后存入数据库。通过 `python -m publisher.credential_store --generate-key` 生成 `CREDENTIALS_ENCRYPTION_KEY`，并将密钥存放在仓库之外的秘密管理设施中。未配置密钥时，应用拒绝保存新凭据。`ALLOW_PLAINTEXT_CREDENTIALS` 默认关闭；只可在隔离的临时本地环境中启用，不要在共享或生产环境保存明文凭据。

## 外部下载与 SSRF 防护

对使用安全下载流程的外部 URL，应用仅允许 HTTP 或 HTTPS，拒绝 URL 内嵌用户凭据，并在请求前及每次重定向时重新解析和校验主机 IP。默认拒绝私有、回环、链路本地、保留、多播及其他非公网地址；跨主机重定向时会移除授权、Cookie 和令牌类请求头。

`ALLOW_PRIVATE_NETWORK_DOWNLOADS` 默认关闭。只有在明确需要访问可信内网下载源、并已评估服务端请求伪造风险时才考虑开启；不要将不可信用户提供的 URL 指向内网服务。

## 默认关闭的危险选项

| 变量 | 默认值 | 用途与建议 |
| --- | --- | --- |
| `ALLOW_PLAINTEXT_PASSWORDS` | `false` | 兼容旧明文密码；只用于短期迁移，迁移后关闭。 |
| `ALLOW_PLAINTEXT_CREDENTIALS` | `false` | 允许无密钥明文保存渠道凭据；共享及生产环境保持关闭。 |
| `ALLOW_PRIVATE_NETWORK_DOWNLOADS` | `false` | 放宽对非公网下载目标的限制；通常保持关闭。 |

## 登录与会话

登录接口对同一 IP 和用户名实施失败次数限制与指数退避；达到阈值后返回 `429` 和 `Retry-After`。这是单进程内保护，生产环境仍应在反向代理或网关层配置基于 IP 的限流。API token 不再持久化到 `localStorage`，也不允许通过 URL 查询参数传递；页面刷新时从 NextAuth 会话恢复到内存。

## 安全响应头

前端对页面响应设置 CSP、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、`X-Content-Type-Options: nosniff`、`Permissions-Policy` 和 HSTS；API 同样设置基础安全头。部署在反向代理后时，请保留这些头并确保 TLS 由可信终结层提供。

## 临时文件

论文 PDF 等下载使用带 HMAC 签名和过期时间的临时链接；临时文件以流式方式写入，文件名会去除路径分隔符，创建新文件时同时清理过期的旧文件。不要将临时目录暴露为静态目录。

## 配置与数据

不要提交 `.env`、`frontend/.env.local`、`config/runtime.yaml`、密码哈希、Cookie、服务令牌或加密密钥。请使用本地忽略文件或秘密管理设施保存部署配置。共享截图、日志和演示材料前，检查是否包含私人论文、个人资料或凭据。
