"use client"
import { useState, useEffect } from "react"
import { getSession, signIn } from "next-auth/react"
import { useRouter } from "next/navigation"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { ThemeToggle } from "@/components/theme-toggle"
import { AppLogo } from "@/components/app-logo"
import { Loader2 } from "lucide-react"
import { defaultHomeForRole } from "@/lib/default-home"
import { useIsDingTalk } from "@/hooks/use-device"
import { getApiErrorMessage } from "@/lib/api-errors"

export default function LoginPage() {
  const router = useRouter()
  const isDingTalk = useIsDingTalk()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [autoLoginStopped, setAutoLoginStopped] = useState(false)
  const autoLoginInProgress = isDingTalk && !autoLoginStopped

  async function confirmBrowserSession() {
    const session = await getSession()
    if (!session?.user?.accessToken) {
      throw new Error("浏览器未能保存登录会话。请允许本站 Cookie，关闭 Edge 严格防跟踪或 InPrivate 模式后重试")
    }
    return session
  }

  // 检测是否在钉钉环境，如果是则自动登录
  useEffect(() => {
    if (!isDingTalk) return

    // 尝试从 URL 获取 corpId（钉钉会自动注入）
    const urlParams = new URLSearchParams(window.location.search)
    const urlCorpId = urlParams.get("corpid")

    // 从后端获取钉钉配置
    fetch("/api/auth/dingtalk/config")
      .then((res) => res.json())
      .then((config: { enabled: boolean; corp_id: string; client_id: string }) => {
        if (!config.enabled) {
          setError("钉钉登录未启用")
          setAutoLoginStopped(true)
          return
        }

        const corpId = urlCorpId || config.corp_id
        const clientId = config.client_id

        if (!clientId || !corpId) {
          setError("钉钉登录配置缺失，请联系管理员")
          setAutoLoginStopped(true)
          return
        }

        // 动态导入 dingtalk-jsapi
        import("dingtalk-jsapi").then((dd) => {
          const ddApi = dd.default || dd

          ddApi.requestAuthCode({
            corpId,
            clientId,
            onSuccess: async (result: { code: string }) => {
              try {
                // 发送 authCode 到后端
                const res = await fetch("/api/auth/dingtalk/callback", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ auth_code: result.code }),
                })

                if (!res.ok) {
                  const err = await res.json()
                  setError(`钉钉自动登录失败: ${err.detail}`)
                  setAutoLoginStopped(true)
                  return
                }

                const data = await res.json()

                // 使用 dingtalk-sso provider 创建 next-auth session
                const signInResult = await signIn("dingtalk-sso", {
                  token: data.access_token,
                  username: data.username,
                  name: data.name,
                  role: data.role,
                  redirect: false,
                })

                if (signInResult?.ok) {
                  await confirmBrowserSession()
                  // 使用完整页面跳转（钉钉内置浏览器兼容性更好）
                  window.location.href = defaultHomeForRole(data.role)
                } else {
                  setError("钉钉登录会话创建失败")
                  setAutoLoginStopped(true)
                }
              } catch (err) {
                setError("钉钉登录失败: " + (err as Error).message)
                setAutoLoginStopped(true)
              }
            },
            onFail: (err: unknown) => {
              setError("钉钉授权码获取失败: " + getApiErrorMessage(err, "未知错误"))
              setAutoLoginStopped(true)
            },
          })
        }).catch((err) => {
          setError("钉钉 SDK 加载失败: " + err.message)
          setAutoLoginStopped(true)
        })
      })
      .catch((err) => {
        setError("获取钉钉配置失败: " + err.message)
        setAutoLoginStopped(true)
      })
  }, [isDingTalk, router])

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setLoading(true)
    setError("")
    const form = new FormData(e.currentTarget)
    const username = form.get("username") as string
    try {
      if (!navigator.cookieEnabled) {
        throw new Error("浏览器已禁用 Cookie，请允许本站 Cookie 后重试")
      }
      const result = await signIn("credentials", {
        username,
        password: form.get("password"),
        redirect: false,
      })
      if (result?.ok) {
        const session = await confirmBrowserSession()
        window.location.href = defaultHomeForRole(session.user.role)
      } else {
        setError("用户名或密码错误")
      }
    } catch (err) {
      setError("登录失败：" + (err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="app-viewport flex min-h-0 flex-col items-center justify-center overflow-y-auto bg-background p-4 safe-area-px safe-area-pb">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>

      {/* Logo 区域 */}
      <div className="mb-8 text-center">
        <div className="mb-3 flex justify-center">
          <AppLogo showWordmark={false} iconClassName="h-12 w-12 rounded-xl" />
        </div>
        <h1 className="text-2xl font-bold text-foreground">Paper Pulse</h1>
        <p className="mt-1 text-sm text-muted-foreground">论文内容工作台</p>
      </div>

      {/* 钉钉自动登录加载提示 */}
      {autoLoginInProgress && (
        <Card className="w-full max-w-sm mb-4">
          <CardContent className="pt-6 pb-6">
            <div className="flex flex-col items-center gap-3">
              <Loader2 className="w-8 h-8 animate-spin text-primary" />
              <p className="text-sm text-muted-foreground">正在使用钉钉登录...</p>
            </div>
          </CardContent>
        </Card>
      )}

      <Card className="w-full max-w-sm">
        <CardHeader className="space-y-1 pb-4">
          <CardTitle className="text-lg">登录</CardTitle>
          <CardDescription>请输入你的账号信息</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="username">用户名</Label>
              <Input id="username" name="username" placeholder="admin / reader" required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">密码</Label>
              <Input id="password" name="password" type="password" placeholder="••••••••" required />
            </div>
            {error && (
              <p className="text-sm text-destructive">{error}</p>
            )}
            <Button type="submit" className="w-full" disabled={loading || autoLoginInProgress}>
              {loading ? "登录中..." : "登录"}
            </Button>
          </form>
        </CardContent>
      </Card>

      <p className="mt-6 text-xs text-muted-foreground">科研文献管理与内容工作台</p>
    </div>
  )
}
