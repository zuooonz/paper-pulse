"use client"

import { useQuery } from "@tanstack/react-query"
import { apiClient } from "@/lib/api"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { fmtApiDate } from "@/lib/datetime"
import { channelName } from "@/lib/channels"

// ── Types ──────────────────────────────────────────────────────────

export interface PublishStats {
  total: number
  success: number
  failed: number
  scheduled: number
  by_channel: { channel: string; account_name: string; count: number }[]
  daily_trend: { date: string; count: number }[]
  recent_records: {
    id: number
    draft_id?: number
    draft_title?: string
    video_id?: number
    channel: string
    account_name: string
    status: string
    url: string | null
    error: string | null
    scheduled_at: string | null
    published_at: string | null
    created_at: string | null
  }[]
}

// ── Helpers ────────────────────────────────────────────────────────

function statusColor(s: string) {
  if (s === "success") return "text-green-600"
  if (s === "failed") return "text-red-500"
  if (s === "scheduled") return "text-yellow-600"
  return "text-muted-foreground"
}

function statusLabel(s: string) {
  if (s === "success") return "成功"
  if (s === "failed") return "失败"
  if (s === "scheduled") return "待发"
  return s
}

// ── Component ──────────────────────────────────────────────────────

interface StatsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  type: "tweet" | "video"
  title: string
  pageUrl?: string  // optional link to full stats page
}

export function StatsDialog({ open, onOpenChange, type, title }: StatsDialogProps) {
  const { data: pub } = useQuery<PublishStats>({
    queryKey: ["stats-dialog", type],
    queryFn: () => {
      const endpoint = type === "tweet" ? "/api/stats/publish" : "/api/stats/video-publish"
      return apiClient.get(endpoint).then((r) => r.data)
    },
    enabled: open,
    staleTime: 30_000,
  })

  const total = pub?.total ?? 0
  const success = pub?.success ?? 0
  const successRate = total > 0 ? Math.round((success / total) * 100) : 0

  const maxDaily = pub?.daily_trend?.length ? Math.max(...pub.daily_trend.map((d) => d.count), 1) : 1
  const maxCh = pub?.by_channel?.length ? Math.max(...pub.by_channel.map((d) => d.count), 1) : 1

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85dvh] overflow-y-auto scrollbar-none sm:max-w-2xl">
        <DialogHeader className="pr-6">
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-6 py-1">
          {/* KPI row */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Card>
              <CardContent className="flex flex-col items-start gap-1 pt-4 pb-3">
                <span className="text-xs text-muted-foreground">总发布</span>
                <span className="text-2xl font-bold">{total}</span>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex flex-col items-start gap-1 pt-4 pb-3">
                <span className="text-xs text-muted-foreground">成功</span>
                <span className="text-2xl font-bold text-green-600">{success}</span>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex flex-col items-start gap-1 pt-4 pb-3">
                <span className="text-xs text-muted-foreground">失败</span>
                <span className="text-2xl font-bold text-red-500">{pub?.failed ?? 0}</span>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex flex-col items-start gap-1 pt-4 pb-3">
                <span className="text-xs text-muted-foreground">成功率</span>
                <span className="text-2xl font-bold text-primary">{successRate}%</span>
              </CardContent>
            </Card>
          </div>

          {/* By channel */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">各渠道发布量</CardTitle>
            </CardHeader>
            <CardContent className="min-h-[48px]">
              {pub?.by_channel?.length ? (
                <div className="flex flex-col gap-1.5">
                  {pub.by_channel.map((ch, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <span className="w-28 shrink-0 text-xs text-muted-foreground truncate">
                        {channelName(ch.channel)}
                        {ch.account_name ? ` · ${ch.account_name}` : ""}
                      </span>
                      <div className="flex-1 h-4 bg-muted rounded overflow-hidden">
                        <div className="h-full bg-primary/70 rounded" style={{ width: `${Math.max(2, (ch.count / maxCh) * 100)}%` }} />
                      </div>
                      <span className="w-7 text-right text-xs font-medium">{ch.count}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">{pub ? "暂无数据" : "加载中…"}</p>
              )}
            </CardContent>
          </Card>

          {/* Daily trend */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">发布趋势</CardTitle>
            </CardHeader>
            <CardContent className="min-h-[60px]">
              {pub?.daily_trend?.some((d) => d.count > 0) ? (
                <div className="flex items-end gap-[2px] h-20">
                  {pub.daily_trend.map((d, i) => (
                    <div key={i} className="flex-1 h-full flex flex-col items-center justify-end group" title={`${d.date}: ${d.count}`}>
                      <div className="w-full bg-primary/60 rounded-t-sm group-hover:bg-primary" style={{ height: `${Math.max(2, (d.count / maxDaily) * 100)}%` }} />
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">{pub ? "暂无数据" : "加载中…"}</p>
              )}
            </CardContent>
          </Card>

          {/* Recent records */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">最近发布记录</CardTitle>
            </CardHeader>
            <CardContent className="p-0 min-h-[48px]">
              {pub?.recent_records?.length ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">ID</TableHead>
                      <TableHead className="text-xs">渠道</TableHead>
                      <TableHead className="text-xs">账号</TableHead>
                      <TableHead className="text-xs">状态</TableHead>
                      <TableHead className="text-xs">链接</TableHead>
                      <TableHead className="text-xs">时间</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pub.recent_records.map((rec) => (
                      <TableRow key={rec.id}>
                        <TableCell className="text-xs text-muted-foreground">{type === "video" ? rec.video_id : rec.draft_id}</TableCell>
                        <TableCell className="text-xs">{channelName(rec.channel)}</TableCell>
                        <TableCell className="text-xs">{rec.account_name}</TableCell>
                        <TableCell className="text-xs">
                          <span className={`font-medium ${statusColor(rec.status)}`}>{statusLabel(rec.status)}</span>
                          {rec.error && <span className="ml-1 cursor-help" title={rec.error}>⚠</span>}
                        </TableCell>
                        <TableCell className="text-xs">
                          {rec.url ? <a href={rec.url} target="_blank" rel="noopener noreferrer" className="text-primary underline">查看</a> : "—"}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">{fmtApiDate(rec.published_at) || fmtApiDate(rec.created_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <p className="text-xs text-muted-foreground p-4">{pub ? "暂无发布记录" : "加载中…"}</p>
              )}
            </CardContent>
          </Card>
        </div>
      </DialogContent>
    </Dialog>
  )
}
