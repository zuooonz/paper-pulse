"use client"

import type { ReactNode } from "react"
import { Eye, Images, Pencil } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { OutboxPanel } from "./outbox-panel"
import { BatchScheduleDialog } from "./batch-schedule-dialog"
import type { DraftDetailView } from "./draft-editor"
import { StatusBadge } from "./draft-list"
import type { Account } from "@/lib/types"

export function WorkspaceDialogs({
  draft,
  selectedDraftIds,
  selectedCount,
  mobileDetailOpen,
  setMobileDetailOpen,
  mobileDetailView,
  setMobileDetailView,
  mobilePublishOpen,
  setMobilePublishOpen,
  mobileOutboxOpen,
  setMobileOutboxOpen,
  scheduleOpen,
  setScheduleOpen,
  scheduledAt,
  setScheduledAt,
  onSchedule,
  schedulePending,
  batchScheduleOpen,
  setBatchScheduleOpen,
  startAt,
  setStartAt,
  onOpenBatchSchedule,
  accounts,
  selectedAccountIds,
  onSelectedAccountIdsChange,
  hasPublishTarget,
  batchSchedulePending,
  onBatchSchedule,
  batchConfirmOpen,
  setBatchConfirmOpen,
  batchPublishPending,
  onBatchPublish,
  canPublish,
  renderEditor,
  renderTargetSelector,
}: {
  draft: { id: number; status: string; paper_internal_id: number | null } | null
  selectedDraftIds: number[]
  selectedCount: number
  mobileDetailOpen: boolean
  setMobileDetailOpen: (open: boolean) => void
  mobileDetailView: DraftDetailView
  setMobileDetailView: (view: DraftDetailView) => void
  mobilePublishOpen: boolean
  setMobilePublishOpen: (open: boolean) => void
  mobileOutboxOpen: boolean
  setMobileOutboxOpen: (open: boolean) => void
  scheduleOpen: boolean
  setScheduleOpen: (open: boolean) => void
  scheduledAt: string
  setScheduledAt: (value: string) => void
  onSchedule: () => void
  schedulePending: boolean
  batchScheduleOpen: boolean
  setBatchScheduleOpen: (open: boolean) => void
  startAt: string
  setStartAt: (value: string) => void
  onOpenBatchSchedule: () => void
  accounts: Account[]
  selectedAccountIds: number[]
  onSelectedAccountIdsChange: (ids: number[]) => void
  hasPublishTarget: boolean
  batchSchedulePending: boolean
  onBatchSchedule: (startAt: string, intervalHours: number, jitterHours: number) => void
  batchConfirmOpen: boolean
  setBatchConfirmOpen: (open: boolean) => void
  batchPublishPending: boolean
  onBatchPublish: () => void
  canPublish: boolean
  renderEditor: (layout: "desktop" | "mobile", view?: DraftDetailView) => ReactNode
  renderTargetSelector: () => ReactNode
}) {
  return (
    <>
      <Dialog open={mobileDetailOpen} onOpenChange={setMobileDetailOpen}>
        <DialogContent className="md:hidden w-[calc(100%-1rem)] max-w-none h-[calc(100dvh-5rem)] p-0 gap-0 overflow-hidden flex flex-col">
          <DialogHeader className="shrink-0 border-b px-4 py-3">
            <DialogTitle className="text-base pr-8">推文详情</DialogTitle>
            {draft && <div className="flex items-center gap-2 text-xs text-muted-foreground"><StatusBadge status={draft.status} /><span>Cite ID {draft.paper_internal_id ?? "-"}</span></div>}
            <div className="grid grid-cols-3 gap-1.5 pt-1">
              <Button type="button" size="sm" variant={mobileDetailView === "edit" ? "default" : "outline"} className="h-8 rounded-lg text-xs" onClick={() => setMobileDetailView("edit")}><Pencil className="w-3.5 h-3.5 mr-1" />编辑发布</Button>
              <Button type="button" size="sm" variant={mobileDetailView === "preview" ? "default" : "outline"} className="h-8 rounded-lg text-xs" onClick={() => setMobileDetailView("preview")}><Eye className="w-3.5 h-3.5 mr-1" />预览</Button>
              <Button type="button" size="sm" variant={mobileDetailView === "images" ? "default" : "outline"} className="h-8 rounded-lg text-xs" onClick={() => setMobileDetailView("images")}><Images className="w-3.5 h-3.5 mr-1" />配图</Button>
            </div>
          </DialogHeader>
          {!draft ? <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">正在加载…</div> : (
            <div className="flex flex-1 flex-col overflow-y-auto scrollbar-none p-4 pb-0">{renderEditor("mobile", mobileDetailView)}</div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={mobilePublishOpen} onOpenChange={setMobilePublishOpen}>
        <DialogContent className="md:hidden w-[calc(100%-1rem)] rounded-xl">
          <DialogHeader><DialogTitle>发布推文</DialogTitle></DialogHeader>
          <div className="space-y-4 py-1">
            <div className="rounded-lg bg-muted/40 px-3 py-2 text-xs">已选择 <b>{selectedCount}</b> 篇推文</div>
            {renderTargetSelector()}
          </div>
          <DialogFooter className="grid grid-cols-2 gap-2 sm:flex">
            <Button variant="outline" disabled={!canPublish || selectedCount === 0 || !hasPublishTarget} onClick={() => { setMobilePublishOpen(false); onOpenBatchSchedule() }}>定时发布</Button>
            <Button disabled={!canPublish || selectedCount === 0 || !hasPublishTarget || batchPublishPending} onClick={onBatchPublish}>{batchPublishPending ? "发布中…" : "立即发布"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={mobileOutboxOpen} onOpenChange={setMobileOutboxOpen}>
        <DialogContent className="md:hidden w-[calc(100%-1rem)] max-w-none h-[min(75dvh,42rem)] p-0 gap-0 overflow-hidden flex flex-col">
          <DialogHeader className="shrink-0 border-b px-4 py-3"><DialogTitle>推文发件箱</DialogTitle></DialogHeader>
          <OutboxPanel compact />
        </DialogContent>
      </Dialog>

      <Dialog open={scheduleOpen} onOpenChange={setScheduleOpen}>
        <DialogContent className="max-h-[85dvh] overflow-y-auto scrollbar-none sm:max-w-md">
          <DialogHeader className="pr-6"><DialogTitle>定时发布</DialogTitle></DialogHeader>
          <div className="flex flex-col gap-6 py-1">
            <div className="space-y-2"><Label>发布时间</Label><Input type="datetime-local" value={scheduledAt} onChange={(event) => setScheduledAt(event.target.value)} onMouseLeave={(event) => event.currentTarget.blur()} className="h-9 text-sm" /></div>
            {renderTargetSelector()}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setScheduleOpen(false)}>取消</Button>
            <Button variant="default" disabled={!scheduledAt || !hasPublishTarget || schedulePending} onClick={onSchedule}>{schedulePending ? "设置中…" : "确认定时"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <BatchScheduleDialog
        open={batchScheduleOpen}
        onOpenChange={setBatchScheduleOpen}
        startAt={startAt}
        onStartAtChange={setStartAt}
        draftIds={selectedDraftIds}
        accounts={accounts}
        selectedAccountIds={selectedAccountIds}
        onSelectedAccountIdsChange={onSelectedAccountIdsChange}
        hasPublishTarget={hasPublishTarget}
        isPending={batchSchedulePending}
        onSubmit={onBatchSchedule}
      />

      <Dialog open={batchConfirmOpen} onOpenChange={setBatchConfirmOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader className="pr-6"><DialogTitle>确认批量发布</DialogTitle></DialogHeader>
          <div className="space-y-3 py-1">
            <p className="text-sm text-muted-foreground">短时间在同一平台连续发布多篇推文可能触发平台限制</p>
            <p className="text-sm">即将发布 <span className="font-semibold">{selectedCount}</span> 篇推文到所选账号，是否继续？</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBatchConfirmOpen(false)}>取消发布</Button>
            <Button variant="default" disabled={batchPublishPending} onClick={() => { setBatchConfirmOpen(false); onBatchPublish() }}>{batchPublishPending ? "发布中…" : "确认发布"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
