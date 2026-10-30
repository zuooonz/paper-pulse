"use client"

import type { ReactNode } from "react"
import { useEffect, useState } from "react"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { PublishTargetSelector } from "./publish-target-selector"
import type { Account, Draft } from "@/lib/types"
import type { PaperImage } from "@/hooks/use-drafts"
import { apiClient } from "@/lib/api"

export type DraftEditorOperation = "save" | "approve" | "reject" | "publish" | "schedule" | "delete" | "batchPublish" | "batchSchedule"
export type DraftDetailView = "edit" | "preview" | "images"

function renderInline(text: string): ReactNode {
  return text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={index}>{part.slice(2, -2)}</strong>
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return <em key={index}>{part.slice(1, -1)}</em>
    }
    return <span key={index}>{part}</span>
  })
}

export function TweetPreview({ title, content }: { title: string; content: string }) {
  const body = content.replace(/^(#\s+)?\s*【(?:实体|课题)】[^\n]*\n?/, "").trim().replace(/^#\s+.+(\n|$)/, "").trim()
  const blocks = body ? body.split(/\n{2,}/) : []
  return (
    <div className="p-4">
      <p className="text-xs font-medium text-muted-foreground mb-3">推文预览</p>
      <div className="rounded-2xl overflow-hidden shadow-sm border border-border mx-auto">
        <div className="p-5 max-h-[600px] overflow-auto">
          <h2 className="text-lg font-bold text-foreground mb-4 leading-snug">{title || "（无标题）"}</h2>
          <div className="space-y-2.5 text-sm text-muted-foreground leading-relaxed">
            {blocks.length === 0 ? <p>（暂无内容）</p> : blocks.map((block, blockIndex) => {
              const heading = block.match(/^(#{1,3})\s+(.+)$/)
              if (heading) {
                const tag = heading[1].length === 1 ? "h3" : heading[1].length === 2 ? "h4" : "h5"
                const className = tag === "h3"
                  ? "text-[17px] font-bold text-foreground"
                  : tag === "h4"
                    ? "text-[15px] font-semibold text-foreground"
                    : "text-[13px] font-semibold text-foreground"
                const Heading = tag
                return <Heading key={blockIndex} className={className}>{renderInline(heading[2])}</Heading>
              }
              return (
                <p key={blockIndex}>
                  {block.split("\n").map((line, lineIndex) => (
                    <span key={lineIndex}>
                      {lineIndex > 0 && <br />}
                      {renderInline(line)}
                    </span>
                  ))}
                </p>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}

function DraftImage({ imagePath, alt }: { imagePath: string; alt: string }) {
  const [src, setSrc] = useState("")
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let objectUrl: string | undefined
    const controller = new AbortController()
    apiClient
      .get(`/api/drafts/images/${encodeURIComponent(imagePath)}`, {
        params: { thumbnail: true, size: 512 },
        responseType: "blob",
        signal: controller.signal,
      })
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
  }, [imagePath])
  if (!src) return <div className="flex h-full w-full items-center justify-center bg-muted text-[10px] text-muted-foreground">{failed ? "无法加载" : "加载中"}</div>
  return (
    // The image is fetched with the Authorization header and kept as a local
    // object URL, so the API token never appears in a URL or DOM attribute.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      className="h-full w-full object-cover"
      loading="lazy"
      decoding="async"
    />
  )
}

export function PaperImageGrid({
  images,
  selectedPaths,
  onToggle,
}: {
  images: PaperImage[]
  selectedPaths: string[]
  onToggle: (path: string) => void
}) {
  const filtered = images.filter((image) => image.size_kb >= 50 || image.size_kb === 0).slice(0, 9)
  if (filtered.length === 0) return <p className="text-xs text-muted-foreground py-3 text-center">无可用图片</p>
  return (
    <div className="mt-3">
      <p className="text-xs text-muted-foreground mb-2">论文配图 · 点击切换选中</p>
      <div className="grid grid-cols-3 gap-1.5">
        {filtered.map((image) => {
          const isSelected = selectedPaths.includes(image.path)
          return (
            <button
              key={image.path}
              onClick={() => onToggle(image.path)}
              className={["relative aspect-square rounded overflow-hidden border-2 transition-all", isSelected ? "border-primary ring-1 ring-primary" : "border-transparent opacity-80 hover:opacity-100"].join(" ")}
              title={image.caption || `${image.size_kb}KB`}
            >
              <DraftImage imagePath={image.path} alt={image.caption || "paper image"} />
              {isSelected && <div className="absolute inset-0 bg-primary/20 flex items-center justify-center"><div className="w-6 h-6 bg-primary text-primary-foreground rounded-full flex items-center justify-center text-xs font-bold shadow-md">✓</div></div>}
            </button>
          )
        })}
      </div>
      <p className="text-[10px] text-muted-foreground mt-1">已选 {selectedPaths.length} 张 · 最多 9 张</p>
    </div>
  )
}

export function DraftEditor({
  layout,
  view = "edit",
  draft,
  title,
  content,
  selectedImagePaths,
  onTitleChange,
  onContentChange,
  onToggleImage,
  accounts,
  selectedAccountIds,
  onSelectedAccountIdsChange,
  canPublish,
  hasPublishTarget,
  isMutating,
  deletePending,
  selectedCount = 0,
  availableOperations,
  onSave,
  onApprove,
  onReject,
  onPublish,
  onSchedule,
  onDelete,
  onBatchPublish,
  onBatchSchedule,
  batchPublishPending,
  batchSchedulePending,
  batchPublishLabel,
  batchScheduleLabel,
  children,
}: {
  layout: "desktop" | "mobile"
  view?: DraftDetailView
  draft: Draft & { paper_images?: PaperImage[] } | null
  title: string
  content: string
  selectedImagePaths: string[]
  onTitleChange: (value: string) => void
  onContentChange: (value: string) => void
  onToggleImage: (path: string) => void
  accounts: Account[]
  selectedAccountIds: number[]
  onSelectedAccountIdsChange: (ids: number[]) => void
  canPublish: boolean
  hasPublishTarget: boolean
  isMutating: boolean
  deletePending: boolean
  selectedCount?: number
  availableOperations: DraftEditorOperation[]
  onSave: () => void
  onApprove: () => void
  onReject?: () => void
  onPublish: () => void
  onSchedule: () => void
  onDelete: () => void
  onBatchPublish?: () => void
  onBatchSchedule?: () => void
  batchPublishPending?: boolean
  batchSchedulePending?: boolean
  batchPublishLabel?: string
  batchScheduleLabel?: string
  children?: ReactNode
}) {
  const can = (operation: DraftEditorOperation) => availableOperations.includes(operation)
  const toggleImage = (path: string) => onToggleImage(path)

  if (view === "preview") return <TweetPreview title={title} content={content} />
  if (view === "images") {
    return draft?.paper_images?.length
      ? <PaperImageGrid images={draft.paper_images} selectedPaths={selectedImagePaths} onToggle={toggleImage} />
      : <p className="py-10 text-center text-sm text-muted-foreground">暂无配图</p>
  }

  const mobile = layout === "mobile"
  return (
    <div className={mobile ? "flex flex-1 flex-col gap-4" : "flex flex-col gap-3 md:gap-4"}>
      <div className={mobile ? "space-y-1.5" : undefined}>
        {mobile ? <Label className="text-xs text-muted-foreground">标题</Label> : <label className="text-xs text-muted-foreground mb-1 block">标题</label>}
        <Input value={title} onChange={(event) => onTitleChange(event.target.value)} placeholder="推文标题" />
      </div>
      <div className={mobile ? "space-y-1.5" : "flex flex-col"}>
        {mobile ? <Label className="text-xs text-muted-foreground">正文（Markdown）</Label> : <label className="text-xs text-muted-foreground mb-1 block">正文（Markdown）</label>}
        <Textarea
          value={content}
          onChange={(event) => onContentChange(event.target.value)}
          placeholder={mobile ? undefined : "推文正文…"}
          className={mobile ? "font-mono text-sm min-h-64 resize-y" : "font-mono text-sm h-80 resize-none"}
        />
      </div>
      <PublishTargetSelector
        label="发布渠道"
        accounts={accounts}
        selected={selectedAccountIds}
        onChange={onSelectedAccountIdsChange}
        disabled={false}
      />
      <div
        data-bottom-bar
        className={mobile
          ? "sticky bottom-0 z-10 mt-auto flex flex-wrap gap-2 border-y bg-background/95 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom,0px))] backdrop-blur supports-backdrop-filter:bg-background/85"
          : "sticky bottom-0 z-10 flex flex-wrap gap-2 border-t border-border bg-background/95 pt-3 pb-2 backdrop-blur supports-backdrop-filter:bg-background/85"}
      >
        {can("save") && <Button variant="outline" size="sm" disabled={isMutating} onClick={onSave}>保存</Button>}
        {can("approve") && <Button variant="outline" size="sm" disabled={isMutating} onClick={onApprove}>通过</Button>}
        {can("reject") && onReject && <Button variant="destructive" size="sm" disabled={isMutating} onClick={onReject}>驳回</Button>}
        {can("publish") && <Button variant="default" size="sm" disabled={!canPublish || isMutating || !hasPublishTarget || (!mobile && selectedCount > 1)} onClick={onPublish}>立即发布</Button>}
        {can("schedule") && <Button variant="default" size="sm" disabled={!canPublish || isMutating || !hasPublishTarget || (!mobile && selectedCount > 1)} onClick={onSchedule}>定时发布</Button>}
        {can("batchPublish") && <Button variant="default" size="sm" disabled={!canPublish || batchPublishPending || selectedCount < 2 || !hasPublishTarget} onClick={onBatchPublish}>{batchPublishLabel ?? `批量发布 (${selectedCount})`}</Button>}
        {can("batchSchedule") && <Button variant="secondary" size="sm" disabled={!canPublish || batchSchedulePending || selectedCount < 2 || !hasPublishTarget} onClick={onBatchSchedule}>{batchScheduleLabel ?? `批量定时 (${selectedCount})`}</Button>}
        {can("delete") && <Button variant="destructive" size="sm" disabled={deletePending} onClick={onDelete}>{deletePending && !mobile ? "删除中…" : "删除"}</Button>}
        {children}
      </div>
    </div>
  )
}

export function DraftPreviewPane({
  title,
  content,
  draft,
  selectedImagePaths,
  onToggleImage,
}: {
  title: string
  content: string
  draft: Draft & { paper_images?: PaperImage[] } | null
  selectedImagePaths: string[]
  onToggleImage: (path: string) => void
}) {
  return (
    <div className="flex-1 overflow-y-auto scrollbar-none">
      <TweetPreview title={title} content={content} />
      {draft?.paper_images && <div className="px-4 pb-4"><PaperImageGrid images={draft.paper_images} selectedPaths={selectedImagePaths} onToggle={onToggleImage} /></div>}
    </div>
  )
}
