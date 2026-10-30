"use client"

import React, { useEffect, useMemo, useRef, useState } from "react"
import { Inbox, Send, Upload } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { MobileHeaderActions } from "@/components/mobile-header-actions"
import { QueryErrorNotice } from "@/components/query-error-notice"
import { useUploadWord, useInfiniteDrafts } from "@/hooks/use-drafts"
import type { Draft } from "@/lib/types"
import { getApiErrorMessage } from "@/lib/api-errors"

const STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  approved: "已审核",
  rejected: "已拒绝",
  published: "已发布",
}
const STATUS_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  draft: "secondary",
  approved: "default",
  rejected: "destructive",
  published: "outline",
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge variant={STATUS_VARIANT[status] ?? "secondary"}>{STATUS_LABEL[status] ?? status}</Badge>
}

function WordUploadButton() {
  const upload = useUploadWord()
  const inputRef = useRef<HTMLInputElement>(null)
  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    if (!file.name.toLowerCase().endsWith(".docx")) {
      alert("仅支持 .docx 格式")
      return
    }
    upload.mutate(file, {
      onSuccess: (data) => {
        alert(`上传成功：${data.title}`)
        if (inputRef.current) inputRef.current.value = ""
      },
      onError: (error: unknown) => {
        alert(getApiErrorMessage(error, "上传失败"))
        if (inputRef.current) inputRef.current.value = ""
      },
    })
  }
  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        className="hidden"
        onChange={handleFileChange}
      />
      <Button variant="outline" size="sm" className="h-7 px-2 text-xs" disabled={upload.isPending} onClick={() => inputRef.current?.click()} title="上传 Word 草稿">
        <Upload className="w-3.5 h-3.5 mr-1" />{upload.isPending ? "上传中…" : "上传"}
      </Button>
    </>
  )
}

function DraftCard({
  draft,
  selected,
  checked,
  onClick,
  onCheck,
}: {
  draft: Draft
  selected: boolean
  checked: boolean
  onClick: (event: React.MouseEvent) => void
  onCheck: () => void
}) {
  const paperCount = draft.paper_ids?.length ?? 0
  const idLabel = paperCount > 1
    ? `Cite ID ${draft.paper_ids.map((paper) => paper.internal_id ?? `#${paper.id}`).join(", ")}`
    : `Cite ID ${draft.paper_internal_id ?? "-"}`
  return (
    <div className={["w-full text-left px-2 py-2 rounded-lg border transition-colors cursor-pointer flex items-start gap-2", selected || checked ? "border-primary bg-primary/5" : "border-transparent hover:border-border hover:bg-muted/40"].join(" ")}>
      <input type="checkbox" checked={checked} onChange={onCheck} className="hidden md:block mt-1 accent-primary shrink-0 cursor-pointer" />
      <Checkbox checked={checked} onCheckedChange={onCheck} className="mt-0.5 size-5 shrink-0 bg-background md:hidden" aria-label={checked ? "取消选择推文" : "选择推文"} />
      <button type="button" onClick={onClick} className="flex-1 min-w-0 text-left">
        <div className="flex items-start justify-between gap-1.5 mb-1">
          <span className="text-sm font-medium leading-snug flex-1 min-w-0 truncate">{draft.title}</span>
          <StatusBadge status={draft.status} />
        </div>
        <div className="flex items-center justify-between text-xs text-muted-foreground gap-2">
          <span className="truncate flex-1 min-w-0">{draft.paper_title ?? ""}</span>
          <span className="shrink-0">{idLabel}</span>
        </div>
      </button>
    </div>
  )
}

export function DraftList({
  selectedId,
  checkedIds,
  onSelect,
  onCheck,
  onCheckAll,
  style,
  mobile,
  onOpenPublish,
  onOpenOutbox,
}: {
  selectedId: number | null
  checkedIds: number[]
  onSelect: (id: number) => void
  onCheck: (ids: number[]) => void
  onCheckAll: (ids: number[], checked: boolean) => void
  style?: React.CSSProperties
  mobile: boolean
  onOpenPublish: () => void
  onOpenOutbox: () => void
}) {
  const [status, setStatus] = useState("draft")
  const [inputVal, setInputVal] = useState("")
  const [query, setQuery] = useState("")
  const [lastClickedIndex, setLastClickedIndex] = useState<number | null>(null)
  useEffect(() => {
    const timer = setTimeout(() => setQuery(inputVal), 300)
    return () => clearTimeout(timer)
  }, [inputVal])

  const {
    data, isLoading, isFetchingNextPage, isFetchNextPageError, isError, error,
    hasNextPage, fetchNextPage, refetch,
  } = useInfiniteDrafts(status, query)
  const drafts = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data?.pages])
  const draftTotal = data?.pages[0]?.total ?? 0
  useEffect(() => {
    if (!mobile && drafts.length > 0 && selectedId === null) onSelect(drafts[0].id)
  }, [drafts, selectedId, onSelect, mobile])

  const loadMoreRef = useRef<HTMLDivElement>(null)
  const listScrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const element = loadMoreRef.current
    if (!element) return
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && hasNextPage && !isFetchingNextPage && !isFetchNextPageError) fetchNextPage()
    }, { root: listScrollRef.current, threshold: 0.1 })
    observer.observe(element)
    return () => observer.disconnect()
  }, [fetchNextPage, hasNextPage, isFetchingNextPage, isFetchNextPageError])

  const visibleIds = drafts.map((draft) => draft.id)
  const allChecked = drafts.length > 0 && drafts.every((draft) => checkedIds.includes(draft.id))
  const someChecked = drafts.some((draft) => checkedIds.includes(draft.id)) && !allChecked
  function handleCardClick(event: React.MouseEvent, id: number, index: number) {
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault()
      onCheck(checkedIds.includes(id) ? checkedIds.filter((value) => value !== id) : [...checkedIds, id])
      setLastClickedIndex(index)
    } else if (event.shiftKey && lastClickedIndex !== null) {
      event.preventDefault()
      const start = Math.min(lastClickedIndex, index)
      const end = Math.max(lastClickedIndex, index)
      onCheck(Array.from(new Set([...checkedIds, ...drafts.slice(start, end + 1).map((draft) => draft.id)])))
      setLastClickedIndex(index)
    } else {
      onSelect(id)
      setLastClickedIndex(index)
    }
  }

  const renderStatusTabs = (compact: boolean) => (
    <Tabs value={status} onValueChange={(value) => { setStatus(value); onCheck([]) }}>
      <TabsList className={compact ? "grid h-8 w-[240px] max-w-full grid-cols-3" : "grid w-full grid-cols-3 h-9"}>
        <TabsTrigger value="draft" className={compact ? "px-2 text-[11px]" : "flex-1 text-xs"}>草稿</TabsTrigger>
        <TabsTrigger value="approved" className={compact ? "px-2 text-[11px]" : "flex-1 text-xs"}>已审核</TabsTrigger>
        <TabsTrigger value="published" className={compact ? "px-2 text-[11px]" : "flex-1 text-xs"}>已发布</TabsTrigger>
      </TabsList>
    </Tabs>
  )

  return (
    <>
      <MobileHeaderActions>{renderStatusTabs(true)}</MobileHeaderActions>
      <aside style={style} className="h-full shrink-0 flex flex-col md:border-r border-border overflow-hidden select-none">
        <div className="hidden md:block px-3 pt-3 pb-0">{renderStatusTabs(false)}</div>
        <div className="px-3 py-2 flex gap-2">
          <Input placeholder="搜索标题…" value={inputVal} onChange={(event) => setInputVal(event.target.value)} className="text-xs h-7 flex-1" />
          <WordUploadButton />
        </div>
        {drafts.length > 0 && (
          <div className="hidden md:flex px-3 py-1.5 items-center gap-2 border-b border-border">
            <input type="checkbox" checked={allChecked} ref={(element) => { if (element) element.indeterminate = someChecked }} onChange={(event) => onCheckAll(visibleIds, event.target.checked)} className="accent-primary cursor-pointer" />
            <span className="text-xs text-muted-foreground">全选 ({checkedIds.length}/{draftTotal})</span>
          </div>
        )}
        <div ref={listScrollRef} className="flex-1 overflow-y-auto scrollbar-none px-2 pb-3 flex flex-col gap-1">
          {isLoading && <p className="text-xs text-muted-foreground text-center py-8">加载中…</p>}
          {!isLoading && isError && drafts.length === 0 ? (
            <QueryErrorNotice className="mx-2 p-4" message={`草稿列表加载失败：${getApiErrorMessage(error, "请稍后重试")}`} onRetry={() => { void refetch() }} />
          ) : !isLoading && drafts.length === 0 ? <p className="text-xs text-muted-foreground text-center py-8">暂无草稿</p> : null}
          {drafts.map((draft, index) => (
            <DraftCard
              key={draft.id}
              draft={draft}
              selected={selectedId === draft.id}
              checked={checkedIds.includes(draft.id)}
              onClick={(event) => handleCardClick(event, draft.id, index)}
              onCheck={() => {
                onCheck(checkedIds.includes(draft.id) ? checkedIds.filter((value) => value !== draft.id) : [...checkedIds, draft.id])
                setLastClickedIndex(index)
              }}
            />
          ))}
          <div ref={loadMoreRef} className="py-2 text-center text-xs text-muted-foreground">
            {isFetchingNextPage ? "加载更多…" : isFetchNextPageError ? (
              <QueryErrorNotice className="mx-2 p-3" message={`加载更多草稿失败：${getApiErrorMessage(error, "请稍后重试")}`} onRetry={() => { void fetchNextPage() }} />
            ) : hasNextPage ? "继续滚动加载" : null}
          </div>
        </div>
        <div data-bottom-bar className="md:hidden shrink-0 border-t bg-background px-2 pt-1.5 pb-[max(0.5rem,env(safe-area-inset-bottom))] space-y-1.5">
          <div className="flex items-center justify-between px-1 text-[11px] text-muted-foreground"><span>共 {draftTotal} 篇</span><span>已选 {checkedIds.length}</span></div>
          <div className="grid grid-cols-2 gap-1.5">
            <Button type="button" size="sm" className="h-9 rounded-lg text-xs" disabled={checkedIds.length === 0} onClick={onOpenPublish}><Send className="w-3.5 h-3.5 mr-1" />发布</Button>
            <Button type="button" size="sm" variant="outline" className="h-9 rounded-lg text-xs" onClick={onOpenOutbox}><Inbox className="w-3.5 h-3.5 mr-1" />发件箱</Button>
          </div>
        </div>
      </aside>
    </>
  )
}
