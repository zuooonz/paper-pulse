"use client"

import React, { useState, useRef, useCallback, useEffect, useMemo } from "react"
import { useSession } from "next-auth/react"
import { useQuery, useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiClient } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { fmtApiDate } from "@/lib/datetime"
import { Loader2, Upload, Send, FileVideo, Image as ImageIcon, X, BarChart3, Inbox } from "lucide-react"
import type { Account } from "@/lib/types"
import { useAccounts } from "@/hooks/use-accounts"
import { videoChannelName } from "@/lib/channels"
import { getApiErrorMessage, isRecord } from "@/lib/api-errors"
import { StatsDialog } from "@/components/stats-dialog"
import { useDevice } from "@/hooks/use-device"
import { MobileHeaderActions } from "@/components/mobile-header-actions"
import { QueryErrorNotice } from "@/components/query-error-notice"

function AuthenticatedVideo({ videoId, className, playsInline }: { videoId: number; className: string; playsInline?: boolean }) {
  const [src, setSrc] = useState("")
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let objectUrl: string | undefined
    const controller = new AbortController()
    apiClient
      .get(`/api/videos/file/${videoId}`, { responseType: "blob", signal: controller.signal })
      .then((response) => {
        objectUrl = URL.createObjectURL(response.data)
        setSrc(objectUrl)
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true)
      })
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [videoId])
  if (!src) return <div className={`${className} flex items-center justify-center bg-black/80 text-xs text-white`}>{failed ? "视频加载失败" : "正在加载视频…"}</div>
  return <video key={videoId} src={src} controls playsInline={playsInline} className={className} />
}

// ── Resizable splitter ───────────────────────────────────────────

function ResizableSplitter({
  onResize,
}: {
  onResize: (delta: number) => void
}) {
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    if (!dragging) return
    const handleMove = (e: MouseEvent) => { onResize(e.movementX) }
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

  return (
    <div
      className="w-2 cursor-col-resize flex items-center justify-center hover:bg-primary/10 active:bg-primary/20 transition-colors z-10 -mx-0.5"
      onMouseDown={() => setDragging(true)}
    >
      <div className="w-px h-8 bg-border rounded-full" />
    </div>
  )
}

// ── Hooks ────────────────────────────────────────────────────────

interface VideoItem {
  id: number; title: string; description: string | null; tags: string | null
  file_path: string | null; file_size: number | null; duration: number | null
  cover_path: string | null; width: number | null; height: number | null
  status: string; source_paper_id: number | null; script: string | null
  created_at: string | null; updated_at: string | null
  publish_records?: PublishRecord[]
}
interface PublishRecord {
  id: number; account_id: number | null; channel: string; account_name: string
  status: string; publish_url: string | null; error: string | null
  scheduled_at: string | null; published_at: string | null; created_at: string | null
}

interface VideoOutboxRecord extends PublishRecord {
  video_id: number
  video_title: string
}
interface VideoEditDraft {
  videoId: number
  title: string
  description: string
  tags: string
}

function readStoryboard(script: string) {
  try {
    const parsed: unknown = JSON.parse(script)
    if (!isRecord(parsed) || !isRecord(parsed.storyboard)) return null
    if (parsed.storyboard.scenes === undefined) return []
    if (!Array.isArray(parsed.storyboard.scenes)) return null
    return parsed.storyboard.scenes.filter(isRecord).map((scene) => ({
      duration_sec: typeof scene.duration_sec === "number" ? scene.duration_sec : 0,
      prompt_en: typeof scene.prompt_en === "string" ? scene.prompt_en : "",
    }))
  } catch {
    return null
  }
}
function useVideo(id: number | null) {
  return useQuery<VideoItem>({ queryKey: ["video", id], enabled: !!id, queryFn: async () => (await apiClient.get(`/api/videos/${id}`)).data, staleTime: 5_000 })
}
interface VideosResponse { items: VideoItem[]; total: number; page: number; page_size: number }
function useVideos(p?: Record<string, string>) {
  const qs = new URLSearchParams(p || {}).toString()
  return useInfiniteQuery<VideosResponse>({
    queryKey: ["videos", qs],
    queryFn: async ({ pageParam = 1 }) => (await apiClient.get("/api/videos", {
      params: { ...(p || {}), page: pageParam, page_size: 20 },
    })).data,
    initialPageParam: 1,
    getNextPageParam: (lastPage) => lastPage.page * lastPage.page_size < lastPage.total ? lastPage.page + 1 : undefined,
    staleTime: 5_000,
  })
}

// ── Helpers ──────────────────────────────────────────────────────

function fmtSize(b: number | null) { if (!b) return "-"; return b < 1024 * 1024 ? `${(b / 1024).toFixed(0)}KB` : `${(b / (1024 * 1024)).toFixed(1)}MB` }
function fmtDur(s: number | null) { if (!s) return "-"; const m = Math.floor(s / 60); return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}` }
function truncate(s: string | null, n: number) { if (!s) return "-"; return s.length > n ? s.slice(0, n) + "…" : s }

const ST: Record<string, { l: string; v: "default" | "secondary" | "destructive" | "outline" }> = {
  draft: { l: "草稿", v: "secondary" }, approved: { l: "已审核", v: "default" },
  publishing: { l: "发布中", v: "default" }, published: { l: "已发布", v: "outline" },
  generating: { l: "生成中", v: "secondary" }, error: { l: "错误", v: "destructive" },
}
function SBadge({ s }: { s: string }) { const x = ST[s] || { l: s, v: "secondary" as const }; return <Badge variant={x.v}>{x.l}</Badge> }
// ── AccountSelector — EXACT copy from workspace ──────────────────

function AccountSelector({
  accounts, selected, onChange, disabled, label,
}: { accounts: Account[]; selected: number[]; onChange: (ids: number[]) => void; disabled?: boolean; label?: string }) {
  const byChannel: Record<string, Account[]> = {}
  for (const acc of accounts) { if (!byChannel[acc.channel]) byChannel[acc.channel] = []; byChannel[acc.channel].push(acc) }
  function toggle(id: number) { onChange(selected.includes(id) ? selected.filter(x => x !== id) : [...selected, id]) }
  if (accounts.length === 0) return <p className="text-xs text-muted-foreground">暂无渠道账号</p>

  // Tested and confirmed working
  const ENABLED = new Set(["baijiahao", "xiaohongshu", "bilibili"])
  const isChannelDisabled = (ch: string) => !ENABLED.has(ch)

  const validAccounts = accounts.filter(a => a.valid && !disabled && !isChannelDisabled(a.channel))
  const allSelected = validAccounts.length > 0 && validAccounts.every(a => selected.includes(a.id))
  const someSelected = validAccounts.some(a => selected.includes(a.id)) && !allSelected
  function toggleAll() {
    const validIds = validAccounts.map(a => a.id)
    if (allSelected) onChange(selected.filter(id => !validIds.includes(id)))
    else onChange(Array.from(new Set([...selected, ...validIds])))
  }
  return (
    <div>
      {label && (
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs font-medium text-muted-foreground">{label}</span>
          <label className="flex items-center gap-1 cursor-pointer text-muted-foreground hover:text-foreground transition-colors">
            <input type="checkbox" checked={allSelected}
              ref={el => { if (el) el.indeterminate = someSelected }}
              disabled={validAccounts.length === 0 || disabled}
              onChange={toggleAll} className="accent-primary shrink-0" />
            <span className="text-xs">全选</span>
          </label>
        </div>
      )}
      <div className="grid grid-cols-2 gap-3">
        {Object.entries(byChannel).map(([channel, accs]) => {
          const chDisabled = isChannelDisabled(channel)
          const validAccs = accs.filter(a => a.valid && !disabled && !chDisabled)
          const allSel = validAccs.length > 0 && validAccs.every(a => selected.includes(a.id))
          const someSel = validAccs.some(a => selected.includes(a.id)) && !allSel
          function toggleChannel() {
            const validIds = validAccs.map(a => a.id)
            if (allSel) onChange(selected.filter(id => !validIds.includes(id)))
            else onChange(Array.from(new Set([...selected, ...validIds])))
          }
          return (
            <div key={channel}>
              <div className="flex items-center gap-1.5 mb-1.5">
                <input type="checkbox" checked={allSel}
                  ref={el => { if (el) el.indeterminate = someSel }}
                  disabled={chDisabled || validAccs.length === 0 || disabled}
                  onChange={toggleChannel} className="accent-primary shrink-0" />
                <span className={chDisabled ? "text-xs font-medium text-muted-foreground/40" : "text-xs font-medium text-muted-foreground"}>{videoChannelName(channel)}</span>
              </div>
              <div className="flex flex-col gap-1 ml-5">
                {accs.filter(a => a.valid && !disabled).map(a => {
                  const accDisabled = chDisabled
                  return (
                    <label key={a.id} className={accDisabled ? "flex items-center gap-1.5 text-muted-foreground/30" : "flex items-center gap-1.5 cursor-pointer text-muted-foreground hover:text-foreground transition-colors"}>
                      <input type="checkbox" checked={selected.includes(a.id)} onChange={() => toggle(a.id)} disabled={accDisabled || disabled} className="accent-primary shrink-0" />
                      <span className="text-xs truncate">{a.account_name}</span>
                    </label>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── VideoCard — modeled exactly after workspace DraftCard ───────

function VideoCard({ v, selected, checked, onClick, onCheck }: {
  v: VideoItem; selected: boolean; checked: boolean
  onClick: (e: React.MouseEvent) => void; onCheck: () => void
}) {
  return (
    <div className={["w-full text-left px-2 py-2 rounded-lg border transition-colors cursor-pointer flex items-start gap-2",
        selected || checked ? "border-primary bg-primary/5" : "border-transparent hover:border-border hover:bg-muted/40"].join(" ")}>
      <input type="checkbox" checked={checked} onChange={onCheck} className="hidden md:block mt-1 accent-primary shrink-0 cursor-pointer" />
      <Checkbox
        checked={checked}
        onCheckedChange={onCheck}
        className="mt-0.5 size-5 shrink-0 bg-background md:hidden"
        aria-label={checked ? "取消选择视频" : "选择视频"}
      />
      <button type="button" onClick={onClick} className="flex-1 min-w-0 text-left">
        <div className="flex items-start justify-between gap-1.5 mb-1">
          <span className="text-sm font-medium leading-snug flex-1 min-w-0 truncate">{truncate(v.title, 30)}</span>
          <SBadge s={v.status} />
        </div>
        <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
          {v.file_path ? <><span>{fmtDur(v.duration)}</span><span>{fmtSize(v.file_size)}</span></> : <span>待生成</span>}
          {v.source_paper_id && <span>Cite ID {v.source_paper_id}</span>}
        </div>
      </button>
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────

export default function VideosPage() {
  const { isMobile } = useDevice()
  const { data: session } = useSession()
  const role = session?.user?.role ?? "viewer"
  const canManage = role === "admin" || role === "reviewer"

  const qc = useQueryClient()
  const [activeId, setActiveId] = useState<number | null>(null)
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false)
  const [mobilePublishOpen, setMobilePublishOpen] = useState(false)
  const [mobileOutboxOpen, setMobileOutboxOpen] = useState(false)
  const [filter, setFilter] = useState("draft")
  const [searchQ, setSearchQ] = useState("")
  const [debouncedSearchQ, setDebouncedSearchQ] = useState("")
  const [checkedIds, setCheckedIds] = useState<number[]>([])
  const [leftWidth, setLeftWidth] = useState(288)    // 18rem
  const [rightWidth, setRightWidth] = useState(512)  // 32rem
  const [lastClickedIdx, setLastClickedIdx] = useState<number>(-1)
  const [showUpload, setShowUpload] = useState(false)
  const [selectedAccs, setSelectedAccs] = useState<number[]>([])
  const [schOpen, setSchOpen] = useState(false)
  const [schDate, setSchDate] = useState(""); const [schTime, setSchTime] = useState("09:00")
  const fileRef = useRef<HTMLInputElement>(null)
  const coverRef = useRef<HTMLInputElement>(null)
  const [upFile, setUpFile] = useState<File | null>(null)
  const [upCover, setUpCover] = useState<File | null>(null)

  const [editDraft, setEditDraft] = useState<VideoEditDraft | null>(null)
  const [videoStatsOpen, setVideoStatsOpen] = useState(false)

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearchQ(searchQ.trim()), 300)
    return () => clearTimeout(timer)
  }, [searchQ])

  const videoParams: Record<string, string> = {}
  if (filter !== "all") videoParams.status = filter
  if (debouncedSearchQ) videoParams.q = debouncedSearchQ
  const {
    data,
    isLoading,
    isError: isVideosError,
    isRefetchError: isVideosRefetchError,
    error: videosError,
    refetch: refetchVideos,
    isFetching: isVideosFetching,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
  } = useVideos(videoParams)
  const items = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data?.pages])
  const selectedId = activeId ?? (!isMobile ? items[0]?.id ?? null : null)
  const { data: detailData } = useVideo(selectedId)
  const {
    data: accData,
    isError: isAccountsError,
    isRefetchError: isAccountsRefetchError,
    error: accountsError,
    refetch: refetchAccounts,
    isFetching: isAccountsFetching,
  } = useAccounts()
  const { data: videoOutboxData, isLoading: isVideoOutboxLoading } = useQuery<{ records: VideoOutboxRecord[] }>({
    queryKey: ["video-outbox"],
    queryFn: () => apiClient.get("/api/videos/outbox").then((response) => response.data),
    enabled: mobileOutboxOpen,
  })

  const totalCount = data?.pages[0]?.total ?? 0
  const accounts = accData ?? []
  const detail = detailData || items.find(v => v.id === selectedId) || null
  const currentEditDraft = editDraft?.videoId === selectedId
    ? editDraft
    : detail
      ? { videoId: detail.id, title: detail.title || "", description: detail.description || "", tags: detail.tags || "" }
      : null
  const editTitle = currentEditDraft?.title ?? ""
  const editDesc = currentEditDraft?.description ?? ""
  const editTags = currentEditDraft?.tags ?? ""

  function updateEditDraft(field: "title" | "description" | "tags", value: string) {
    if (!detail) return
    setEditDraft((current) => {
      const base = current?.videoId === detail.id
        ? current
        : { videoId: detail.id, title: detail.title || "", description: detail.description || "", tags: detail.tags || "" }
      return { ...base, [field]: value }
    })
  }

  const qInv = () => { qc.invalidateQueries({ queryKey: ["videos"], exact: false }); qc.invalidateQueries({ queryKey: ["video", selectedId] }) }
  const delMut = useMutation({ mutationFn: (id: number) => apiClient.delete(`/api/videos/${id}`), onSuccess: () => { qInv(); setActiveId(null); setMobileDetailOpen(false) } })
  const patchMut = useMutation({ mutationFn: ({ id, ...body }: { id: number; title?: string; description?: string; tags?: string; status?: string }) => apiClient.patch(`/api/videos/${id}`, body), onSuccess: qInv })
  const pubMut = useMutation({ mutationFn: ({ id, ids }: { id: number; ids: number[] }) => { const fd = new FormData(); ids.forEach(a => fd.append("account_ids", String(a))); return apiClient.post(`/api/videos/${id}/publish`, fd) }, onSuccess: qInv })
  const batchPubMut = useMutation({ mutationFn: ({ ids, aids }: { ids: number[]; aids: number[] }) => { const fd = new FormData(); ids.forEach(v => fd.append("video_ids", String(v))); aids.forEach(a => fd.append("account_ids", String(a))); return apiClient.post("/api/videos/batch-publish", fd) }, onSuccess: () => { qInv(); qc.invalidateQueries({ queryKey: ["video-outbox"] }); setCheckedIds([]); setMobilePublishOpen(false) } })

  const [upTitle, setUpTitle] = useState(""); const [upDesc, setUpDesc] = useState(""); const [upTags, setUpTags] = useState("")
  const uploadMut = useMutation({
    mutationFn: async (vars: { title: string; file: File; desc?: string; tags?: string; cover?: File }) => {
      const fd = new FormData(); fd.append("title", vars.title); fd.append("file", vars.file)
      if (vars.desc) fd.append("description", vars.desc)
      if (vars.tags) fd.append("tags", vars.tags)
      if (vars.cover) fd.append("cover", vars.cover)
      return (await apiClient.post("/api/videos", fd)).data
    },
    onSuccess: () => {
      qInv(); setShowUpload(false)
      setUpTitle(""); setUpDesc(""); setUpTags(""); setUpFile(null); setUpCover(null)
    },
    onError: (err: unknown) => {
      alert(`上传失败: ${getApiErrorMessage(err, "未知错误")}`)
    },
  })
  const doPublish = () => { if (!selectedId) return; pubMut.mutate({ id: selectedId, ids: selectedAccs }) }
  const doSchedule = async () => { if (!selectedId || !schDate) return; await apiClient.post(`/api/videos/${selectedId}/schedule`, { scheduled_at: `${schDate}T${schTime}:00`, account_ids: selectedAccs }); setSchOpen(false); qInv() }

  const pubRecords = detail?.publish_records ?? []

  const filtered = items
  const visibleIds = filtered.map(v => v.id)
  const allChecked = visibleIds.length > 0 && visibleIds.every(id => checkedIds.includes(id))
  const someChecked = visibleIds.some(id => checkedIds.includes(id)) && !allChecked
  const onCheckAll = (ids: number[], checked: boolean) => {
    if (checked) setCheckedIds(Array.from(new Set([...checkedIds, ...ids])))
    else setCheckedIds(checkedIds.filter(id => !ids.includes(id)))
  }

  const videoListRef = useRef<HTMLDivElement>(null)
  const videoLoadMoreRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const target = videoLoadMoreRef.current
    if (!target) return
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && hasNextPage && !isFetchingNextPage && !isFetchNextPageError) fetchNextPage()
    }, { root: videoListRef.current, threshold: 0.1 })
    observer.observe(target)
    return () => observer.disconnect()
  }, [data?.pages.length, fetchNextPage, hasNextPage, isFetchingNextPage, isFetchNextPageError])

  const handleCardClick = useCallback((e: React.MouseEvent, id: number, idx: number) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault()
      setCheckedIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
    } else if (e.shiftKey && lastClickedIdx >= 0) {
      e.preventDefault()
      const [lo, hi] = [Math.min(lastClickedIdx, idx), Math.max(lastClickedIdx, idx)]
      const rid = filtered.slice(lo, hi + 1).map(v => v.id)
      setCheckedIds(Array.from(new Set([...checkedIds, ...rid])))
    } else {
      if (id !== selectedId) setEditDraft(null)
      setActiveId(id)
      if (isMobile) setMobileDetailOpen(true)
    }
    setLastClickedIdx(idx)
  }, [lastClickedIdx, filtered, checkedIds, isMobile, selectedId])

  if (isLoading) return <div className="flex items-center justify-center h-full"><Loader2 className="w-8 h-8 animate-spin text-muted-foreground" /></div>

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden md:flex-row">
      <MobileHeaderActions>
        <Tabs value={filter} onValueChange={(value) => { setFilter(value); setCheckedIds([]) }}>
          <TabsList className="grid h-8 w-[240px] max-w-full grid-cols-3">
            <TabsTrigger value="draft" className="px-2 text-[11px]">草稿</TabsTrigger>
            <TabsTrigger value="approved" className="px-2 text-[11px]">已审核</TabsTrigger>
            <TabsTrigger value="published" className="px-2 text-[11px]">已发布</TabsTrigger>
          </TabsList>
        </Tabs>
      </MobileHeaderActions>
      {/* ═══ LEFT: Video list ═══ */}
      <div className="w-full flex-1 min-h-0 md:flex-none shrink-0 md:border-r border-border flex flex-col select-none overflow-hidden" style={{ width: typeof window !== 'undefined' && window.innerWidth >= 768 ? leftWidth : undefined }}>
        {/* Status tabs */}
        <div className="hidden md:block px-3 pt-3 pb-0">
          <Tabs value={filter} onValueChange={(value) => { setFilter(value); setCheckedIds([]) }}>
            <TabsList className="grid w-full grid-cols-3 h-9">
              <TabsTrigger value="draft" className="flex-1 text-xs">草稿</TabsTrigger>
              <TabsTrigger value="approved" className="flex-1 text-xs">已审核</TabsTrigger>
              <TabsTrigger value="published" className="flex-1 text-xs">已发布</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>

        {/* Search + Upload */}
        <div className="px-3 py-2 flex gap-2">
          <Input placeholder="搜索标题…" value={searchQ} onChange={e => setSearchQ(e.target.value)} className="text-xs h-7 flex-1" />
          <Button size="sm" variant="outline" className="h-7 px-2 text-xs shrink-0" onClick={() => setShowUpload(true)}><Upload className="w-3.5 h-3.5 mr-1" />上传</Button>
        </div>

        {/* Select all bar */}
        {filtered.length > 0 && (
          <div className="hidden md:flex px-3 py-1.5 items-center gap-2 border-b border-border">
            <input type="checkbox" checked={allChecked}
              ref={el => { if (el) el.indeterminate = someChecked }}
              onChange={e => onCheckAll(visibleIds, e.target.checked)}
              className="accent-primary cursor-pointer" />
            <span className="text-xs text-muted-foreground">全选 ({checkedIds.length}/{totalCount})</span>
            {checkedIds.length > 0 && (
              <Button variant="default" size="sm" className="h-6 px-2 text-[10px] ml-auto"
                disabled={!canManage || selectedAccs.length === 0 || batchPubMut.isPending}
                onClick={() => batchPubMut.mutate({ ids: checkedIds, aids: selectedAccs })}>
                <Send className="w-3 h-3 mr-1" />批量发布 ({checkedIds.length})
              </Button>
            )}
          </div>
        )}

        {/* List */}
        <div ref={videoListRef} className="flex-1 overflow-y-auto scrollbar-none px-2 pb-1 flex flex-col gap-1">
          {(isVideosError || isVideosRefetchError) && (
            <QueryErrorNotice
              className="mx-2 my-3 p-4"
              message={`视频列表加载失败：${getApiErrorMessage(videosError, "请稍后重试")}`}
              onRetry={() => { void refetchVideos() }}
              retrying={isVideosFetching}
            />
          )}
          {filtered.length === 0 && !(isVideosError || isVideosRefetchError) && (
            <p className="text-xs text-muted-foreground text-center py-8">暂无视频</p>
          )}
          {filtered.map((v, idx) => (
            <VideoCard key={v.id} v={v} selected={selectedId === v.id} checked={checkedIds.includes(v.id)}
              onClick={e => handleCardClick(e, v.id, idx)}
              onCheck={() => { setCheckedIds(prev => prev.includes(v.id) ? prev.filter(x => x !== v.id) : [...prev, v.id]); setLastClickedIdx(idx) }} />
          ))}
          <div ref={videoLoadMoreRef} className="py-2 text-center text-xs text-muted-foreground">
            {isFetchingNextPage ? "加载更多…" : isFetchNextPageError ? (
              <QueryErrorNotice
                className="mx-2 p-3"
                message={`加载更多视频失败：${getApiErrorMessage(videosError, "请稍后重试")}`}
                onRetry={() => { void fetchNextPage() }}
              />
            ) : hasNextPage ? "继续滚动加载" : null}
          </div>
        </div>
        <div data-bottom-bar className="md:hidden shrink-0 border-t bg-background px-2 pt-1.5 pb-[max(0.5rem,env(safe-area-inset-bottom))] space-y-1.5">
          <div className="flex items-center justify-between px-1 text-[11px] text-muted-foreground">
            <span>共 {totalCount} 条</span>
            <span>已选 {checkedIds.length}</span>
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            <Button type="button" size="sm" className="h-9 rounded-lg text-xs" disabled={checkedIds.length === 0} onClick={() => setMobilePublishOpen(true)}><Send className="w-3.5 h-3.5 mr-1" />发布</Button>
            <Button type="button" size="sm" variant="outline" className="h-9 rounded-lg text-xs" onClick={() => setMobileOutboxOpen(true)}><Inbox className="w-3.5 h-3.5 mr-1" />发件箱</Button>
          </div>
        </div>
      </div>

      <div className="hidden md:block">
        <ResizableSplitter
          onResize={(dx) =>
            setLeftWidth((w) => Math.max(200, Math.min(400, w + dx)))
          }
        />
      </div>

      {/* ═══ CENTER: Player → Publish ═══ */}
      <div className="hidden md:flex flex-1 flex-col overflow-hidden min-w-0">
        {!detail ? (
          <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">选择左侧视频</div>
        ) : (
          <div className="flex-1 flex flex-col overflow-hidden">
            <div className="shrink-0 flex items-center justify-center mx-3 mt-3">
              {detail.file_path ? (
                <div className="relative rounded-lg overflow-hidden">
                  <AuthenticatedVideo key={detail.id} videoId={detail.id} className="max-w-full max-h-[50vh] rounded-lg" />
                  {detail.duration && <span className="absolute bottom-2 right-2 text-xs bg-black/60 text-white px-1.5 py-0.5 rounded">{fmtDur(detail.duration)}</span>}
                </div>
              ) : (
                <div className="text-muted-foreground flex flex-col items-center gap-2 py-12"><FileVideo className="w-12 h-12" /><span className="text-xs">{detail.status === "generating" ? "AI 生成中…" : "暂无视频文件"}</span></div>
              )}
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain scrollbar-none px-3 md:px-4 pt-3 md:pt-4 pb-4 space-y-4">
              {canManage && (isAccountsError || isAccountsRefetchError) && (
                <QueryErrorNotice
                  className="p-3"
                  message={`账号列表加载失败：${getApiErrorMessage(accountsError, "请稍后重试")}`}
                  onRetry={() => { void refetchAccounts() }}
                  retrying={isAccountsFetching}
                />
              )}
              <AccountSelector accounts={accounts} selected={selectedAccs} onChange={setSelectedAccs} label="发布渠道" />
              <div data-bottom-bar className="sticky bottom-0 z-10 flex gap-2 flex-wrap border-t border-border bg-background/95 pt-3 pb-2 backdrop-blur supports-backdrop-filter:bg-background/85">
                <Button variant="default" size="sm" disabled={!canManage || !selectedId || selectedAccs.length === 0 || pubMut.isPending} onClick={doPublish}>
                  {pubMut.isPending ? "发布中…" : "立即发布"}
                </Button>
                <Button variant="default" size="sm" disabled={!canManage || !selectedId || selectedAccs.length === 0}
                  onClick={() => { setSchDate(""); setSchTime("09:00"); setSchOpen(true) }}>
                  定时发布
                </Button>
              </div>

              {/* Outbox */}
              <div className="border rounded-lg p-3 bg-muted/30">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <p className="text-xs font-semibold">发件箱</p>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-5 px-1.5 text-[10px] text-muted-foreground hover:text-primary"
                      onClick={() => setVideoStatsOpen(true)}
                    >
                      <BarChart3 className="w-3 h-3 mr-0.5" />
                      统计
                    </Button>
                  </div>
                  <span className="text-xs text-muted-foreground">{pubRecords.length} 条{pubRecords.some(r => r.status === "uploading" || r.status === "pending") && <span className="ml-1 text-blue-600">· 发布中</span>}</span>
                </div>
                {pubRecords.length === 0 ? <p className="text-xs text-muted-foreground">暂无任务</p> : (
                  <div className="flex flex-col gap-2">
                    {Object.entries(pubRecords.reduce((g, r) => { const ch = r.channel || "unknown"; if (!g[ch]) g[ch] = []; g[ch].push(r); return g }, {} as Record<string, PublishRecord[]>)).map(([ch, recs]) => (
                      <div key={ch} className="text-xs bg-background rounded px-2.5 py-2 border">
                        <div className="text-muted-foreground text-[11px] font-medium mb-1">{videoChannelName(ch)}</div>
                        {recs.map(r => {
                          const c = r.status === "success" ? "text-green-600" : r.status === "failed" ? "text-red-600" : r.status === "uploading" || r.status === "pending" ? "text-blue-600" : r.status === "scheduled" ? "text-purple-600" : "text-muted-foreground"
                          const l = r.status === "success" ? "成功" : r.status === "failed" ? "失败" : r.status === "uploading" || r.status === "pending" ? "发布中" : r.status === "scheduled" && r.scheduled_at ? `定时 ${fmtApiDate(r.scheduled_at)}` : "定时"
                          return (
                            <div key={r.id} className="flex items-center justify-between gap-2">
                              <div className="flex items-center gap-1.5 min-w-0"><span className="truncate max-w-[80px]">{r.account_name}</span>{r.published_at && <span className="text-muted-foreground/60 shrink-0">{fmtApiDate(r.published_at)}</span>}</div>
                              <div className="flex items-center gap-1.5 shrink-0"><span className={c}>{l}</span>{r.publish_url && !r.publish_url.includes("builder/rc/edit") && <a href={r.publish_url} target="_blank" rel="noreferrer" className="text-blue-500 hover:underline">链接</a>}{r.error && <Button variant="ghost" size="sm" className="h-4 px-1 text-[10px] text-red-500" onClick={() => alert(`${videoChannelName(r.channel)}: ${r.error}`)}>详情</Button>}</div>
                            </div>
                          )
                        })}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="hidden md:block">
        <ResizableSplitter
          onResize={(dx) =>
            setRightWidth((w) => Math.max(300, Math.min(640, w - dx)))
          }
        />
      </div>

      {/* ═══ RIGHT: Metadata ═══ */}
      <div style={{ width: typeof window !== 'undefined' && window.innerWidth >= 768 ? rightWidth : undefined }} className="hidden md:block shrink-0 border-l border-border overflow-y-auto scrollbar-none p-3 space-y-3">
        {!detail ? <div className="text-xs text-muted-foreground py-8 text-center">选择视频查看信息</div> : (<>
          <h3 className="text-xs font-semibold text-muted-foreground">视频信息</h3>
          <div className="space-y-1.5"><Label className="text-[10px] text-muted-foreground">标题</Label><Input value={editTitle} onChange={e => updateEditDraft("title", e.target.value)} className="text-xs h-7" /></div>
          <div className="space-y-1.5"><Label className="text-[10px] text-muted-foreground">简介</Label><Textarea value={editDesc} onChange={e => updateEditDraft("description", e.target.value)} rows={2} className="text-xs resize-none" /></div>
          <div className="space-y-1.5"><Label className="text-[10px] text-muted-foreground">标签</Label><Input value={editTags} onChange={e => updateEditDraft("tags", e.target.value)} className="text-xs h-7" placeholder="逗号分隔" /></div>
          <div className="grid grid-cols-[auto_1fr] gap-x-8 gap-y-0.5 text-[10px]">
            <span className="text-muted-foreground">状态</span><span><SBadge s={detail.status} /></span>
            <span className="text-muted-foreground">分辨率</span><span>{detail.width && detail.height ? `${detail.width}x${detail.height}` : "-"}</span>
            <span className="text-muted-foreground">文件大小</span><span>{fmtSize(detail.file_size)}</span>
            <span className="text-muted-foreground">时长</span><span>{fmtDur(detail.duration)}</span>
            <span className="text-muted-foreground">Cite ID</span><span>{detail.source_paper_id || "-"}</span>
            <span className="text-muted-foreground">创建时间</span><span>{detail.created_at ? fmtApiDate(detail.created_at) : "-"}</span>
          </div>
          {detail.script && (() => {
            const scenes = readStoryboard(detail.script)
            return scenes ? (
              <div className="space-y-1.5"><Label className="text-[10px] text-muted-foreground">AI 分镜</Label><div className="bg-muted/50 rounded p-2 text-[10px] space-y-0.5">{scenes.map((scene, i) => (<div key={i} className="text-muted-foreground">{i + 1}. ({scene.duration_sec}s) {truncate(scene.prompt_en, 50)}</div>))}</div></div>
            ) : null
          })()}

          {/* Action buttons */}
          <div data-bottom-bar className="sticky bottom-0 z-10 flex gap-2 flex-wrap border-t border-border bg-background/95 pt-3 pb-2 backdrop-blur supports-backdrop-filter:bg-background/85">
            <Button variant="outline" size="sm"
              onClick={() => patchMut.mutate({ id: detail.id, title: editTitle, description: editDesc, tags: editTags })}>
              保存
            </Button>
            <Button variant="outline" size="sm"
              disabled={patchMut.isPending || detail.status === "approved"}
              onClick={() => patchMut.mutate({ id: detail.id, status: "approved" })}>
              通过
            </Button>
            <Button variant="destructive" size="sm"
              disabled={delMut.isPending}
              onClick={() => { if (confirm("确定删除？") && selectedId) delMut.mutate(selectedId) }}>
              {delMut.isPending ? "删除中…" : "删除"}
            </Button>
          </div>
        </>)}
      </div>

      {/* ═══ Dialogs ═══ */}
      <Dialog open={mobileDetailOpen} onOpenChange={setMobileDetailOpen}>
        <DialogContent className="md:hidden w-[calc(100%-1rem)] max-w-none h-[calc(100dvh-5rem)] p-0 gap-0 overflow-hidden flex flex-col">
          <DialogHeader className="shrink-0 border-b px-4 py-3">
            <DialogTitle className="text-base pr-8">视频详情</DialogTitle>
            {detail && <div className="flex items-center gap-2 text-xs text-muted-foreground"><SBadge s={detail.status} />{detail.source_paper_id && <span>Cite ID {detail.source_paper_id}</span>}</div>}
          </DialogHeader>
          {!detail ? (
            <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">正在加载…</div>
          ) : (
            <div className="flex-1 overflow-y-auto scrollbar-none p-4 space-y-4">
              {detail.file_path ? (
                <AuthenticatedVideo key={detail.id} videoId={detail.id} playsInline className="w-full max-h-[38vh] rounded-lg bg-black" />
              ) : (
                <div className="rounded-lg bg-muted/40 py-10 flex flex-col items-center gap-2 text-muted-foreground"><FileVideo className="w-10 h-10" /><span className="text-xs">{detail.status === "generating" ? "AI 生成中…" : "暂无视频文件"}</span></div>
              )}
              <div className="space-y-1.5"><Label className="text-xs text-muted-foreground">标题</Label><Input value={editTitle} onChange={(e) => updateEditDraft("title", e.target.value)} /></div>
              <div className="space-y-1.5"><Label className="text-xs text-muted-foreground">简介</Label><Textarea value={editDesc} onChange={(e) => updateEditDraft("description", e.target.value)} rows={3} /></div>
              <div className="space-y-1.5"><Label className="text-xs text-muted-foreground">标签</Label><Input value={editTags} onChange={(e) => updateEditDraft("tags", e.target.value)} placeholder="逗号分隔" /></div>
              <div className="grid grid-cols-2 gap-2 rounded-lg bg-muted/30 p-3 text-xs">
                <span className="text-muted-foreground">时长</span><span>{fmtDur(detail.duration)}</span>
                <span className="text-muted-foreground">文件大小</span><span>{fmtSize(detail.file_size)}</span>
                <span className="text-muted-foreground">分辨率</span><span>{detail.width && detail.height ? `${detail.width}×${detail.height}` : "-"}</span>
                <span className="text-muted-foreground">创建时间</span><span>{detail.created_at ? fmtApiDate(detail.created_at) : "-"}</span>
              </div>
              {canManage && (isAccountsError || isAccountsRefetchError) && (
                <QueryErrorNotice
                  className="p-3"
                  message={`账号列表加载失败：${getApiErrorMessage(accountsError, "请稍后重试")}`}
                  onRetry={() => { void refetchAccounts() }}
                  retrying={isAccountsFetching}
                />
              )}
              <AccountSelector accounts={accounts} selected={selectedAccs} onChange={setSelectedAccs} label="发布渠道" />
              <div className="flex flex-wrap gap-2 border-y py-3">
                <Button variant="outline" size="sm" disabled={patchMut.isPending} onClick={() => patchMut.mutate({ id: detail.id, title: editTitle, description: editDesc, tags: editTags })}>保存</Button>
                <Button variant="outline" size="sm" disabled={patchMut.isPending || detail.status === "approved"} onClick={() => patchMut.mutate({ id: detail.id, status: "approved" })}>通过</Button>
                <Button size="sm" disabled={!canManage || selectedAccs.length === 0 || pubMut.isPending} onClick={doPublish}>立即发布</Button>
                <Button size="sm" disabled={!canManage || selectedAccs.length === 0} onClick={() => { setSchDate(""); setSchTime("09:00"); setSchOpen(true) }}>定时发布</Button>
                <Button variant="destructive" size="sm" disabled={delMut.isPending} onClick={() => { if (confirm("确定删除？") && selectedId) delMut.mutate(selectedId) }}>删除</Button>
              </div>
              <div className="rounded-lg border p-3">
                <div className="flex items-center justify-between mb-2"><span className="text-xs font-semibold">发布记录</span><span className="text-xs text-muted-foreground">{pubRecords.length} 条</span></div>
                {pubRecords.length === 0 ? <p className="text-xs text-muted-foreground">暂无记录</p> : (
                  <div className="space-y-2">
                    {pubRecords.map((record) => (
                      <div key={record.id} className="flex items-center justify-between gap-2 text-xs">
                        <span className="truncate">{record.account_name}</span>
                        <span className="text-muted-foreground shrink-0">{record.status === "success" ? "成功" : record.status === "failed" ? "失败" : record.status === "scheduled" ? "已定时" : "发布中"}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={mobilePublishOpen} onOpenChange={setMobilePublishOpen}>
        <DialogContent className="md:hidden w-[calc(100%-1rem)] rounded-xl">
          <DialogHeader><DialogTitle>发布视频</DialogTitle></DialogHeader>
          <div className="space-y-4 py-1">
            <div className="rounded-lg bg-muted/40 px-3 py-2 text-xs">已选择 <b>{checkedIds.length}</b> 个视频</div>
            {canManage && (isAccountsError || isAccountsRefetchError) && (
              <QueryErrorNotice
                className="p-3"
                message={`账号列表加载失败：${getApiErrorMessage(accountsError, "请稍后重试")}`}
                onRetry={() => { void refetchAccounts() }}
                retrying={isAccountsFetching}
              />
            )}
            <AccountSelector accounts={accounts} selected={selectedAccs} onChange={setSelectedAccs} label="发布渠道" />
          </div>
          <DialogFooter><Button className="w-full" disabled={!canManage || checkedIds.length === 0 || selectedAccs.length === 0 || batchPubMut.isPending} onClick={() => batchPubMut.mutate({ ids: checkedIds, aids: selectedAccs })}>{batchPubMut.isPending ? "发布中…" : "立即发布"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={mobileOutboxOpen} onOpenChange={setMobileOutboxOpen}>
        <DialogContent className="md:hidden w-[calc(100%-1rem)] max-w-none h-[min(75dvh,42rem)] p-0 gap-0 overflow-hidden flex flex-col">
          <DialogHeader className="shrink-0 border-b px-4 py-3"><DialogTitle>视频发件箱</DialogTitle></DialogHeader>
          <div className="flex-1 overflow-y-auto scrollbar-none p-3 space-y-2">
            {isVideoOutboxLoading ? <p className="py-10 text-center text-sm text-muted-foreground">加载中…</p> : !(videoOutboxData?.records || []).length ? <p className="py-10 text-center text-sm text-muted-foreground">暂无发布任务</p> : (videoOutboxData?.records || []).map((record) => (
              <div key={record.id} className="rounded-lg border p-3 space-y-1.5">
                <div className="text-sm font-medium line-clamp-2">{record.video_title}</div>
                <div className="flex items-center justify-between gap-2 text-xs"><span className="truncate">{videoChannelName(record.channel)} · {record.account_name}</span><span className={record.status === "success" ? "text-green-600" : record.status === "failed" ? "text-red-600" : record.status === "pending" || record.status === "uploading" ? "text-blue-600" : "text-muted-foreground"}>{record.status === "success" ? "成功" : record.status === "failed" ? "失败" : record.status === "scheduled" ? "已定时" : "发布中"}</span></div>
                {(record.published_at || record.scheduled_at) && <div className="text-[11px] text-muted-foreground">{fmtApiDate(record.published_at || record.scheduled_at)}</div>}
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={showUpload} onOpenChange={(o) => { setShowUpload(o); if (!o) { setUpFile(null); setUpCover(null) } }}>
        <DialogContent className="max-h-[85dvh] overflow-y-auto scrollbar-none sm:max-w-md">
          <DialogHeader className="pr-6"><DialogTitle>上传视频</DialogTitle></DialogHeader>
          <div className="flex flex-col gap-6 py-1">
            {/* 视频文件 */}
            <div className="space-y-2">
              <Label>视频文件 *</Label>
              <div className="border-2 border-dashed border-muted-foreground/25 rounded-lg p-4 text-center hover:border-muted-foreground/50 transition-colors cursor-pointer"
                onClick={() => fileRef.current?.click()}>
                {upFile ? (
                  <div className="flex items-center justify-center gap-2 text-sm">
                    <FileVideo className="w-4 h-4 text-green-600 shrink-0" />
                    <span className="truncate max-w-[200px]">{upFile.name}</span>
                    <span className="text-muted-foreground text-xs shrink-0">{fmtSize(upFile.size)}</span>
                    <button type="button" onClick={e => { e.stopPropagation(); setUpFile(null); if (fileRef.current) fileRef.current.value = "" }}
                      className="text-muted-foreground hover:text-red-500 shrink-0"><X className="w-3.5 h-3.5" /></button>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-1 text-muted-foreground">
                    <Upload className="w-6 h-6" />
                    <span className="text-xs">点击选择视频文件</span>
                    <span className="text-[10px]">支持 MP4 / MOV</span>
                  </div>
                )}
              </div>
              <input ref={fileRef} type="file" accept="video/*" className="sr-only"
                onChange={e => setUpFile(e.target.files?.[0] || null)} />
            </div>

            {/* 封面 */}
            <div className="space-y-2">
              <Label>封面图片（可选）</Label>
              <div className="flex items-center gap-2">
                <Button type="button" variant="outline" className="h-9 text-xs"
                  onClick={() => coverRef.current?.click()}>
                  <ImageIcon className="w-3.5 h-3.5 mr-1" />
                  {upCover ? "更换封面" : "选择封面"}
                </Button>
                {upCover && (
                  <span className="flex items-center gap-1 text-xs text-muted-foreground">
                    <span className="truncate max-w-[140px]">{upCover.name}</span>
                    <button type="button" onClick={() => { setUpCover(null); if (coverRef.current) coverRef.current.value = "" }}
                      className="hover:text-red-500"><X className="w-3 h-3" /></button>
                  </span>
                )}
              </div>
              <input ref={coverRef} type="file" accept="image/*" className="sr-only"
                onChange={e => setUpCover(e.target.files?.[0] || null)} />
            </div>

            {/* 标题 */}
            <div className="space-y-2">
              <Label>标题 *</Label>
              <Input placeholder="输入视频标题" value={upTitle} onChange={e => setUpTitle(e.target.value)} className="h-9 text-sm" />
            </div>

            {/* 简介 */}
            <div className="space-y-2">
              <Label>简介</Label>
              <Textarea placeholder="输入视频简介" rows={2} value={upDesc} onChange={e => setUpDesc(e.target.value)} className="text-sm resize-none" />
            </div>

            {/* 标签 */}
            <div className="space-y-2">
              <Label>标签</Label>
              <Input placeholder="标签，逗号分隔" value={upTags} onChange={e => setUpTags(e.target.value)} className="h-9 text-sm" />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setShowUpload(false)}>取消</Button>
            <Button type="button" disabled={!upTitle || !upFile || uploadMut.isPending}
                onClick={() => uploadMut.mutate({ title: upTitle, file: upFile!, desc: upDesc, tags: upTags, cover: upCover || undefined })}>
              {uploadMut.isPending && <Loader2 className="w-4 h-4 animate-spin mr-1" />}上传
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={schOpen} onOpenChange={setSchOpen}><DialogContent><DialogHeader className="pr-6"><DialogTitle>定时发布</DialogTitle></DialogHeader>
        <div className="flex flex-col gap-4 py-1"><div className="space-y-2"><Label>日期</Label><Input className="h-9" type="date" value={schDate} onChange={e => setSchDate(e.target.value)} /></div><div className="space-y-2"><Label>时间</Label><Input className="h-9" type="time" value={schTime} onChange={e => setSchTime(e.target.value)} /></div></div>
        <DialogFooter><Button variant="outline" onClick={() => setSchOpen(false)}>取消</Button><Button disabled={!schDate} onClick={doSchedule}>定时发布</Button></DialogFooter>
      </DialogContent></Dialog>

      {/* Video stats dialog */}
      <StatsDialog
        open={videoStatsOpen}
        onOpenChange={setVideoStatsOpen}
        type="video"
        title="视频发布统计"
      />
    </div>
  )
}
