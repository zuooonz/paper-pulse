"use client"

import { useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { PublishTargetSelector } from "./publish-target-selector"
import type { Account } from "@/lib/types"
import { appLocalInputToIso, fmtApiDate } from "@/lib/datetime"

export function BatchScheduleDialog({
  open,
  onOpenChange,
  startAt,
  onStartAtChange,
  draftIds,
  accounts,
  selectedAccountIds,
  onSelectedAccountIdsChange,
  hasPublishTarget,
  isPending,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  startAt: string
  onStartAtChange: (value: string) => void
  draftIds: number[]
  accounts: Account[]
  selectedAccountIds: number[]
  onSelectedAccountIdsChange: (ids: number[]) => void
  hasPublishTarget: boolean
  isPending: boolean
  onSubmit: (startAt: string, intervalHours: number, jitterHours: number) => void
}) {
  const [intervalHours, setIntervalHours] = useState(24)
  const [jitterHours, setJitterHours] = useState(2)

  const preview = useMemo(() => {
    if (!startAt) return []
    const first = new Date(appLocalInputToIso(startAt))
    return draftIds.map((id, index) => ({
      id,
      time: new Date(first.getTime() + index * intervalHours * 60 * 60_000),
    }))
  }, [startAt, draftIds, intervalHours])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85dvh] overflow-y-auto scrollbar-none sm:max-w-lg">
        <DialogHeader className="pr-6"><DialogTitle>批量定时发布 ({draftIds.length} 篇)</DialogTitle></DialogHeader>
        <div className="flex flex-col gap-6 py-1">
          <div className="space-y-2">
            <Label>开始时间</Label>
            <Input type="datetime-local" value={startAt} onChange={(event) => onStartAtChange(event.target.value)} onMouseLeave={(event) => event.currentTarget.blur()} className="h-9 text-sm" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label>间隔时间（小时）</Label>
              <Input type="number" min={0} step={0.5} value={intervalHours} onChange={(event) => setIntervalHours(Number(event.target.value))} className="h-9 text-sm" />
            </div>
            <div className="space-y-2">
              <Label>随机抖动（小时）</Label>
              <Input type="number" min={0} step={0.5} value={jitterHours} onChange={(event) => setJitterHours(Number(event.target.value))} className="h-9 text-sm" />
            </div>
          </div>
          <PublishTargetSelector
            label="发布渠道"
            accounts={accounts}
            selected={selectedAccountIds}
            onChange={onSelectedAccountIdsChange}
            disabled={false}
          />
          {preview.length > 0 && (
            <div className="bg-muted/50 rounded-lg p-3 text-xs">
              <p className="font-semibold mb-1">预计发布时间（约）</p>
              <p className="text-muted-foreground mb-2">预览显示基准时间，实际安排会按随机间隔调整。</p>
              <div className="flex flex-col gap-1 max-h-32 overflow-y-auto">
                {preview.map(({ id, time }, index) => (
                  <div key={id} className="flex justify-between">
                    <span className="truncate flex-1">#{index + 1} Draft {id}</span>
                    <span className="text-muted-foreground shrink-0">约 {fmtApiDate(time.toISOString(), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
          <Button variant="default" disabled={!startAt || !hasPublishTarget || isPending} onClick={() => onSubmit(startAt, intervalHours, jitterHours)}>{isPending ? "设置中…" : "确认批量定时"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
