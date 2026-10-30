"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { apiClient } from "@/lib/api"
import { appDateOffset, appLocalInputToIso, fmtApiDate } from "@/lib/datetime"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { SettingsSectionHeader } from "@/components/settings-section-header"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { Check, ShieldCheck, X } from "lucide-react"
import { getApiErrorMessage } from "@/lib/api-errors"

type QuotaPolicy = {
  id: number
  role: string
  limit_value: number
  timezone: string
  enabled: boolean
  updated_at: string
}

type AccessRequest = {
  id: number
  requester_actor_id: string
  requester_name: string | null
  business_date: string
  request_type: "exact_scope" | "daily_extra"
  action: string
  requested_count: number
  paper_ids: number[]
  reason: string
  status: string
  review_comment: string | null
  approved_count: number | null
  expires_at: string | null
  created_at: string
  version: number
}

type QuotaUsage = {
  actor_id: string
  business_date: string
  base_limit: number
  extra_granted: number
  used: number
  reserved: number
  updated_at: string
}

type AuditEvent = {
  event_id: string
  occurred_at: string
  actor_id: string
  display_name: string | null
  role: string
  action: string
  resource_type: string
  resource_id: string | null
  resource_count: number
  result: string
  ip_address: string | null
  paper_ids: number[]
  paper_internal_ids: number[]
  details: Record<string, unknown>
}

const STATUS_LABEL: Record<string, string> = {
  pending: "待审批",
  approved: "已批准",
  rejected: "已拒绝",
  cancelled: "已撤销",
  expired: "已过期",
}

function detailMessage(error: unknown, fallback: string) {
  return getApiErrorMessage(error, fallback)
}

const AUDIT_ACTION_LABELS: Record<string, string> = {
  "paper.view": "查看论文",
  "paper.change": "修改论文",
  "paper.delete": "删除论文",
  "paper.export_excel": "导出论文",
  "paper.batch_pdf": "批量下载 PDF",
  "paper.get_pdf": "下载 PDF",
  "paper.generate_word_summary": "生成 Word 总结",
  "draft.change": "修改草稿",
  "draft.delete": "删除草稿",
  "video.change": "修改视频",
  "video.delete": "删除视频",
  "channel_account.change": "修改渠道账号",
  "channel_account.delete": "删除渠道账号",
}

function auditActionLabel(row: AuditEvent): string {
  // Older delete events were stored as *.change. Infer their real operation
  // from the immutable request details so historical rows remain intelligible.
  const method = typeof row.details?.method === "string" ? row.details.method.toUpperCase() : ""
  if (method === "DELETE") {
    const byResource: Record<string, string> = {
      paper: "删除论文",
      draft: "删除草稿",
      video: "删除视频",
      channel_account: "删除渠道账号",
    }
    return byResource[row.resource_type] || "删除记录"
  }
  return AUDIT_ACTION_LABELS[row.action] || row.action
}

function AuditActionSummary({ row }: { row: AuditEvent }) {
  return <>
    <div>{auditActionLabel(row)}</div>
    <div className="break-all font-mono text-[10px] text-muted-foreground">{row.action}</div>
    {typeof row.details?.delete_reason === "string" && (
      <div className="mt-1 break-words text-xs text-muted-foreground" title={row.details.delete_reason}>
        原因：{row.details.delete_reason}
      </div>
    )}
  </>
}

function AuditResourceSummary({ row }: { row: AuditEvent }) {
  return <>
    <div className="break-words">
      {row.resource_type}
      {row.resource_type === "paper" && row.paper_ids?.length > 0
        ? ` #${row.paper_ids.join(", ")}`
        : row.resource_id ? ` #${row.resource_id}` : ""}
    </div>
    {row.paper_internal_ids?.length > 0 && (
      <div className="break-words text-xs text-muted-foreground">
        Cite ID: {row.paper_internal_ids.join(", ")}
      </div>
    )}
    {row.resource_type === "paper" && typeof row.details?.artifact_id === "string" && (
      <div className="break-all font-mono text-[10px] text-muted-foreground">
        制品 ID：{row.details.artifact_id}
      </div>
    )}
    <div className="text-xs text-muted-foreground">数量：{row.resource_count}</div>
  </>
}

function AuditResultBadge({ result }: { result: string }) {
  return <Badge variant={result === "success" ? "secondary" : result === "denied" ? "outline" : "destructive"}>
    {result}
  </Badge>
}

export function QuotaAndRequestsPanel() {
  const qc = useQueryClient()
  const [error, setError] = useState("")
  const [limitDraft, setLimitDraft] = useState<Record<number, string>>({})
  const [reviewRow, setReviewRow] = useState<AccessRequest | null>(null)
  const [grantType, setGrantType] = useState<"exact_scope" | "daily_extra">("exact_scope")
  const [approvedCount, setApprovedCount] = useState("1")
  const [reviewComment, setReviewComment] = useState("同意本次申请")
  const [expiresAt, setExpiresAt] = useState("")

  const policies = useQuery<QuotaPolicy[]>({
    queryKey: ["governance", "policies"],
    queryFn: () => apiClient.get("/api/admin/governance/quota-policies").then((r) => r.data),
  })
  const requests = useQuery<{ total: number; items: AccessRequest[] }>({
    queryKey: ["governance", "requests"],
    queryFn: () => apiClient.get("/api/admin/governance/access-requests", { params: { limit: 300 } }).then((r) => r.data),
  })
  const usage = useQuery<QuotaUsage[]>({
    queryKey: ["governance", "usage"],
    queryFn: () => apiClient.get("/api/admin/governance/quota-usage").then((r) => r.data),
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["governance"] })
  }

  const updatePolicy = useMutation({
    mutationFn: ({ policy, enabled }: { policy: QuotaPolicy; enabled: boolean }) =>
      apiClient.put(`/api/admin/governance/quota-policies/${policy.id}`, {
        enabled,
        limit_value: Number(limitDraft[policy.id] || policy.limit_value),
      }),
    onSuccess: () => { setError(""); refresh() },
    onError: (e) => setError(detailMessage(e, "策略更新失败")),
  })

  const review = useMutation({
    mutationFn: async ({ row, decision, payload }: { row: AccessRequest; decision: "approve" | "reject"; payload?: Record<string, unknown> }) => {
      if (decision === "approve") {
        return apiClient.post(`/api/admin/governance/access-requests/${row.id}/approve`, {
          version: row.version,
          ...payload,
        })
      }
      const comment = window.prompt("请输入拒绝原因")
      if (!comment?.trim()) throw new Error("拒绝时必须填写原因")
      return apiClient.post(`/api/admin/governance/access-requests/${row.id}/reject`, {
        version: row.version,
        review_comment: comment.trim(),
      })
    },
    onSuccess: () => { setError(""); setReviewRow(null); refresh() },
    onError: (e) => setError(detailMessage(e, "审批失败")),
  })

  const refreshing = policies.isFetching || requests.isFetching || usage.isFetching
  const loadError = policies.error || requests.error || usage.error
  const openApproval = (row: AccessRequest) => {
    setReviewRow(row)
    setGrantType(row.request_type)
    setApprovedCount(String(row.request_type === "exact_scope" ? row.paper_ids.length : row.requested_count))
    setReviewComment("同意本次申请")
    setExpiresAt(`${row.business_date}T23:59`)
  }

  const submitApproval = () => {
    if (!reviewRow) return
    review.mutate({
      row: reviewRow,
      decision: "approve",
      payload: {
        grant_type: grantType,
        approved_count: grantType === "exact_scope" ? reviewRow.paper_ids.length : Number(approvedCount),
        review_comment: reviewComment,
        expires_at: appLocalInputToIso(`${expiresAt}:59`),
      },
    })
  }
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <SettingsSectionHeader
        title="额度与申请"
        description="销售人员按北京时间自然日累计；同日重复论文不重复计数。"
        onRefresh={refresh}
        refreshing={refreshing}
      />
      {error && <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}
      {loadError && (
        <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          治理数据加载失败：{detailMessage(loadError, "请确认后端治理接口可用")}
        </div>
      )}

      <Card className="overflow-hidden">
        <div className="flex items-center gap-2 border-b px-4 py-3">
          <ShieldCheck className="size-4" />
          <h2 className="font-medium">额度策略</h2>
        </div>
        <div className="p-4">{(policies.data ?? []).map((policy) => (
          <div key={policy.id} className="flex min-h-12 flex-wrap items-center gap-3 rounded-lg border p-3">
            <Badge variant="secondary">{policy.role === "limited" ? "受限用户" : policy.role}</Badge>
            <span className="text-sm text-muted-foreground">每日不同论文</span>
            <Input
              className="h-8 w-24"
              type="number"
              min={1}
              value={limitDraft[policy.id] ?? String(policy.limit_value)}
              onChange={(e) => setLimitDraft((old) => ({ ...old, [policy.id]: e.target.value }))}
            />
            <span className="text-sm">篇</span>
            <Button
              size="sm"
              variant={policy.enabled ? "destructive" : "default"}
              onClick={() => updatePolicy.mutate({ policy, enabled: !policy.enabled })}
              disabled={updatePolicy.isPending}
            >
              {policy.enabled ? "停用限制" : "启用限制"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => updatePolicy.mutate({ policy, enabled: policy.enabled })}
              disabled={updatePolicy.isPending}
            >保存额度</Button>
            <span className="text-xs text-muted-foreground">时区：{policy.timezone}</span>
          </div>
        ))}</div>
      </Card>

      <Card className="overflow-hidden">
        <div className="border-b px-4 py-3">
          <h2 className="font-medium">超额申请</h2>
          <p className="text-xs text-muted-foreground">默认批准精确论文范围，有效期至申请当日北京时间结束。</p>
        </div>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader><TableRow>
              <TableHead>申请人</TableHead><TableHead>北京时间日期</TableHead><TableHead>动作/范围</TableHead>
              <TableHead>理由</TableHead><TableHead>状态</TableHead><TableHead className="text-right">操作</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {(requests.data?.items ?? []).map((row) => (
                <TableRow key={row.id}>
                  <TableCell><div className="font-medium">{row.requester_name || row.requester_actor_id}</div><div className="text-xs text-muted-foreground">{row.requester_actor_id}</div></TableCell>
                  <TableCell>{row.business_date}</TableCell>
                  <TableCell><div>{row.action}</div><div className="max-w-64 truncate text-xs text-muted-foreground">{row.paper_ids.length ? `论文 ${row.paper_ids.join(", ")}` : `追加 ${row.requested_count} 篇`}</div></TableCell>
                  <TableCell className="max-w-72 whitespace-normal">{row.reason}</TableCell>
                  <TableCell><Badge variant={row.status === "pending" ? "default" : "secondary"}>{STATUS_LABEL[row.status] || row.status}</Badge></TableCell>
                  <TableCell className="text-right">
                    {row.status === "pending" && <div className="flex justify-end gap-1">
                      <Button size="sm" variant="ghost" onClick={() => openApproval(row)}><Check className="mr-1 size-3.5" />批准</Button>
                      <Button size="sm" variant="ghost" className="text-destructive" onClick={() => review.mutate({ row, decision: "reject" })}><X className="mr-1 size-3.5" />拒绝</Button>
                    </div>}
                  </TableCell>
                </TableRow>
              ))}
              {!requests.data?.items?.length && <TableRow><TableCell colSpan={6} className="py-8 text-center text-muted-foreground">暂无申请</TableCell></TableRow>}
            </TableBody>
          </Table>
        </div>
      </Card>
      <Dialog open={Boolean(reviewRow)} onOpenChange={(open) => !open && setReviewRow(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>批准额度申请</DialogTitle>
            <DialogDescription>可按本次论文精确授权，或为申请人追加当日通用额度。</DialogDescription>
          </DialogHeader>
          {reviewRow && <div className="space-y-3">
            <div className="rounded-md bg-muted p-3 text-sm">
              <div>{reviewRow.requester_name || reviewRow.requester_actor_id} · {reviewRow.action}</div>
              <div className="mt-1 text-xs text-muted-foreground">申请 {reviewRow.requested_count} 篇：{reviewRow.reason}</div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-sm"><span>授权方式</span>
                <Select value={grantType} onValueChange={(value) => setGrantType((value || "exact_scope") as "exact_scope" | "daily_extra")}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent><SelectItem value="exact_scope">精确论文范围</SelectItem><SelectItem value="daily_extra">追加当日额度</SelectItem></SelectContent>
                </Select>
              </label>
              <label className="space-y-1 text-sm"><span>批准篇数</span>
                <Input type="number" min={1} value={grantType === "exact_scope" ? reviewRow.paper_ids.length : approvedCount} disabled={grantType === "exact_scope"} onChange={(e) => setApprovedCount(e.target.value)} />
              </label>
            </div>
            <label className="block space-y-1 text-sm"><span>有效期（北京时间）</span><Input type="datetime-local" value={expiresAt} onChange={(e) => setExpiresAt(e.target.value)} /></label>
            <label className="block space-y-1 text-sm"><span>审批意见</span><Input value={reviewComment} onChange={(e) => setReviewComment(e.target.value)} /></label>
            {grantType === "exact_scope" && <div className="max-h-24 overflow-y-auto rounded border p-2 font-mono text-xs text-muted-foreground">{reviewRow.paper_ids.join(", ")}</div>}
          </div>}
          <DialogFooter><Button variant="outline" onClick={() => setReviewRow(null)}>取消</Button><Button onClick={submitApproval} disabled={!expiresAt || review.isPending}>{review.isPending ? "批准中..." : "确认批准"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Card className="overflow-hidden">
        <div className="border-b px-4 py-3"><h2 className="font-medium">近期额度使用</h2></div>
        <div className="overflow-x-auto"><Table>
          <TableHeader><TableRow><TableHead>操作人</TableHead><TableHead>北京时间日期</TableHead><TableHead>基础/追加</TableHead><TableHead>已用</TableHead><TableHead>预占</TableHead><TableHead>更新时间（北京时间）</TableHead></TableRow></TableHeader>
          <TableBody>{(usage.data ?? []).map((row) => <TableRow key={`${row.actor_id}-${row.business_date}`}>
            <TableCell className="font-mono text-xs">{row.actor_id}</TableCell><TableCell>{row.business_date}</TableCell>
            <TableCell>{row.base_limit} / +{row.extra_granted}</TableCell><TableCell>{row.used}</TableCell><TableCell>{row.reserved}</TableCell><TableCell>{fmtApiDate(row.updated_at)}</TableCell>
          </TableRow>)}</TableBody>
        </Table></div>
      </Card>
    </div>
  )
}

export function AuditEventsPanel() {
  const [dateRange, setDateRange] = useState("all")
  const [action, setAction] = useState("all")
  const [result, setResult] = useState("all")
  const rangeDays = dateRange === "7d" ? 6 : dateRange === "30d" ? 29 : dateRange === "90d" ? 89 : null
  const dateFrom = rangeDays == null ? "" : appDateOffset(rangeDays)
  const dateTo = rangeDays == null ? "" : appDateOffset(0)
  const audit = useQuery<{ total: number; items: AuditEvent[] }>({
    queryKey: ["governance", "audit", dateRange, action, result],
    queryFn: () => apiClient.get("/api/admin/governance/audit-events", {
      params: {
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        action: action === "all" ? undefined : action,
        result: result === "all" ? undefined : result,
        limit: 500,
      },
    }).then((r) => r.data),
  })
  return <div className="flex min-w-0 flex-col gap-4">
    <SettingsSectionHeader
      title="行为记录"
      description="必要业务行为审计，所有时间统一显示为北京时间。"
      onRefresh={() => audit.refetch()}
      refreshing={audit.isFetching}
    />
    {audit.error && <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">行为记录加载失败：{detailMessage(audit.error, "请确认后端治理接口可用")}</div>}
    <Card className="flex min-w-0 flex-col gap-2 p-3 sm:flex-row sm:items-center">
      <div className="flex min-w-0 items-center gap-2">
        <Label className="w-10 shrink-0 text-xs text-muted-foreground" htmlFor="audit-date-range">时间</Label>
        <Select value={dateRange} onValueChange={(value) => value && setDateRange(value)}>
          <SelectTrigger id="audit-date-range" className="w-full sm:w-36"><SelectValue placeholder="请选择时间" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部时间</SelectItem>
            <SelectItem value="7d">最近 7 天</SelectItem>
            <SelectItem value="30d">最近 30 天</SelectItem>
            <SelectItem value="90d">最近 90 天</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="flex min-w-0 items-center gap-2">
        <Label className="w-10 shrink-0 text-xs text-muted-foreground" htmlFor="audit-action">操作</Label>
        <Select value={action} onValueChange={(value) => value && setAction(value)}>
          <SelectTrigger id="audit-action" className="w-full sm:w-44"><SelectValue placeholder="请选择操作" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部操作</SelectItem>
            <SelectItem value="paper.view">查看论文</SelectItem>
            <SelectItem value="paper.change">修改论文</SelectItem>
            <SelectItem value="paper.delete">删除论文</SelectItem>
            <SelectItem value="paper.get_pdf">下载 PDF</SelectItem>
            <SelectItem value="paper.export_excel">导出论文</SelectItem>
            <SelectItem value="paper.batch_pdf">批量下载 PDF</SelectItem>
            <SelectItem value="draft.change">修改草稿</SelectItem>
            <SelectItem value="draft.delete">删除草稿</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="flex min-w-0 items-center gap-2">
        <Label className="w-10 shrink-0 text-xs text-muted-foreground" htmlFor="audit-result">结果</Label>
        <Select value={result} onValueChange={(value) => value && setResult(value)}>
          <SelectTrigger id="audit-result" className="w-full sm:w-32"><SelectValue placeholder="请选择结果" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部结果</SelectItem>
            <SelectItem value="success">成功</SelectItem>
            <SelectItem value="failed">失败</SelectItem>
            <SelectItem value="denied">已拒绝</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <span className="ml-auto whitespace-nowrap px-1 text-xs text-muted-foreground">
        共 {audit.data?.total ?? 0} 条
      </span>
    </Card>
    <Card className="hidden min-w-0 overflow-hidden md:block">
      <Table className="table-fixed">
        <colgroup>
          <col className="w-[18%]" />
          <col className="w-[18%]" />
          <col className="w-[25%]" />
          <col className="w-[31%]" />
          <col className="w-[8%]" />
        </colgroup>
        <TableHeader><TableRow>
          <TableHead className="whitespace-normal">时间（北京时间）</TableHead>
          <TableHead className="whitespace-normal">操作人 / 角色</TableHead>
          <TableHead className="whitespace-normal">动作</TableHead>
          <TableHead className="whitespace-normal">资源 / 数量</TableHead>
          <TableHead className="whitespace-normal">结果</TableHead>
        </TableRow></TableHeader>
        <TableBody>{(audit.data?.items ?? []).map((row) => <TableRow key={row.event_id}>
          <TableCell className="whitespace-normal break-words align-top">
            {fmtApiDate(row.occurred_at, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })}
          </TableCell>
          <TableCell className="whitespace-normal break-words align-top">
            <div>{row.display_name || row.actor_id}</div>
            <div className="break-all font-mono text-[10px] text-muted-foreground">{row.actor_id}</div>
            <Badge className="mt-1" variant="outline">{row.role}</Badge>
          </TableCell>
          <TableCell className="whitespace-normal align-top"><AuditActionSummary row={row} /></TableCell>
          <TableCell className="whitespace-normal align-top"><AuditResourceSummary row={row} /></TableCell>
          <TableCell className="whitespace-normal align-top"><AuditResultBadge result={row.result} /></TableCell>
        </TableRow>)}</TableBody>
      </Table>
    </Card>
    <div className="grid gap-3 md:hidden">
      {(audit.data?.items ?? []).map((row) => <Card className="min-w-0 space-y-3 p-3" key={row.event_id}>
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="break-words text-sm font-medium">{row.display_name || row.actor_id}</div>
            <div className="break-all font-mono text-[10px] text-muted-foreground">{row.actor_id} · {row.role}</div>
          </div>
          <AuditResultBadge result={row.result} />
        </div>
        <div className="grid min-w-0 gap-3 text-sm sm:grid-cols-2">
          <div className="min-w-0"><div className="mb-1 text-xs text-muted-foreground">动作</div><AuditActionSummary row={row} /></div>
          <div className="min-w-0"><div className="mb-1 text-xs text-muted-foreground">资源</div><AuditResourceSummary row={row} /></div>
        </div>
        <div className="text-xs text-muted-foreground">
          {fmtApiDate(row.occurred_at, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })}
        </div>
      </Card>)}
    </div>
  </div>
}
