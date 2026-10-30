"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { useSession } from "next-auth/react"
import Image from "next/image"
import { apiClient } from "@/lib/api"
import type { Account, AccountValidationResponse } from "@/lib/types"
import { useAccounts } from "@/hooks/use-accounts"
import { ARTICLE_CHANNELS, channelName } from "@/lib/channels"
import { getApiErrorMessage } from "@/lib/api-errors"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import { Textarea } from "@/components/ui/textarea"
import { AuditEventsPanel, QuotaAndRequestsPanel } from "@/components/governance-panel"
import { SettingsSectionHeader } from "@/components/settings-section-header"
import { QueryErrorNotice } from "@/components/query-error-notice"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogTrigger,
  DialogClose,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Loader2,
  RefreshCw,
  CheckCircle2,
  XCircle,
  QrCode,
  Smartphone,
  ShieldCheck,
  Plus,
  Trash2,
  Cookie,
  Users,
  Edit,
} from "lucide-react"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

const CHANNELS = Object.keys(ARTICLE_CHANNELS)

function clearPollTimer(ref: { current: ReturnType<typeof setInterval> | null }) {
  if (ref.current) clearInterval(ref.current)
}

import { fmtApiDate, fmtApiDateOnly } from "@/lib/datetime"

function fmtDate(s: string | null) {
  return fmtApiDateOnly(s)
}

function HealthDot({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    healthy: "bg-green-500",
    warning: "bg-amber-500",
    critical: "bg-orange-500",
    expired: "bg-red-500",
    unknown: "bg-gray-400",
  }
  return (
    <span
      className={`inline-block size-2.5 rounded-full ${colorMap[status] || colorMap.unknown}`}
      title={status}
    />
  )
}

// ──────────────────────────────────────────────
// 方形账号卡片
// ──────────────────────────────────────────────
function AccountSquareCard({
  account,
  onUnbound,
  canManage,
  onRebind,
}: {
  account: Account
  onUnbound: () => void
  canManage: boolean
  onRebind: (account: Account) => void
}) {
  const [open, setOpen] = useState(false)
  const qc = useQueryClient()

  const del = useMutation({
    mutationFn: () => apiClient.delete(`/api/accounts/${account.id}`),
    onSuccess: () => {
      setOpen(false)
      onUnbound()
      qc.invalidateQueries({ queryKey: ["accounts"] })
    },
  })

  const validate = useMutation({
    mutationFn: () => apiClient.post<AccountValidationResponse>(`/api/accounts/${account.id}/validate`),
    onSuccess: (res) => {
      const updated = res.data
      if (updated?.last_validated_at) {
        qc.setQueryData(["accounts"], (old: Account[] | undefined) => {
          if (!old) return old
          return old.map((a) =>
            a.id === account.id
              ? {
                  ...a,
                  last_validated_at: updated.last_validated_at,
                  validation_status: updated.validation_status,
                  health: updated.health,
                  valid: updated.health?.overall !== "expired" && updated.health?.overall !== "critical",
                }
              : a
          )
        })
      }
    },
  })

  const health = account.health
  const overall = health?.overall || "unknown"
  const needsRebind = overall === "expired" || overall === "critical"

  return (
    <Card className="flex flex-col h-48 md:h-64 p-3 md:p-4 relative hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-semibold text-sm truncate leading-tight" title={account.account_name}>
          {account.account_name}
        </h3>
        <HealthDot status={overall} />
      </div>
      <p className="text-[10px] text-muted-foreground mt-0.5">{channelName(account.channel)}</p>

      <div className="flex-1 flex flex-col justify-center gap-1 text-[11px] text-muted-foreground mt-1">
        <div className="flex items-center gap-1">
          <span className="shrink-0">有效期:</span>
          {account.expires_at ? (
            <span className={account.valid ? "text-green-600" : "text-red-500"}>
              {account.valid ? fmtDate(account.expires_at) : "已过期"}
            </span>
          ) : (
            <span>长期</span>
          )}
        </div>
        {account.last_validated_at && (
          <div className="flex items-center gap-1">
            <span className="shrink-0">验证:</span>
            <span>{fmtDate(account.last_validated_at)}</span>
          </div>
        )}
        {health?.message && (
          <p className={`text-[10px] leading-tight ${overall === "healthy" ? "text-green-600" : overall === "expired" ? "text-red-500" : "text-amber-600"}`}>
            {health.message}
          </p>
        )}
      </div>

      <div className="flex items-center gap-1.5 mt-2 pt-2 border-t">
        <Button size="sm" variant="ghost" className="h-7 px-2 text-[11px]" onClick={() => validate.mutate()} disabled={validate.isPending} title="验证">
          {validate.isPending ? <Loader2 className="size-3 animate-spin" /> : <ShieldCheck className="size-3" />}
          <span className="ml-1">验证</span>
        </Button>
        {needsRebind && canManage && (
          <Button size="sm" variant="ghost" className="h-7 px-2 text-[11px] text-amber-600 hover:text-amber-700 hover:bg-amber-50" onClick={() => onRebind(account)}>
            <RefreshCw className="size-3" />
            <span className="ml-1">重绑</span>
          </Button>
        )}
        {canManage && (
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger
              render={
                <Button size="sm" variant="ghost" className="h-7 px-2 text-[11px] text-destructive hover:text-destructive hover:bg-destructive/10 ml-auto">
                  <Trash2 className="size-3" />
                </Button>
              }
            />
            <DialogContent>
              <DialogHeader>
                <DialogTitle>确认解绑账号</DialogTitle>
                <DialogDescription>确定要解绑账号「{account.account_name}」吗？此操作不可撤销。</DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <DialogClose render={<Button variant="outline" size="sm">取消</Button>} />
                <Button variant="destructive" size="sm" onClick={() => del.mutate()} disabled={del.isPending}>
                  {del.isPending ? "解绑中..." : "确认解绑"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </div>
    </Card>
  )
}

// ──────────────────────────────────────────────
// 自动登录面板（扫码 + 短信左右并排，高度平衡）
// ──────────────────────────────────────────────
function AutoLoginPanel({
  channel,
  onCookieReady,
}: {
  channel: string
  onCookieReady: (result: { cookieStr: string; taskId: string } | null) => void
}) {
  const [phone, setPhone] = useState("")

  // QR state
  const [qrTaskId, setQrTaskId] = useState<string | null>(null)
  const [qrStatus, setQrStatus] = useState("")
  const [qrMessage, setQrMessage] = useState("")
  const [qrCode, setQrCode] = useState<string | null>(null)
  const [qrCookieStr, setQrCookieStr] = useState<string | null>(null)
  const [qrError, setQrError] = useState<string | null>(null)
  const [qrIsStarting, setQrIsStarting] = useState(false)

  // SMS state
  const [smsTaskId, setSmsTaskId] = useState<string | null>(null)
  const [smsStatus, setSmsStatus] = useState("")
  const [smsMessage, setSmsMessage] = useState("")
  const [smsCode, setSmsCode] = useState("")
  const [smsCookieStr, setSmsCookieStr] = useState<string | null>(null)
  const [smsError, setSmsError] = useState<string | null>(null)
  const [smsIsStarting, setSmsIsStarting] = useState(false)
  const [smsIsSubmitting, setSmsIsSubmitting] = useState(false)

  const qrPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const smsPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const onCookieReadyRef = useRef(onCookieReady)

  useEffect(() => {
    onCookieReadyRef.current = onCookieReady
  }, [onCookieReady])

  useEffect(() => {
    return () => {
      clearPollTimer(qrPollRef)
      clearPollTimer(smsPollRef)
    }
  }, [])

  const startPolling = (tid: string, mode: "qrcode" | "sms") => {
    const ref = mode === "qrcode" ? qrPollRef : smsPollRef
    if (ref.current) clearInterval(ref.current)
    ref.current = setInterval(async () => {
      try {
        const pollRes = await apiClient.get(`/api/accounts/auto-login/${tid}`)
        const d = pollRes.data
        if (mode === "qrcode") {
          setQrStatus(d.status)
          setQrMessage(d.message)
          if (d.qr_code_base64 && !qrCode) setQrCode(d.qr_code_base64)
          if (d.status === "success") {
            setQrCookieStr(d.cookie_str)
            onCookieReadyRef.current?.({ cookieStr: d.cookie_str, taskId: tid })
            if (qrPollRef.current) clearInterval(qrPollRef.current)
          }
          if (d.status === "failed" || d.status === "cancelled") {
            setQrError(d.error || "登录失败")
            if (qrPollRef.current) clearInterval(qrPollRef.current)
          }
        } else {
          setSmsStatus(d.status)
          setSmsMessage(d.message)
          if (d.status === "success") {
            setSmsCookieStr(d.cookie_str)
            onCookieReadyRef.current?.({ cookieStr: d.cookie_str, taskId: tid })
            if (smsPollRef.current) clearInterval(smsPollRef.current)
          }
          if (d.status === "failed" || d.status === "cancelled") {
            setSmsError(d.error || "登录失败")
            if (smsPollRef.current) clearInterval(smsPollRef.current)
          }
        }
      } catch (e) {
        console.error("Polling error:", e)
      }
    }, 3000)
  }

  const startQr = async () => {
    setQrIsStarting(true)
    setQrError(null)
    try {
      const res = await apiClient.post("/api/accounts/auto-login", {
        channel, mode: "qrcode", account_name: "", phone: "",
      })
      const d = res.data
      setQrTaskId(d.task_id)
      setQrStatus(d.status)
      setQrMessage(d.message)
      if (d.qr_code_base64) setQrCode(d.qr_code_base64)
      startPolling(d.task_id, "qrcode")
    } catch (e: unknown) {
      setQrError(getApiErrorMessage(e, "启动失败"))
    } finally {
      setQrIsStarting(false)
    }
  }

  const startSms = async () => {
    if (!phone.trim()) return
    setSmsIsStarting(true)
    setSmsError(null)
    try {
      const res = await apiClient.post("/api/accounts/auto-login", {
        channel, mode: "sms", account_name: "", phone,
      })
      const d = res.data
      setSmsTaskId(d.task_id)
      setSmsStatus(d.status)
      setSmsMessage(d.message)
      startPolling(d.task_id, "sms")
    } catch (e: unknown) {
      setSmsError(getApiErrorMessage(e, "启动失败"))
    } finally {
      setSmsIsStarting(false)
    }
  }

  const submitSmsCode = async () => {
    if (!smsTaskId || !smsCode.trim()) return
    setSmsIsSubmitting(true)
    try {
      await apiClient.post(`/api/accounts/auto-login/${smsTaskId}/submit-sms-code`, { sms_code: smsCode })
      setSmsMessage("已提交，登录中...")
    } catch (e: unknown) {
      setSmsError(getApiErrorMessage(e, "提交失败"))
    } finally {
      setSmsIsSubmitting(false)
    }
  }

  const cancelQr = async () => {
    if (qrTaskId) {
      try { await apiClient.post(`/api/accounts/auto-login/${qrTaskId}/cancel`) } catch { /* ignore */ }
    }
    if (qrPollRef.current) clearInterval(qrPollRef.current)
    setQrTaskId(null); setQrStatus(""); setQrMessage(""); setQrCode(null); setQrCookieStr(null); setQrError(null)
    onCookieReadyRef.current?.(null)
  }

  const cancelSms = async () => {
    if (smsTaskId) {
      try { await apiClient.post(`/api/accounts/auto-login/${smsTaskId}/cancel`) } catch { /* ignore */ }
    }
    if (smsPollRef.current) clearInterval(smsPollRef.current)
    setSmsTaskId(null); setSmsStatus(""); setSmsMessage(""); setSmsCode(""); setSmsCookieStr(null); setSmsError(null)
    onCookieReadyRef.current?.(null)
  }

  const qrRunning = !!qrTaskId && !["success", "failed", "cancelled"].includes(qrStatus)
  const qrSuccess = qrStatus === "success"
  const qrNeedsVerify = qrStatus === "needs_verify"

  const smsRunning = !!smsTaskId && !["success", "failed", "cancelled"].includes(smsStatus)
  const smsWaitingCode = smsStatus === "waiting_for_sms_code"
  const smsSuccess = smsStatus === "success"

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 flex-1">
      {/* 扫码登录 */}
      <div className="flex flex-col gap-3 rounded-lg border bg-background/50 p-4 min-h-[220px]">
        {!qrTaskId && (
          <>
            <div className="flex-1 flex flex-col items-center justify-center gap-2 text-muted-foreground">
              <QrCode className="size-8 opacity-40" />
              <p className="text-xs">使用 App 扫码登录</p>
            </div>
            <Button size="sm" className="w-full" disabled={qrIsStarting} onClick={startQr}>
              {qrIsStarting ? <Loader2 className="size-4 animate-spin mr-1" /> : <QrCode className="size-4 mr-1" />}
              扫码登录
            </Button>
          </>
        )}

        {qrRunning && (
          <div className="flex-1 flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <Button size="sm" variant="outline" onClick={cancelQr}>取消</Button>
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <Loader2 className="size-3 animate-spin" />{qrMessage || "等待扫码..."}
              </span>
            </div>
            {qrCode && (
              <div className="flex flex-col items-center gap-2 rounded border bg-muted p-3">
                <Image src={`data:image/png;base64,${qrCode}`} alt="QR" width={112} height={112} unoptimized className="size-28 rounded border bg-white" />
                <p className="text-xs text-muted-foreground text-center">
                  {qrNeedsVerify ? "扫码成功！需二次验证..." : "请用手机 App 扫描此二维码"}
                </p>
              </div>
            )}
          </div>
        )}

        {qrSuccess && (
          <div className="flex-1 flex flex-col gap-2 rounded border border-green-200 bg-green-50 p-3 dark:border-green-900 dark:bg-green-950">
            <div className="flex items-center gap-1 text-green-700 text-xs">
              <CheckCircle2 className="size-3.5" />
              <span className="font-medium">登录成功</span>
            </div>
            <p className="text-[10px] text-muted-foreground">Cookie: {qrCookieStr?.length || 0} 字符</p>
            <div className="mt-auto">
              <Button size="sm" variant="outline" className="h-7 text-xs" onClick={cancelQr}>重试</Button>
            </div>
          </div>
        )}

        {qrError && (
          <div className="flex-1 flex flex-col gap-2 rounded border border-red-200 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950">
            <p className="text-xs text-red-600">{qrError}</p>
            <Button size="sm" variant="outline" className="h-7 text-xs mt-auto" onClick={cancelQr}>
              <RefreshCw className="size-3 mr-1" />重试
            </Button>
          </div>
        )}
      </div>

      {/* 短信验证码登录 */}
      <div className="flex flex-col gap-3 rounded-lg border bg-background/50 p-4 min-h-[220px]">
        {!smsTaskId && (
          <>
            <div className="flex-1 flex flex-col items-center justify-center gap-2 text-muted-foreground">
              <Smartphone className="size-8 opacity-40" />
              <p className="text-xs">短信验证码登录</p>
            </div>
            <div className="flex flex-col gap-2">
              <Input placeholder="手机号" value={phone} onChange={(e) => setPhone(e.target.value)} className="text-sm" />
              <Button size="sm" className="w-full" disabled={smsIsStarting || !phone.trim()} onClick={startSms}>
                {smsIsStarting ? <Loader2 className="size-4 animate-spin mr-1" /> : <Smartphone className="size-4 mr-1" />}
                获取验证码
              </Button>
            </div>
          </>
        )}

        {smsRunning && !smsWaitingCode && (
          <div className="flex-1 flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <Button size="sm" variant="outline" onClick={cancelSms}>取消</Button>
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <Loader2 className="size-3 animate-spin" />{smsMessage || "处理中..."}
              </span>
            </div>
          </div>
        )}

        {smsWaitingCode && (
          <div className="flex-1 flex flex-col gap-2">
            <p className="text-xs text-muted-foreground">验证码已发送至 {phone}</p>
            <div className="flex items-center gap-2">
              <Input placeholder="验证码" maxLength={6} className="w-28 text-sm" value={smsCode} onChange={(e) => setSmsCode(e.target.value)} />
              <Button size="sm" disabled={smsIsSubmitting || smsCode.length < 4} onClick={submitSmsCode}>
                {smsIsSubmitting ? <Loader2 className="size-4 animate-spin mr-1" /> : null}
                提交
              </Button>
            </div>
            <Button size="sm" variant="ghost" className="w-fit text-xs h-7 mt-auto" onClick={cancelSms}>取消</Button>
          </div>
        )}

        {smsSuccess && (
          <div className="flex-1 flex flex-col gap-2 rounded border border-green-200 bg-green-50 p-3 dark:border-green-900 dark:bg-green-950">
            <div className="flex items-center gap-1 text-green-700 text-xs">
              <CheckCircle2 className="size-3.5" />
              <span className="font-medium">登录成功</span>
            </div>
            <p className="text-[10px] text-muted-foreground">Cookie: {smsCookieStr?.length || 0} 字符</p>
            <div className="mt-auto">
              <Button size="sm" variant="outline" className="h-7 text-xs" onClick={cancelSms}>重试</Button>
            </div>
          </div>
        )}

        {smsError && (
          <div className="flex-1 flex flex-col gap-2 rounded border border-red-200 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950">
            <p className="text-xs text-red-600">{smsError}</p>
            <Button size="sm" variant="outline" className="h-7 text-xs mt-auto" onClick={cancelSms}>
              <RefreshCw className="size-3 mr-1" />重试
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────
// 添加账号面板（方形按钮 → 下方展开）
// ──────────────────────────────────────────────
function AddAccountPanel({
  channel,
  onAdded,
  canManage,
  autoOpen = false,
  defaultAccountName = "",
  overwriteAccountId = null,
  accounts,
}: {
  channel: string
  onAdded: () => void
  canManage: boolean
  autoOpen?: boolean
  defaultAccountName?: string
  overwriteAccountId?: number | null
  accounts: Account[]
}) {
  const [open, setOpen] = useState(autoOpen)

  const qc = useQueryClient()
  const [accountName, setAccountName] = useState(defaultAccountName)
  const [form, setForm] = useState({ cookie: "" })
  const [autoLoginResult, setAutoLoginResult] = useState<{ cookieStr: string; taskId: string } | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const [overwriteDialogOpen, setOverwriteDialogOpen] = useState(false)
  const [overwriteTargetName, setOverwriteTargetName] = useState("")
  const credentialMetadata = ARTICLE_CHANNELS[channel as keyof typeof ARTICLE_CHANNELS]
  const credentialLabel = credentialMetadata?.credentialLabel ?? "Cookie"
  const credentialDescription = credentialMetadata?.credentialDescription ?? "从浏览器 F12 → Application → Cookies 复制全部"

  const doSave = async (name: string, deleteOldId?: number) => {
    try {
      if (deleteOldId) {
        await apiClient.delete(`/api/accounts/${deleteOldId}`)
      }
      if (autoLoginResult) {
        await apiClient.post(`/api/accounts/auto-login/${autoLoginResult.taskId}/save`, {
          account_name: name,
        })
      } else {
        await apiClient.post("/api/accounts", {
          channel, account_name: name, credential_type: credentialMetadata?.credentialType ?? "cookie", credential: form.cookie,
        })
      }
      setOpen(false)
      setAccountName("")
      setForm({ cookie: "" })
      setAutoLoginResult(null)
      setSaveError(null)
      onAdded()
      qc.invalidateQueries({ queryKey: ["accounts"] })
    } catch (e: unknown) {
      setSaveError(getApiErrorMessage(e, "保存失败"))
    } finally {
      setIsSaving(false)
    }
  }

  const handleSave = async () => {
    const name = accountName.trim()
    if (!name) {
      setSaveError("请填写账号自定义名称")
      return
    }
    if (!autoLoginResult && !form.cookie.trim()) {
      setSaveError("请先完成登录或粘贴 Cookie")
      return
    }
    setSaveError(null)
    setIsSaving(true)

    // 查找同名账号（排除当前重绑的目标，重绑时自动覆盖不提示）
    const existing = accounts.find(
      (a) => a.channel === channel && a.account_name === name && a.id !== overwriteAccountId
    )

    if (existing) {
      // 新建模式：提示用户确认覆盖
      setOverwriteTargetName(existing.account_name)
      setOverwriteDialogOpen(true)
      setIsSaving(false)
      return
    }

    // 重绑模式或无同名：直接保存（重绑时先删除旧账号）
    await doSave(name, overwriteAccountId ?? undefined)
  }

  const handleConfirmOverwrite = async () => {
    const name = accountName.trim()
    setOverwriteDialogOpen(false)
    setIsSaving(true)
    const existing = accounts.find(
      (a) => a.channel === channel && a.account_name === name
    )
    await doSave(name, existing?.id)
  }

  if (!open) {
    if (!canManage) return null
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex flex-col items-center justify-center h-48 md:h-64 rounded-xl border-2 border-dashed border-muted-foreground/25 hover:border-primary/50 hover:bg-muted/50 transition-colors text-muted-foreground hover:text-primary gap-2"
      >
        <Plus className="size-8" />
        <span className="text-sm font-medium">添加账号</span>
      </button>
    )
  }

  return (
    <div className="flex flex-col gap-4 col-span-full">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-sm">添加 {channelName(channel)} 账号</h3>
        <Button size="sm" variant="ghost" className="h-8 w-8 p-0" onClick={() => setOpen(false)}>
          <XCircle className="size-4" />
        </Button>
      </div>

      <Card className="mx-auto w-full max-w-4xl p-5">
          {/* 公共账号名称 + 保存按钮 */}
          <div className="mb-5 flex items-center gap-3 flex-wrap">
            <Input
              placeholder="账号自定义名称（仅展示）"
              value={accountName}
              onChange={(e) => {
                setAccountName(e.target.value)
                if (saveError) setSaveError(null)
              }}
              className="max-w-sm"
            />
            <Button
              size="sm"
              disabled={isSaving}
              onClick={handleSave}
            >
              {isSaving ? <Loader2 className="size-4 animate-spin mr-1" /> : null}
              保存
            </Button>
            {saveError && <p className="text-xs text-destructive">{saveError}</p>}
          </div>

          {/* 左右两栏，等高 */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 items-stretch">
            {/* 左侧：登陆方式一 — 自动登录获取 Cookie */}
            <div className="rounded-xl border bg-card p-5 flex flex-col gap-4 h-full">
              <div className="flex items-center gap-2 text-sm font-medium text-card-foreground">
                <QrCode className="size-4 text-primary" />
                登陆方式一：自动登录获取 {credentialLabel}
              </div>
              <div className="flex-1 flex flex-col">
                <AutoLoginPanel
                  channel={channel}
                  onCookieReady={(result) => {
                    setAutoLoginResult(result)
                    if (saveError && result) setSaveError(null)
                  }}
                />
              </div>
            </div>

            {/* 右侧：登陆方式二 — 手动粘贴 Cookie */}
            <div className="rounded-xl border bg-card p-5 flex flex-col gap-4 h-full">
              <div className="flex items-center gap-2 text-sm font-medium text-card-foreground">
                <Cookie className="size-4 text-primary" />
                登陆方式二：手动粘贴 {credentialLabel}
              </div>
              <div className="flex-1 flex flex-col gap-3 min-h-0">
                <Textarea
                  placeholder={`粘贴 ${credentialLabel} 字符串（${credentialDescription}）`}
                  className="flex-1 min-h-0 w-full text-xs resize-none overflow-y-auto field-sizing-fixed font-mono leading-relaxed"
                  value={form.cookie}
                  onChange={(e) => {
                    setForm((f) => ({ ...f, cookie: e.target.value }))
                    if (saveError) setSaveError(null)
                  }}
                />
              </div>
            </div>
          </div>
      </Card>

      {/* 覆盖确认对话框 */}
      <Dialog open={overwriteDialogOpen} onOpenChange={setOverwriteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认覆盖账号</DialogTitle>
            <DialogDescription>
              已有同名账号「{overwriteTargetName}」，是否覆盖？此操作将删除旧账号并用新信息替换。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" size="sm">取消</Button>} />
            <Button variant="destructive" size="sm" onClick={handleConfirmOverwrite} disabled={isSaving}>
              {isSaving ? "覆盖中..." : "确认覆盖"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// ──────────────────────────────────────────────
// 钉钉用户管理面板
// ──────────────────────────────────────────────
interface DingTalkUser {
  id: number
  dingtalk_userid: string
  internal_username: string
  role: string
  display_name: string | null
  created_at: string
  updated_at: string
}

const ROLE_LABELS: Record<string, string> = {
  admin: "管理员",
  reviewer: "审核员",
  viewer: "查看者",
}



function DingTalkUsersPanel() {
  const [error, setError] = useState("")
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editUsername, setEditUsername] = useState("")
  const {
    data: users = [],
    isLoading: loading,
    error: queryError,
    refetch,
  } = useQuery<DingTalkUser[]>({
    queryKey: ["dingtalk-users"],
    queryFn: async () => (await apiClient.get<DingTalkUser[]>("/api/auth/dingtalk/users")).data,
  })

  const fetchUsers = useCallback(async () => {
    setError("")
    await refetch()
  }, [refetch])

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: { internal_username?: string } }) =>
      apiClient.put(`/api/auth/dingtalk/users/${id}`, data),
    onSuccess: () => {
      setEditingId(null)
      fetchUsers()
    },
    onError: (err: unknown) => setError(getApiErrorMessage(err, "更新失败")),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => apiClient.delete(`/api/auth/dingtalk/users/${id}`),
    onSuccess: () => fetchUsers(),
    onError: (err: unknown) => setError(getApiErrorMessage(err, "删除失败")),
  })

  const startEdit = (user: DingTalkUser) => {
    setEditingId(user.id)
    setEditUsername(user.internal_username)
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <SettingsSectionHeader
        title="钉钉用户"
        description="管理钉钉用户与通用账号的映射。"
        onRefresh={fetchUsers}
        refreshing={loading}
      />

      {(error || (queryError ? getApiErrorMessage(queryError, "获取用户列表失败") : "")) && (
        <div className="p-3 bg-destructive/10 text-destructive rounded text-sm">
          {error || getApiErrorMessage(queryError, "获取用户列表失败")}
        </div>
      )}

      {loading ? (
        <Card className="p-8 text-center text-muted-foreground">加载中...</Card>
      ) : users.length === 0 ? (
        <Card className="p-8 text-center text-muted-foreground">
          <Users className="size-10 mx-auto mb-3 opacity-30" />
          <p>暂无钉钉用户登录记录</p>
          <p className="text-xs mt-1">钉钉用户首次登录后会自动出现在此列表中</p>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto"><Table>
            <TableHeader>
              <TableRow>
                <TableHead>钉钉姓名</TableHead>
                <TableHead>钉钉 UserID</TableHead>
                <TableHead>映射账号</TableHead>
                <TableHead>权限角色</TableHead>
                <TableHead>最近登录</TableHead>
                <TableHead className="w-[100px] text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((user) => (
                <TableRow key={user.id}>
                  <TableCell className="font-medium">
                    {user.display_name || <span className="text-muted-foreground italic">未知</span>}
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {user.dingtalk_userid}
                  </TableCell>
                  <TableCell>
                    {editingId === user.id ? (
                      <Select value={editUsername} onValueChange={(v) => setEditUsername(v || "")}>
                        <SelectTrigger className="h-8 w-[120px]">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="admin">管理员</SelectItem>
                          <SelectItem value="reader">只读用户</SelectItem>
                        </SelectContent>
                      </Select>
                    ) : (
                      user.internal_username
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary" className="text-xs">
                      {ROLE_LABELS[user.role] || user.role}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {fmtApiDate(user.updated_at, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
                  </TableCell>
                  <TableCell className="text-right">
                    {editingId === user.id ? (
                      <div className="flex gap-1 justify-end">
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-2 text-xs"
                          onClick={() => updateMutation.mutate({
                            id: user.id,
                            data: { internal_username: editUsername },
                          })}
                          disabled={updateMutation.isPending}
                        >
                          {updateMutation.isPending ? <Loader2 className="size-3 animate-spin" /> : "保存"}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-2 text-xs text-muted-foreground"
                          onClick={() => setEditingId(null)}
                        >
                          取消
                        </Button>
                      </div>
                    ) : (
                      <div className="flex gap-1 justify-end">
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-2"
                          onClick={() => startEdit(user)}
                        >
                          <Edit className="size-3.5" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-2 text-destructive hover:text-destructive"
                          onClick={() => {
                            if (confirm("确定删除此钉钉用户映射？删除后该用户下次登录将重新自动创建。")) {
                              deleteMutation.mutate(user.id)
                            }
                          }}
                          disabled={deleteMutation.isPending}
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table></div>
        </Card>
      )}
    </div>
  )
}

// ──────────────────────────────────────────────
// Main page
// ──────────────────────────────────────────────
export default function SettingsPage() {
  const { data: session } = useSession()
  const role = session?.user?.role ?? "viewer"
  const canManage = role === "admin" || role === "reviewer"
  const isAdmin = role === "admin"

  // channel / identity / quota approval / audit governance views
  const [activeView, setActiveView] = useState<string>("channel:" + CHANNELS[0])
  const [rebindAccount, setRebindAccount] = useState<Account | null>(null)
  const qc = useQueryClient()

  const activeChannel = activeView.startsWith("channel:") ? activeView.slice(8) : CHANNELS[0]
  const showDingTalk = activeView === "dingtalk"
  const showQuota = activeView === "governance-quota"
  const showAudit = activeView === "governance-audit"

  const {
    data: accounts,
    isLoading,
    isError: isAccountsError,
    isRefetchError: isAccountsRefetchError,
    error: accountsError,
    refetch: refetchAccounts,
    isFetching: isAccountsFetching,
  } = useAccounts()

  const channelAccounts = (accounts ?? []).filter((a) => a.channel === activeChannel)

  const validateAll = useMutation({
    mutationFn: () => apiClient.post("/api/accounts/validate-all"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  })

  const healthCounts = (accounts ?? []).reduce(
    (acc, a) => {
      const status = a.health?.overall || "unknown"
      acc[status] = (acc[status] || 0) + 1
      return acc
    },
    {} as Record<string, number>
  )
  const expiredCount = healthCounts["expired"] || 0
  const warningCount = (healthCounts["warning"] || 0) + (healthCounts["critical"] || 0)

  return (
    <div className="flex h-full min-h-0 overflow-hidden">
      {/* Left sidebar */}
      <aside className="w-28 md:w-72 shrink-0 border-r bg-sidebar p-2 md:p-3 flex flex-col gap-0.5 md:gap-1">
        <p className="px-2 py-1 text-[10px] md:text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">
          渠道
        </p>
        {CHANNELS.map((ch) => {
          const count = (accounts ?? []).filter((a) => a.channel === ch).length
          return (
            <button
              key={ch}
              onClick={() => {
                setActiveView("channel:" + ch)
                setRebindAccount(null)
              }}
              className={`flex items-center justify-between w-full rounded-md px-2 py-1.5 md:py-1.5 text-xs md:text-sm transition-colors text-left ${
                activeView === "channel:" + ch
                  ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                  : "hover:bg-sidebar-accent/60 text-sidebar-foreground"
              }`}
            >
              <span className="truncate">{channelName(ch)}</span>
              {count > 0 && (
                <Badge variant="secondary" className="text-[10px] md:text-xs h-4 ml-1 shrink-0">
                  {count}
                </Badge>
              )}
            </button>
          )
        })}

        {isAdmin && (
          <>
            <div className="mt-3 mb-1">
              <p className="px-2 py-1 text-[10px] md:text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                授权
              </p>
            </div>
            <button
              onClick={() => setActiveView("dingtalk")}
              className={`flex items-center w-full rounded-md px-2 py-1.5 text-xs md:text-sm transition-colors text-left ${
                showDingTalk
                  ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                  : "hover:bg-sidebar-accent/60 text-sidebar-foreground"
              }`}
            >
              <span>钉钉用户</span>
            </button>
            <button
              onClick={() => setActiveView("governance-quota")}
              className={`flex items-center w-full rounded-md px-2 py-1.5 text-xs md:text-sm transition-colors text-left ${
                showQuota
                  ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                  : "hover:bg-sidebar-accent/60 text-sidebar-foreground"
              }`}
            >
              <span>额度与申请</span>
            </button>
            <button
              onClick={() => setActiveView("governance-audit")}
              className={`flex items-center w-full rounded-md px-2 py-1.5 text-xs md:text-sm transition-colors text-left ${
                showAudit
                  ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                  : "hover:bg-sidebar-accent/60 text-sidebar-foreground"
              }`}
            >
              <span>行为记录</span>
            </button>
          </>
        )}

      </aside>

      {/* Right content */}
      <main className="min-h-0 flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto overscroll-contain p-3 pb-[calc(1.5rem+env(safe-area-inset-bottom,0px))] scrollbar-none md:p-6">
        {showDingTalk ? (
          <DingTalkUsersPanel />
        ) : showQuota ? (
          <QuotaAndRequestsPanel />
        ) : showAudit ? (
          <AuditEventsPanel />
        ) : (
        <>
        <SettingsSectionHeader
          title={`${channelName(activeChannel)} 账号管理`}
          description="管理渠道账号、登录凭据和可用状态。"
          actions={canManage ? (
            <div className="flex items-center gap-2">
              {(expiredCount > 0 || warningCount > 0) && (
                <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${
                  expiredCount > 0 ? "bg-red-100 text-red-700 border-red-200" : "bg-amber-100 text-amber-700 border-amber-200"
                }`}>
                  {expiredCount > 0 && `${expiredCount} 个已失效`}
                  {expiredCount > 0 && warningCount > 0 && "，"}
                  {warningCount > 0 && `${warningCount} 个即将过期`}
                </span>
              )}
              <Button size="sm" variant="outline" onClick={() => validateAll.mutate()} disabled={validateAll.isPending}>
                {validateAll.isPending ? <Loader2 className="mr-1 size-4 animate-spin" /> : <ShieldCheck className="mr-1 size-4" />}
                批量验证
              </Button>
            </div>
          ) : undefined}
        />

        {/* 账号网格（方形卡片 + 添加按钮 Z 形排列） */}
        {(isAccountsError || isAccountsRefetchError) && (
          <QueryErrorNotice
            className="mb-4"
            message={`账号列表加载失败：${getApiErrorMessage(accountsError, "请稍后重试")}`}
            onRetry={() => { void refetchAccounts() }}
            retrying={isAccountsFetching}
          />
        )}
        {isLoading ? (
          <div className="grid grid-cols-2 md:grid-cols-2 lg:grid-cols-3 gap-2 md:gap-4">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-48 md:h-64 rounded-xl" />
            ))}
          </div>
        ) : isAccountsError && !accounts ? null : (
          <div className="grid grid-cols-2 md:grid-cols-2 lg:grid-cols-3 gap-2 md:gap-4">
            {channelAccounts.map((acc) => (
              <AccountSquareCard
                key={acc.id}
                account={acc}
                onUnbound={() => qc.invalidateQueries({ queryKey: ["accounts"] })}
                canManage={canManage}
                onRebind={(acc) => {
                  setActiveView("channel:" + acc.channel)
                  setRebindAccount(acc)
                }}
              />
            ))}
            {canManage && (
              <AddAccountPanel
                key={`add-panel-${activeChannel}-${rebindAccount?.id || 'none'}`}
                channel={activeChannel}
                onAdded={() => {
                  qc.invalidateQueries({ queryKey: ["accounts"] })
                  setRebindAccount(null)
                }}
                canManage={canManage}
                autoOpen={rebindAccount?.channel === activeChannel}
                defaultAccountName={rebindAccount?.account_name || ""}
                overwriteAccountId={rebindAccount?.id ?? null}
                accounts={channelAccounts}
              />
            )}
          </div>
        )}
        </>
        )}

      </main>
    </div>
  )
}
