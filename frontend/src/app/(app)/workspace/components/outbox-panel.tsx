"use client"

import { BarChart3 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { QueryErrorNotice } from "@/components/query-error-notice"
import { useCancelOutboxRecord, useClearOutbox, useOutbox } from "@/hooks/use-drafts"
import type { OutboxAccount, OutboxItem } from "@/lib/types"
import { channelName } from "@/lib/channels"
import { fmtApiDate } from "@/lib/datetime"
import { getApiErrorMessage } from "@/lib/api-errors"

const OUTBOX_STATUS: Record<string, { label: string; color: string; canCancel: boolean }> = {
  success: { label: "成功", color: "text-green-600", canCancel: false },
  failed: { label: "失败", color: "text-red-600", canCancel: false },
  publishing: { label: "发布中", color: "text-blue-600", canCancel: true },
  pending: { label: "排队中", color: "text-amber-600", canCancel: true },
  cancelled: { label: "已取消", color: "text-orange-500", canCancel: false },
  scheduled: { label: "定时", color: "text-muted-foreground", canCancel: true },
}

function getOutboxStatus(status: string, scheduledAt: string | null, compact: boolean) {
  const meta = OUTBOX_STATUS[status]
  if (!meta) return { label: compact ? "已取消" : "定时", color: "text-muted-foreground", canCancel: false }
  if (status === "scheduled") {
    return { ...meta, label: compact ? "已定时" : scheduledAt ? `定时 ${fmtApiDate(scheduledAt)}` : "定时" }
  }
  return meta
}

export function OutboxRecordRow({
  account,
  scheduledAt,
  compact = false,
  cancelPending = false,
  onCancel,
}: {
  account: OutboxAccount
  scheduledAt: string | null
  compact?: boolean
  cancelPending?: boolean
  onCancel?: () => void
}) {
  const status = getOutboxStatus(account.status, scheduledAt, compact)
  if (compact) {
    return (
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="truncate">{channelName(account.channel)} · {account.account_name}</span>
        <span className={status.color}>{status.label}</span>
      </div>
    )
  }

  const timeText = account.published_at ? fmtApiDate(account.published_at) : ""
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="flex items-center gap-1.5 min-w-0 overflow-hidden">
        <span className="truncate max-w-[80px]">{account.account_name}</span>
        {timeText && <span className="text-muted-foreground/60 shrink-0">{timeText}</span>}
      </div>
      <div className="flex items-center gap-1.5 shrink-0">
        <span className={status.color}>{status.label}</span>
        {account.url && <a href={account.url} target="_blank" rel="noreferrer" className="text-blue-500 hover:underline">链接</a>}
        {account.error && <Button variant="ghost" size="sm" className="h-4 px-1 text-[10px] text-red-500" onClick={() => alert(`${channelName(account.channel)}: ${account.error}`)}>详情</Button>}
        {status.canCancel && onCancel && (
          <Button
            variant="ghost"
            size="sm"
            className="h-4 px-1 text-[10px] text-destructive"
            disabled={cancelPending}
            onClick={() => {
              const target = `${channelName(account.channel)} / ${account.account_name}`
              if (window.confirm(`取消 ${target} 的发布任务？`)) onCancel()
            }}
          >取消</Button>
        )}
      </div>
    </div>
  )
}

function DesktopOutboxRecord({
  task,
  cancelPending,
  onCancel,
}: {
  task: OutboxItem
  cancelPending: boolean
  onCancel: (recordId: number) => void
}) {
  const grouped = task.accounts.reduce<Record<string, OutboxAccount[]>>((groups, account) => {
    if (!groups[account.channel]) groups[account.channel] = []
    groups[account.channel].push(account)
    return groups
  }, {})
  return (
    <div className="text-xs bg-background rounded px-2.5 py-2 border">
      <div className="truncate font-medium text-[13px]">{task.draft_title || `Draft #${task.draft_id}`}</div>
      <div className="mt-1.5 flex flex-col gap-2">
        {Object.entries(grouped).map(([channel, accounts]) => (
          <div key={channel} className="flex items-start gap-2 text-[11px]">
            <span className="text-muted-foreground shrink-0 w-20 pt-0.5">{channelName(channel)}</span>
            <div className="flex flex-col gap-1 flex-1 min-w-0">
              {accounts.map((account) => (
                <OutboxRecordRow
                  key={account.record_id}
                  account={account}
                  scheduledAt={task.scheduled_at}
                  cancelPending={cancelPending}
                  onCancel={() => onCancel(account.record_id)}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export function OutboxPanel({ compact = false, onShowStats }: { compact?: boolean; onShowStats?: () => void }) {
  const outbox = useOutbox()
  const cancel = useCancelOutboxRecord()
  const clear = useClearOutbox()
  const tasks = outbox.data ?? []
  const hasClearable = tasks.some((task) => task.accounts.some((account) => ["success", "failed", "cancelled"].includes(account.status)))
  const hasPublishing = tasks.some((task) => task.accounts.some((account) => account.status === "publishing"))
  const hasPending = tasks.some((task) => task.accounts.some((account) => account.status === "pending"))

  if (compact) {
    return (
      <div className="flex-1 overflow-y-auto scrollbar-none p-3 space-y-2">
        {(outbox.isError || outbox.isRefetchError) && <QueryErrorNotice className="p-3" message={`发件箱加载失败：${getApiErrorMessage(outbox.error, "请稍后重试")}`} onRetry={() => { void outbox.refetch() }} retrying={outbox.isFetching} />}
        {outbox.isLoading && !outbox.data ? <p className="py-10 text-center text-sm text-muted-foreground">加载中…</p> : !outbox.data && (outbox.isError || outbox.isRefetchError) ? null : !tasks.length ? <p className="py-10 text-center text-sm text-muted-foreground">暂无发布任务</p> : tasks.map((task) => (
          <div key={task.draft_id} className="rounded-lg border p-3 space-y-2">
            <div className="text-sm font-medium line-clamp-2">{task.draft_title || `Draft #${task.draft_id}`}</div>
            <div className="space-y-1">{task.accounts.map((account) => <OutboxRecordRow key={account.record_id} account={account} scheduledAt={task.scheduled_at} compact />)}</div>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="border rounded-lg p-3 bg-muted/30">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <p className="text-xs font-semibold">发件箱</p>
          {onShowStats && <Button variant="ghost" size="sm" className="h-5 px-1.5 text-[10px] text-muted-foreground hover:text-primary" onClick={onShowStats}><BarChart3 className="w-3 h-3 mr-0.5" />统计</Button>}
          {hasPublishing && <span className="text-[10px] text-blue-600">发布中</span>}
          {hasPending && <span className="text-[10px] text-amber-600">排队中</span>}
        </div>
        <div className="flex items-center gap-2">
          {hasClearable && <Button variant="ghost" size="sm" className="h-5 px-1.5 text-[10px] text-muted-foreground hover:text-destructive" disabled={clear.isPending} onClick={() => { if (window.confirm("清空发件箱？发布中/定时任务将保留。")) clear.mutate() }}>清空记录</Button>}
          <span className="text-xs text-muted-foreground">{tasks.length} 篇</span>
        </div>
      </div>
      {(outbox.isError || outbox.isRefetchError) && <QueryErrorNotice className="mb-2 p-3" message={`发件箱加载失败：${getApiErrorMessage(outbox.error, "请稍后重试")}`} onRetry={() => { void outbox.refetch() }} retrying={outbox.isFetching} />}
      {outbox.isLoading && !outbox.data ? <p className="text-xs text-muted-foreground">加载中…</p> : !outbox.data && (outbox.isError || outbox.isRefetchError) ? null : !tasks.length ? <p className="text-xs text-muted-foreground">暂无任务</p> : (
        <div className="flex flex-col gap-2">
          {tasks.map((task) => <DesktopOutboxRecord key={task.draft_id} task={task} cancelPending={cancel.isPending} onCancel={(id) => cancel.mutate(id)} />)}
        </div>
      )}
    </div>
  )
}
