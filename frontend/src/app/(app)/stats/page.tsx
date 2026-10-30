"use client"

import { useQuery } from "@tanstack/react-query"
import { apiClient } from "@/lib/api"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { QueryErrorNotice } from "@/components/query-error-notice"
import { getApiErrorMessage } from "@/lib/api-errors"
import { channelName } from "@/lib/channels"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

interface PublishRecord {
  id: number
  draft_id: number
  draft_title?: string
  channel: string
  account_name: string
  status: string
  url: string | null
  error: string | null
  published_at: string | null
}

interface ByChannel {
  channel: string
  account_name: string
  count: number
}

interface DailyTrend {
  date: string
  count: number
}

interface PublishStats {
  total: number
  success: number
  failed: number
  scheduled: number
  by_channel: ByChannel[]
  daily_trend: DailyTrend[]
  recent_records: PublishRecord[]
}

interface DraftStats {
  draft?: number
  approved?: number
  rejected?: number
  published?: number
  total?: number
  distribution?: Partial<Record<"draft" | "approved" | "rejected" | "published", number>>
}

function statusColor(status: string) {
  if (status === "success") return "text-green-600 dark:text-green-400"
  if (status === "failed") return "text-red-500 dark:text-red-400"
  if (status === "scheduled") return "text-yellow-600 dark:text-yellow-400"
  return "text-muted-foreground"
}

function statusLabel(status: string) {
  if (status === "success") return "成功"
  if (status === "failed") return "失败"
  if (status === "scheduled") return "待发"
  return status
}

import { fmtApiDate } from "@/lib/datetime"

function thisWeekCount(trend: DailyTrend[]) {
  const now = new Date()
  const weekAgo = new Date(now)
  weekAgo.setDate(now.getDate() - 7)
  return trend
    .filter((d) => new Date(d.date) >= weekAgo)
    .reduce((sum, d) => sum + d.count, 0)
}

function KpiCard({
  label,
  value,
  valueClass = "",
}: {
  label: string
  value: number | string
  valueClass?: string
}) {
  return (
    <Card>
      <CardContent className="flex flex-col items-start gap-1 pt-4">
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className={`text-3xl font-bold ${valueClass}`}>{value}</span>
      </CardContent>
    </Card>
  )
}

function StatusCard({
  label,
  value,
  color,
}: {
  label: string
  value: number
  color: string
}) {
  return (
    <div className={`rounded-xl p-3 flex flex-col gap-1 ${color}`}>
      <span className="text-xs font-medium">{label}</span>
      <span className="text-2xl font-bold">{value}</span>
    </div>
  )
}

export default function StatsPage() {
  const {
    data: pub,
    isLoading: pubLoading,
    isError: isPubError,
    isRefetchError: isPubRefetchError,
    error: pubError,
    refetch: refetchPub,
    isFetching: isPubFetching,
  } = useQuery<PublishStats>({
    queryKey: ["stats", "publish"],
    queryFn: () => apiClient.get("/api/stats/publish").then((r) => r.data),
  })

  const {
    data: drafts,
    isLoading: draftsLoading,
    isError: isDraftsError,
    isRefetchError: isDraftsRefetchError,
    error: draftsError,
    refetch: refetchDrafts,
    isFetching: isDraftsFetching,
  } = useQuery<DraftStats>({
    queryKey: ["stats", "drafts"],
    queryFn: () => apiClient.get("/api/stats/drafts").then((r) => r.data),
  })

  const successRate =
    pub && pub.total > 0 ? Math.round((pub.success / pub.total) * 100) : 0

  const draftCounts = {
    draft: drafts?.draft ?? drafts?.distribution?.draft ?? 0,
    approved: drafts?.approved ?? drafts?.distribution?.approved ?? 0,
    rejected: drafts?.rejected ?? drafts?.distribution?.rejected ?? 0,
    published: drafts?.published ?? drafts?.distribution?.published ?? 0,
  }

  const maxDailyCount = pub?.daily_trend?.length
    ? Math.max(...pub.daily_trend.map((d) => d.count), 1)
    : 1

  const maxChannelCount = pub?.by_channel?.length
    ? Math.max(...pub.by_channel.map((d) => d.count), 1)
    : 1

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3 pb-[calc(1.5rem+env(safe-area-inset-bottom,0px))] scrollbar-none md:p-6">
        <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <h1 className="text-lg font-semibold">发布统计</h1>

      {(isPubError || isPubRefetchError) && (
        <QueryErrorNotice
          message={`发布统计加载失败：${getApiErrorMessage(pubError, "请稍后重试")}`}
          onRetry={() => { void refetchPub() }}
          retrying={isPubFetching}
        />
      )}
      {(isDraftsError || isDraftsRefetchError) && (
        <QueryErrorNotice
          message={`草稿统计加载失败：${getApiErrorMessage(draftsError, "请稍后重试")}`}
          onRetry={() => { void refetchDrafts() }}
          retrying={isDraftsFetching}
        />
      )}

      {/* KPI row */}
      {(pub || pubLoading) && <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        {pubLoading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))
        ) : (
          <>
            <KpiCard label="总发布" value={pub?.total ?? 0} />
            <KpiCard
              label="成功"
              value={pub?.success ?? 0}
              valueClass="text-green-600 dark:text-green-400"
            />
            <KpiCard label="成功率" value={`${successRate}%`} valueClass="text-primary" />
            <KpiCard label="本周新增" value={thisWeekCount(pub?.daily_trend ?? [])} />
          </>
        )}
      </div>}

      {/* Draft status row */}
      {(drafts || draftsLoading) && <div>
        <h2 className="text-sm font-medium mb-3">草稿状态分布</h2>
        {draftsLoading ? (
          <Skeleton className="h-16 rounded-xl" />
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <StatusCard
              label="草稿"
              value={draftCounts.draft}
              color="bg-muted text-muted-foreground"
            />
            <StatusCard
              label="已审核"
              value={draftCounts.approved}
              color="bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
            />
            <StatusCard
              label="已拒绝"
              value={draftCounts.rejected}
              color="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
            />
            <StatusCard
              label="已发布"
              value={draftCounts.published}
              color="bg-primary/10 text-primary"
            />
          </div>
        )}
      </div>}

      {/* By channel bar chart */}
      {(pub || pubLoading) && <Card>
        <CardHeader>
          <CardTitle>各渠道发布量</CardTitle>
        </CardHeader>
        <CardContent>
          {pubLoading ? (
            <Skeleton className="h-32" />
          ) : pub?.by_channel?.length ? (
            <div className="flex flex-col gap-2">
              {pub.by_channel.map((ch, i) => (
                <div key={i} className="flex items-center gap-3">
                  <span className="w-20 md:w-32 shrink-0 text-xs text-muted-foreground truncate">
                    {channelName(ch.channel)}
                    {ch.account_name ? ` · ${ch.account_name}` : ""}
                  </span>
                  <div className="flex-1 h-5 bg-muted rounded overflow-hidden">
                    <div
                      className="h-full bg-primary/70 rounded transition-all"
                      style={{
                        width: `${Math.max(2, (ch.count / maxChannelCount) * 100)}%`,
                      }}
                    />
                  </div>
                  <span className="w-8 text-right text-xs font-medium">{ch.count}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">暂无数据</p>
          )}
        </CardContent>
      </Card>}

      {/* Daily trend */}
      {(pub || pubLoading) && <Card>
        <CardHeader>
          <CardTitle>近30天发布趋势</CardTitle>
        </CardHeader>
        <CardContent>
          {pubLoading ? (
            <Skeleton className="h-28" />
          ) : pub?.daily_trend?.some((d) => d.count > 0) ? (
            <div className="flex items-end gap-[2px] h-28">
              {pub.daily_trend.map((d, i) => (
                <div
                  key={i}
                  className="flex-1 h-full flex flex-col items-center justify-end group"
                  title={`${d.date}: ${d.count}`}
                >
                  <div
                    className="w-full bg-primary/60 rounded-t-sm transition-all group-hover:bg-primary"
                    style={{
                      height: `${Math.max(2, (d.count / maxDailyCount) * 100)}%`,
                    }}
                  />
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">暂无数据</p>
          )}
        </CardContent>
      </Card>}

      {/* Recent records */}
      {(pub || pubLoading) && <Card>
        <CardHeader>
          <CardTitle>最近发布记录</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {pubLoading ? (
            <Skeleton className="h-48 m-4 rounded-xl" />
          ) : pub?.recent_records?.length ? (
            <div className="overflow-x-auto scrollbar-none">
            <Table className="min-w-[640px] md:min-w-0">
              <TableHeader>
                <TableRow>
                  <TableHead>标题</TableHead>
                  <TableHead>渠道</TableHead>
                  <TableHead>账号</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>链接</TableHead>
                  <TableHead>发布时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pub.recent_records.map((rec) => {
                  const title = rec.draft_title ?? "（无标题）"
                  return (
                  <TableRow key={rec.id}>
                    <TableCell
                      className="max-w-[180px] truncate"
                      title={title}
                    >
                      {title}
                    </TableCell>
                    <TableCell>
                      {channelName(rec.channel)}
                    </TableCell>
                    <TableCell>{rec.account_name}</TableCell>
                    <TableCell>
                      <span className={`font-medium ${statusColor(rec.status)}`}>
                        {statusLabel(rec.status)}
                      </span>
                      {rec.error && (
                        <span
                          className="ml-1 text-xs text-muted-foreground cursor-help"
                          title={rec.error}
                        >
                          ⚠
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      {rec.url ? (
                        <a
                          href={rec.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary underline text-xs"
                        >
                          查看
                        </a>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {fmtApiDate(rec.published_at)}
                    </TableCell>
                  </TableRow>
                  )
                })}
              </TableBody>
            </Table>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground p-4">暂无发布记录</p>
          )}
        </CardContent>
      </Card>}
        </div>
      </div>
    </div>
  )
}
