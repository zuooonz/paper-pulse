"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useSession } from "next-auth/react"
import { useRouter, useSearchParams } from "next/navigation"
import { StatsDialog } from "@/components/stats-dialog"
import { useDevice } from "@/hooks/use-device"
import { useAccounts } from "@/hooks/use-accounts"
import {
  useBatchPublish,
  useBatchSchedule,
  useDeleteDraft,
  usePublishDraft,
  useScheduleDraft,
} from "@/hooks/use-drafts"
import { useDraftEditor } from "./hooks/use-draft-editor"
import { DraftList } from "./components/draft-list"
import { DraftEditor, DraftPreviewPane } from "./components/draft-editor"
import type { DraftDetailView } from "./components/draft-editor"
import { OutboxPanel } from "./components/outbox-panel"
import { PublishTargetSelector } from "./components/publish-target-selector"
import { WorkspaceDialogs } from "./components/workspace-dialogs"
import { appLocalInputToIso, fmtApiDate, nextHourInAppTimezone } from "@/lib/datetime"
import { getApiErrorMessage } from "@/lib/api-errors"

function ResizableSplitter({ onResize }: { onResize: (delta: number) => void }) {
  const [dragging, setDragging] = useState(false)
  useEffect(() => {
    if (!dragging) return
    const handleMove = (event: MouseEvent) => onResize(event.movementX)
    const handleUp = () => setDragging(false)
    document.addEventListener("mousemove", handleMove)
    document.addEventListener("mouseup", handleUp)
    document.body.style.cursor = "col-resize"
    document.body.style.userSelect = "none"
    return () => {
      document.removeEventListener("mousemove", handleMove)
      document.removeEventListener("mouseup", handleUp)
      document.body.style.cursor = ""
      document.body.style.userSelect = ""
    }
  }, [dragging, onResize])
  return <div className="w-2 cursor-col-resize flex items-center justify-center hover:bg-primary/10 active:bg-primary/20 transition-colors z-10 -mx-0.5" onMouseDown={() => setDragging(true)}><div className="w-px h-8 bg-border rounded-full" /></div>
}

export default function WorkspacePage() {
  const workspaceRef = useRef<HTMLDivElement>(null)
  const publishClickLock = useRef(false)
  const { isMobile } = useDevice()
  const { data: session } = useSession()
  const role = session?.user?.role ?? "viewer"
  const canPublish = role === "admin" || role === "reviewer"
  const router = useRouter()
  useEffect(() => {
    if (role === "limited") router.replace("/papers")
  }, [role, router])

  const searchParams = useSearchParams()
  const urlDraftId = searchParams?.get("draft")
  const [selectedId, setSelectedId] = useState<number | null>(urlDraftId ? parseInt(urlDraftId) : null)
  const [selectedAccountIds, setSelectedAccountIds] = useState<number[]>([])
  const [checkedDraftIds, setCheckedDraftIds] = useState<number[]>([])
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false)
  const [mobileDetailView, setMobileDetailView] = useState<DraftDetailView>("edit")
  const [mobilePublishOpen, setMobilePublishOpen] = useState(false)
  const [mobileOutboxOpen, setMobileOutboxOpen] = useState(false)
  const [leftWidth, setLeftWidth] = useState(288)
  const [rightWidth, setRightWidth] = useState(512)
  const [workspaceWidth, setWorkspaceWidth] = useState(0)

  useEffect(() => {
    const element = workspaceRef.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => setWorkspaceWidth(Math.round(entry.contentRect.width)))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const showDesktopPreview = workspaceWidth >= 1000
  const paneGutters = showDesktopPreview ? 16 : 8
  const minLeftWidth = workspaceWidth > 0 && workspaceWidth < 700 ? 180 : 220
  const effectiveRightWidth = showDesktopPreview
    ? Math.max(300, Math.min(rightWidth, workspaceWidth * 0.38, workspaceWidth - minLeftWidth - 360 - paneGutters))
    : 0
  const effectiveLeftWidth = workspaceWidth > 0
    ? Math.max(minLeftWidth, Math.min(leftWidth, workspaceWidth - effectiveRightWidth - 360 - paneGutters))
    : leftWidth

  const editor = useDraftEditor(selectedId)
  const { saveDraft, resetSnapshot, setSelectedImagePaths } = editor
  const { data: accountData = [] } = useAccounts()
  const accounts = accountData
  const hasPublishTarget = selectedAccountIds.length > 0
  const publishDraft = usePublishDraft()
  const batchPublish = useBatchPublish()
  const deleteDraft = useDeleteDraft()
  const scheduleDraft = useScheduleDraft()
  const batchSchedule = useBatchSchedule()
  const [scheduleOpen, setScheduleOpen] = useState(false)
  const [batchScheduleOpen, setBatchScheduleOpen] = useState(false)
  const [scheduledAt, setScheduledAt] = useState("")
  const [startAt, setStartAt] = useState("")
  const [batchConfirmOpen, setBatchConfirmOpen] = useState(false)
  const [tweetStatsOpen, setTweetStatsOpen] = useState(false)
  const isMutating = editor.patchDraft.isPending || publishDraft.isPending || batchPublish.isPending || deleteDraft.isPending || scheduleDraft.isPending || batchSchedule.isPending

  const handleSave = useCallback(() => {
    void saveDraft().catch((error: unknown) => alert("保存草稿失败：" + getApiErrorMessage(error, "未知错误")))
  }, [saveDraft])

  const handleApprove = useCallback(() => {
    void saveDraft("approved", false).catch((error: unknown) => alert("保存草稿失败：" + getApiErrorMessage(error, "未知错误")))
  }, [saveDraft])

  const handlePublish = useCallback(() => {
    if (!selectedId || publishClickLock.current) return
    publishClickLock.current = true
    void saveDraft().then(() => {
      publishDraft.mutate(
        { id: selectedId, account_ids: selectedAccountIds },
        {
          onError: (error: unknown) => alert("发布请求失败：" + getApiErrorMessage(error, "未知错误")),
          onSettled: () => { publishClickLock.current = false },
        },
      )
    }).catch((error: unknown) => {
      alert("保存草稿失败：" + getApiErrorMessage(error, "未知错误"))
      publishClickLock.current = false
    })
  }, [selectedId, selectedAccountIds, saveDraft, publishDraft])

  const handleBatchPublish = useCallback(() => {
    if (checkedDraftIds.length === 0 || publishClickLock.current) return
    publishClickLock.current = true
    batchPublish.mutate(
      { draft_ids: checkedDraftIds, account_ids: selectedAccountIds },
      {
        onSuccess: () => {
          setCheckedDraftIds([])
          setMobilePublishOpen(false)
        },
        onError: (error: unknown) => alert("批量发布请求失败：" + getApiErrorMessage(error, "未知错误")),
        onSettled: () => { publishClickLock.current = false },
      },
    )
  }, [checkedDraftIds, selectedAccountIds, batchPublish])

  const handleDelete = useCallback(() => {
    if (!selectedId || !window.confirm("确定删除这篇草稿吗？此操作不可恢复。")) return
    deleteDraft.mutate(selectedId, {
      onSuccess: () => {
        setSelectedId(null)
        resetSnapshot()
        setCheckedDraftIds((previous) => previous.filter((id) => id !== selectedId))
        setMobileDetailOpen(false)
      },
    })
  }, [selectedId, deleteDraft, resetSnapshot])

  const openSchedule = useCallback(() => {
    setScheduledAt(nextHourInAppTimezone())
    setScheduleOpen(true)
  }, [])

  const openBatchSchedule = useCallback(() => {
    setStartAt(nextHourInAppTimezone())
    setBatchScheduleOpen(true)
  }, [])

  const handleSchedule = useCallback(() => {
    if (!selectedId || !scheduledAt) return
    scheduleDraft.mutate(
      { id: selectedId, account_ids: selectedAccountIds, scheduled_at: appLocalInputToIso(scheduledAt) },
      {
        onSuccess: () => { setScheduleOpen(false); setScheduledAt("") },
        onError: (error: unknown) => alert("定时发布设置失败：" + getApiErrorMessage(error, "未知错误")),
      },
    )
  }, [selectedId, selectedAccountIds, scheduledAt, scheduleDraft])

  const handleBatchSchedule = useCallback((startAt: string, intervalHours: number, jitterHours: number) => {
    if (checkedDraftIds.length === 0 || !startAt) return
    batchSchedule.mutate(
      {
        draft_ids: checkedDraftIds,
        account_ids: selectedAccountIds,
        start_at: appLocalInputToIso(startAt),
        interval_hours: intervalHours,
        jitter_hours: jitterHours,
      },
      {
        onSuccess: (response) => {
          setBatchScheduleOpen(false)
          setStartAt("")
          setCheckedDraftIds([])
          const data = response.data as { draft_count?: number; first_at?: string | null; last_at?: string | null }
          const range = data.first_at && data.last_at
            ? `首篇 ${fmtApiDate(data.first_at)}，末篇 ${fmtApiDate(data.last_at)}`
            : ""
          alert(`批量定时设置成功：${data.draft_count ?? checkedDraftIds.length} 篇。${range}`)
        },
        onError: (error: unknown) => alert("批量定时发布设置失败：" + getApiErrorMessage(error, "未知错误")),
      },
    )
  }, [checkedDraftIds, selectedAccountIds, batchSchedule])

  const handleSelectDraft = useCallback((id: number) => {
    if (id !== selectedId) resetSnapshot()
    setSelectedId(id)
    if (isMobile) { setMobileDetailView("edit"); setMobileDetailOpen(true) }
  }, [selectedId, isMobile, resetSnapshot])

  const toggleImage = useCallback((path: string) => {
    setSelectedImagePaths((previous) => previous.includes(path) ? previous.filter((item) => item !== path) : [...previous, path])
  }, [setSelectedImagePaths])

  const renderEditor = (layout: "desktop" | "mobile", view: DraftDetailView = "edit") => (
    <DraftEditor
      layout={layout}
      view={view}
      draft={editor.draft ?? null}
      title={editor.title}
      content={editor.content}
      selectedImagePaths={editor.selectedImagePaths}
      onTitleChange={editor.setTitle}
      onContentChange={editor.setContent}
      onToggleImage={toggleImage}
      accounts={accounts}
      selectedAccountIds={selectedAccountIds}
      onSelectedAccountIdsChange={setSelectedAccountIds}
      canPublish={canPublish}
      hasPublishTarget={hasPublishTarget}
      isMutating={isMutating}
      deletePending={deleteDraft.isPending}
      selectedCount={checkedDraftIds.length}
      availableOperations={layout === "desktop"
        ? ["save", "approve", "publish", "schedule", "batchPublish", "batchSchedule", "delete"]
        : ["save", "approve", "publish", "schedule", "delete"]}
      onSave={handleSave}
      onApprove={handleApprove}
      onPublish={handlePublish}
      onSchedule={openSchedule}
      onDelete={handleDelete}
      onBatchPublish={() => setBatchConfirmOpen(true)}
      onBatchSchedule={openBatchSchedule}
      batchPublishPending={batchPublish.isPending}
      batchSchedulePending={batchSchedule.isPending}
      batchPublishLabel={batchPublish.isPending ? `发布中 ${checkedDraftIds.length} 篇…` : `批量发布 (${checkedDraftIds.length})`}
      batchScheduleLabel={batchSchedule.isPending ? `定时中 ${checkedDraftIds.length} 篇…` : `批量定时 (${checkedDraftIds.length})`}
    />
  )

  const renderTargetSelector = () => (
    <PublishTargetSelector
      label="发布渠道"
      accounts={accounts}
      selected={selectedAccountIds}
      onChange={setSelectedAccountIds}
      disabled={false}
    />
  )

  if (role === "limited") return null

  return (
    <div ref={workspaceRef} className="flex h-full min-w-0 flex-col md:flex-row overflow-hidden bg-background text-foreground">
      <div className="flex-1 min-h-0 min-w-0 md:flex-none md:shrink-0" style={{ width: !isMobile && workspaceWidth > 0 ? effectiveLeftWidth : undefined }}>
        <DraftList
          selectedId={selectedId}
          checkedIds={checkedDraftIds}
          onSelect={handleSelectDraft}
          onCheck={setCheckedDraftIds}
          onCheckAll={(ids, checked) => setCheckedDraftIds((previous) => {
            const remaining = previous.filter((id) => !ids.includes(id))
            return checked ? [...remaining, ...ids] : remaining
          })}
          mobile={isMobile}
          onOpenPublish={() => setMobilePublishOpen(true)}
          onOpenOutbox={() => setMobileOutboxOpen(true)}
        />
      </div>

      <div className="hidden md:block">
        <ResizableSplitter onResize={(delta) => setLeftWidth((width) => Math.max(minLeftWidth, Math.min(400, workspaceWidth - effectiveRightWidth - 360 - paneGutters, width + delta)))} />
      </div>

      <main className="hidden md:flex min-h-0 flex-1 flex-col min-w-[360px] overflow-hidden">
        {!selectedId ? <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">从左侧选择一篇草稿开始编辑</div> : (
          <div className="min-h-0 flex-1 flex flex-col overflow-y-auto overscroll-contain scrollbar-none p-3 pb-[calc(1rem+env(safe-area-inset-bottom,0px))] md:p-5 md:pb-5 gap-3 md:gap-4">
            {renderEditor("desktop")}
            <OutboxPanel onShowStats={() => setTweetStatsOpen(true)} />
          </div>
        )}
      </main>

      {showDesktopPreview && <>
        <div className="hidden md:block"><ResizableSplitter onResize={(delta) => setRightWidth((width) => Math.max(300, Math.min(640, workspaceWidth - effectiveLeftWidth - 360 - paneGutters, width - delta)))} /></div>
        <aside style={{ width: effectiveRightWidth }} className="hidden md:flex shrink-0 flex-col overflow-hidden border-l border-border">
          <DraftPreviewPane title={editor.title} content={editor.content} draft={editor.draft ?? null} selectedImagePaths={editor.selectedImagePaths} onToggleImage={toggleImage} />
        </aside>
      </>}

      <WorkspaceDialogs
        draft={editor.draft ?? null}
        selectedDraftIds={checkedDraftIds}
        selectedCount={checkedDraftIds.length}
        mobileDetailOpen={mobileDetailOpen}
        setMobileDetailOpen={setMobileDetailOpen}
        mobileDetailView={mobileDetailView}
        setMobileDetailView={setMobileDetailView}
        mobilePublishOpen={mobilePublishOpen}
        setMobilePublishOpen={setMobilePublishOpen}
        mobileOutboxOpen={mobileOutboxOpen}
        setMobileOutboxOpen={setMobileOutboxOpen}
        scheduleOpen={scheduleOpen}
        setScheduleOpen={setScheduleOpen}
        scheduledAt={scheduledAt}
        setScheduledAt={setScheduledAt}
        onSchedule={handleSchedule}
        schedulePending={scheduleDraft.isPending}
        batchScheduleOpen={batchScheduleOpen}
        setBatchScheduleOpen={setBatchScheduleOpen}
        startAt={startAt}
        setStartAt={setStartAt}
        onOpenBatchSchedule={openBatchSchedule}
        accounts={accounts}
        selectedAccountIds={selectedAccountIds}
        onSelectedAccountIdsChange={setSelectedAccountIds}
        hasPublishTarget={hasPublishTarget}
        batchSchedulePending={batchSchedule.isPending}
        onBatchSchedule={handleBatchSchedule}
        batchConfirmOpen={batchConfirmOpen}
        setBatchConfirmOpen={setBatchConfirmOpen}
        batchPublishPending={batchPublish.isPending}
        onBatchPublish={handleBatchPublish}
        canPublish={canPublish}
        renderEditor={renderEditor}
        renderTargetSelector={renderTargetSelector}
      />

      <StatsDialog open={tweetStatsOpen} onOpenChange={setTweetStatsOpen} type="tweet" title="推文发布统计" />
    </div>
  )
}
