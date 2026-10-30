import { useState, type RefObject } from "react"
import { apiClient } from "@/lib/api"
import { getApiErrorMessage } from "@/lib/api-errors"
import { canFormatAPA } from "@/lib/citation"
import type { Paper } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  ChevronRight,
  FileText,
  Film,
  Link as LinkIcon,
  Quote,
  Upload,
} from "lucide-react"
import { EditableDetailFormV2, StructuredClassificationEditor, type ClassificationEditorOptions } from "./paper-detail-dialog"
import { safeArrayStr, safeAuth } from "../lib/paper-format"
import type { PaperDetail } from "../lib/paper-types"

interface CitationCopyState {
  paperId: number
  source: "list" | "detail"
  status: "success" | "error"
  message: string
}

interface PaperDetailViewDialogProps {
  detailPaper: PaperDetail | null
  setDetailPaper: (paper: PaperDetail | null) => void
  showOriginalAbstract: boolean
  setShowOriginalAbstract: (value: boolean | ((previous: boolean) => boolean)) => void
  isMobile: boolean
  role: string
  onGenerateDraft: (paper: Paper) => void
  onGenerateVideo: (paperId: number) => void
  setUpdateDoiText: (value: string) => void
  setUpdateDoiDialog: (open: boolean) => void
  singlePdfInputRef: RefObject<HTMLInputElement | null>
  isUploadingSinglePdf: boolean
  handleSinglePdfUpload: (file: File) => void | Promise<void>
  handleOpenPdf: (paper: Paper) => void | Promise<void>
  handleCopyCitation: (paper: Paper, source: "list" | "detail") => void | Promise<void>
  citationCopyState: CitationCopyState | null
  classificationEditorOptions: ClassificationEditorOptions
  isSavingPaper: boolean
  onSavePaper: (paperId: number, data: Record<string, unknown>) => void
  onOpenDraft: (draftId?: number) => void
  setDeleteReason: (reason: string) => void
  setDeleteDialogPaper: (paper: Paper | null) => void
  onRefetch: () => void
}

function PaperReviewPanel({ paper, setPaper, onRefetch }: {
  paper: PaperDetail
  setPaper: (paper: PaperDetail | null) => void
  onRefetch: () => void
}) {
  const [note, setNote] = useState(paper.review_note || "")
  const [saving, setSaving] = useState(false)
  async function decide(decision: "approve" | "exclude" | "reset") {
    setSaving(true)
    try {
      await apiClient.post(`/api/papers/${paper.id}/review`, {
        decision,
        note: note.trim() || undefined,
        entity_label: paper.entity_label || undefined,
      })
      const response = await apiClient.get(`/api/papers/${paper.id}`)
      setPaper(response.data as PaperDetail)
      setNote(decision === "reset" ? "" : note.trim())
      onRefetch()
    } catch (error: unknown) {
      alert(getApiErrorMessage(error, "审核操作失败"))
    } finally {
      setSaving(false)
    }
  }
  const outcomeLabel = paper.review_outcome === "approved" ? "已通过" : paper.review_outcome === "excluded" ? "已排除" : "未审核"
  return (
    <section className="space-y-1 border-t pt-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[10px] font-semibold text-muted-foreground">论文审核：{outcomeLabel}</p>
        {paper.reviewed_by && <p className="text-[11px] text-muted-foreground">{paper.reviewed_by}{paper.reviewed_at ? ` · ${new Date(paper.reviewed_at).toLocaleString()}` : ""}</p>}
      </div>
      <Textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="审核备注（可选）" rows={2} />
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => void decide("approve")} disabled={saving}>通过</Button>
        <Button size="sm" variant="outline" onClick={() => void decide("exclude")} disabled={saving}>排除</Button>
        <Button size="sm" variant="ghost" onClick={() => void decide("reset")} disabled={saving}>撤销审核</Button>
      </div>
    </section>
  )
}

export function PaperDetailViewDialog({
  detailPaper,
  setDetailPaper,
  showOriginalAbstract,
  setShowOriginalAbstract,
  isMobile,
  role,
  onGenerateDraft,
  onGenerateVideo,
  setUpdateDoiText,
  setUpdateDoiDialog,
  singlePdfInputRef,
  isUploadingSinglePdf,
  handleSinglePdfUpload,
  handleOpenPdf,
  handleCopyCitation,
  citationCopyState,
  classificationEditorOptions,
  isSavingPaper,
  onSavePaper,
  onOpenDraft,
  setDeleteReason,
  setDeleteDialogPaper,
  onRefetch,
}: PaperDetailViewDialogProps) {
  return (
      <Dialog open={!!detailPaper} onOpenChange={(open) => {
        if (!open) {
          setDetailPaper(null)
          setShowOriginalAbstract(false)
        }
      }}>
        <DialogContent className="w-[calc(100%-1rem)] max-w-[calc(100%-1rem)] max-h-[calc(100dvh-1rem)] overflow-x-hidden overflow-y-auto overscroll-contain rounded-xl p-3 pb-[calc(1rem+env(safe-area-inset-bottom,0px))] scrollbar-none sm:max-w-[68rem] sm:p-4">
          <DialogHeader className="min-w-0">
            <DialogTitle className="break-words leading-snug pr-6 text-base">{detailPaper?.title}</DialogTitle>
            <p className="break-words pr-6 text-xs leading-relaxed text-muted-foreground">
              {detailPaper?.title_zh || "暂无中文标题"}
            </p>
            <DialogDescription className="flex min-w-0 flex-wrap gap-x-4 gap-y-0.5 text-xs [&_a]:break-all">
              {detailPaper?.id != null && <span>ID: {detailPaper.id}</span>}
              {detailPaper?.internal_id != null && <span>Cite ID: {detailPaper.internal_id}</span>}
              {detailPaper?.journal_name && <span>{detailPaper.journal_name}</span>}
              {detailPaper?.year && <span>{detailPaper.year}</span>}
              {detailPaper?.impact_factor != null && <span>IF: {detailPaper.impact_factor.toFixed(1)}</span>}
              {detailPaper?.cas_quartile && <span>CAS: {detailPaper.cas_quartile}</span>}
              {detailPaper?.jcr_quartile && <span>JCR: {detailPaper.jcr_quartile}</span>}
              {detailPaper?.doi && (
                <a href={`https://doi.org/${detailPaper.doi}`} target="_blank" rel="noopener noreferrer"
                  className="text-primary hover:underline">DOI: {detailPaper.doi}</a>
              )}
              {detailPaper?.volume && <span>Vol.{detailPaper.volume}{detailPaper?.issue ? `(${detailPaper.issue})` : ""}{detailPaper?.pages ? ` ${detailPaper.pages}` : ""}</span>}
            </DialogDescription>
          </DialogHeader>

          <div className="min-w-0 space-y-2 text-xs">
            <p className="break-words text-muted-foreground">
              <span className="font-semibold">全部作者: </span>
              {(detailPaper?.full_authors?.length
                ? detailPaper.full_authors : detailPaper?.authors?.length
                ? detailPaper.authors : detailPaper?.first_authors?.length
                ? detailPaper.first_authors : detailPaper?.corresponding_authors?.length
                ? detailPaper.corresponding_authors : []).map((a: Record<string, unknown>) => String(a.name || "")).join(", ") || "—"}
            </p>
            {isMobile && detailPaper?.corresponding_authors && (() => {
              const ca = safeAuth(detailPaper.corresponding_authors)
              return ca.length ? (
                <p className="break-words text-muted-foreground">
                  <span className="font-semibold">通讯作者: </span>
                  {ca.map((a) => {
                    const name = String(a.name_zh || a.name || "")
                    const affiliation = String(a.affiliation_zh || a.affiliation_en || a.affiliation || "")
                    return affiliation ? `${name}（${affiliation}）` : name
                  }).join("、")}
                </p>
              ) : null
            })()}
            {role === "reviewer" && (
              <div className="space-y-1 text-muted-foreground">
                {detailPaper?.research_fields && <p><span className="font-semibold">归属类别: </span>{safeArrayStr(detailPaper.research_fields)}</p>}
                {detailPaper?.paradigm && <p><span className="font-semibold">应用范式: </span>{safeArrayStr(detailPaper.paradigm)}</p>}
                {detailPaper?.corresponding_region && <p><span className="font-semibold">地区: </span>{detailPaper.corresponding_region}</p>}
                {detailPaper?.equipment_brand && <p><span className="font-semibold">设备: </span>{safeArrayStr(detailPaper.equipment_brand)}</p>}
                {detailPaper?.notes && <p><span className="font-semibold">备注: </span>{detailPaper.notes}</p>}
              </div>
            )}
          </div>

          {/* Two-column layout for academic, single-column for others */}
          <div className={role === "viewer" || role === "admin"
            ? "grid min-w-0 grid-cols-1 gap-4 text-xs lg:grid-cols-2"
            : "min-w-0 text-xs"}>
            {/* LEFT / ONLY column: Read-only info */}
            <div className="min-w-0 space-y-3">
              {/* Abstract */}
              {(detailPaper?.abstract_zh || detailPaper?.abstract) && (
                <details className="group relative rounded-lg border bg-card/50 p-3">
                  <summary className="-mx-1 flex min-h-8 cursor-pointer list-none items-center gap-1.5 rounded-md px-1 text-xs font-semibold text-muted-foreground transition-colors hover:bg-muted/60 md:pr-36 [&::-webkit-details-marker]:hidden">
                    <ChevronRight className="h-3.5 w-3.5 shrink-0 transition-transform group-open:rotate-90" />
                    <span>摘要</span>
                  </summary>
                  {!isMobile && detailPaper.abstract_zh && detailPaper.abstract && detailPaper.abstract_zh !== detailPaper.abstract && (
                    <div className="absolute right-3 top-3 hidden h-8 items-center group-open:flex">
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => setShowOriginalAbstract((value) => !value)}
                      >
                        {showOriginalAbstract ? "显示中文摘要" : "显示原文摘要"}
                      </Button>
                    </div>
                  )}
                  <div className="mt-2 space-y-2">
                    <p className="whitespace-pre-wrap text-xs leading-relaxed text-foreground/70">
                      {showOriginalAbstract && detailPaper.abstract
                        ? detailPaper.abstract
                        : detailPaper.abstract_zh || detailPaper.abstract}
                    </p>
                    {isMobile && detailPaper.abstract_zh && detailPaper.abstract && detailPaper.abstract_zh !== detailPaper.abstract && (
                      <div className="flex justify-end border-t pt-2">
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() => setShowOriginalAbstract((value) => !value)}
                        >
                          {showOriginalAbstract ? "显示中文摘要" : "显示原文摘要"}
                        </Button>
                      </div>
                    )}
                  </div>
                </details>
              )}
              {/* Chinese conclusions / highlights */}
              {(detailPaper?.conclusions_zh || detailPaper?.highlights_zh) && (
                <div className="border rounded-lg p-3 space-y-2">
                  {detailPaper.conclusions_zh && (
                    <div>
                      <p className="text-xs font-semibold text-muted-foreground">结论</p>
                      <p className="text-xs leading-relaxed">{detailPaper.conclusions_zh}</p>
                    </div>
                  )}
                  {detailPaper.highlights_zh && (
                    <div>
                      <p className="text-xs font-semibold text-muted-foreground">亮点</p>
                      <p className="text-xs leading-relaxed">{detailPaper.highlights_zh}</p>
                    </div>
                  )}
                </div>
              )}
              {/* Actions — 内容生成（受限用户不显示） */}
              {role !== "limited" && (
                <div className="pt-2 border-t space-y-1">
                  <p className="text-[10px] font-semibold text-muted-foreground">内容生成</p>
                  <div className="grid min-w-0 grid-cols-2 gap-2 sm:flex">
                    {detailPaper && (
                      <Button size="sm" variant="outline" className="w-full min-w-0 px-1.5 sm:flex-1 sm:px-2.5"
                        onClick={() => { if (detailPaper) onGenerateDraft(detailPaper) }}
                        disabled={detailPaper.article_status === "generating" || !detailPaper.can_generate}>
                        <FileText className="h-3.5 w-3.5 mr-1" />
                        {detailPaper.article_status === "generating" ? "生成中…" : "生成推文"}
                      </Button>
                    )}
                    {detailPaper && (
                      <Button size="sm" variant="outline" className="w-full min-w-0 px-1.5 sm:flex-1 sm:px-2.5"
                        onClick={() => { onGenerateVideo(detailPaper.id) }}
                        disabled={!detailPaper.can_generate}>
                        <Film className="h-3.5 w-3.5 mr-1" />
                        生成视频
                      </Button>
                    )}
                    {detailPaper?.article_status === "generated" && (
                      <Button size="sm" variant="outline" className="w-full min-w-0 px-1.5 sm:flex-1 sm:px-2.5" onClick={() => {
                        const did = detailPaper?.draft_ids?.[0]
                        onOpenDraft(did)
                      }}>
                        <LinkIcon className="h-3.5 w-3.5 mr-1" />查看推文
                      </Button>
                    )}
                  </div>
                </div>
              )}
              {/* Actions — 论文 PDF（受限用户不显示） */}
              {role !== "limited" && (
                <div className="space-y-1">
                  <p className="text-[10px] font-semibold text-muted-foreground">论文 PDF</p>
                  <div className="grid min-w-0 grid-cols-2 gap-2 sm:flex">
                    <Button size="sm" variant="outline" className="w-full min-w-0 px-1.5 sm:flex-1 sm:px-2.5"
                      onClick={() => { setUpdateDoiText(detailPaper?.doi || ""); setUpdateDoiDialog(true) }}>
                      更新 DOI
                    </Button>
                    <Button size="sm" variant="outline" className="w-full min-w-0 px-1.5 sm:flex-1 sm:px-2.5" onClick={() => singlePdfInputRef.current?.click()} disabled={isUploadingSinglePdf}>
                      <Upload className="h-3.5 w-3.5 mr-1" />
                      {isUploadingSinglePdf ? "上传中..." : detailPaper?.pdf_storage_path ? "替换PDF" : "上传PDF"}
                    </Button>
                    <input ref={singlePdfInputRef} type="file" accept=".pdf" className="hidden"
                      onChange={(e) => { const file = e.target.files?.[0]; if (file) handleSinglePdfUpload(file) }} />
                    {detailPaper?.pdf_storage_path ? (
                      <Button size="sm" variant="outline" className="w-full min-w-0 px-1.5 sm:flex-1 sm:px-2.5"
                        onClick={() => void handleOpenPdf(detailPaper)}>
                        <FileText className="h-3.5 w-3.5 mr-1" />查看PDF
                      </Button>
                    ) : (
                      <div className="hidden sm:block sm:flex-1" />
                    )}
                    <div className="relative flex w-full min-w-0 sm:flex-1">
                      <Button size="sm" variant="outline" className="w-full min-w-0 px-1.5 sm:px-2.5" onClick={() => detailPaper && handleCopyCitation(detailPaper, "detail")}
                        disabled={!detailPaper || !canFormatAPA({ title: detailPaper.title, journal_name: detailPaper.journal_name, year: detailPaper.year })}>
                        <Quote className="h-3.5 w-3.5 mr-1" />复制引用
                      </Button>
                      {detailPaper && citationCopyState?.source === "detail" && citationCopyState.paperId === detailPaper.id && (
                        <span
                          role="status"
                          aria-live="polite"
                          className={`absolute -top-7 left-1/2 z-20 w-max -translate-x-1/2 rounded px-2 py-1 text-[10px] text-white shadow ${citationCopyState.status === "success" ? "bg-emerald-600" : "bg-destructive"}`}
                        >
                          {citationCopyState.status === "success" ? "已复制" : citationCopyState.message}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              )}
              {(role === "admin" || role === "reviewer") && detailPaper?.can_review && (
                <PaperReviewPanel key={detailPaper.id} paper={detailPaper} setPaper={setDetailPaper} onRefetch={onRefetch} />
              )}
              {detailPaper && (
                <div className="hidden lg:block">
                  <StructuredClassificationEditor
                    key={`desktop-classification-${detailPaper.id}`}
                    paper={detailPaper}
                    classificationOptions={classificationEditorOptions}
                    editable={role === "viewer" || role === "admin"}
                    saving={isSavingPaper}
                    onSave={(data) => onSavePaper(detailPaper.id, data)}
                  />
                </div>
              )}
          </div>

          {/* RIGHT column: Edit form only */}
            {(role === "viewer" || role === "admin") && detailPaper && (
              <details
                key={`paper-edit-${detailPaper.id}-${isMobile ? "mobile" : "desktop"}`}
                className={isMobile ? "group min-w-0 rounded-lg border bg-card/50 p-3" : "min-w-0"}
                open={!isMobile ? true : undefined}
              >
                <summary className={isMobile
                  ? "-mx-1 flex min-h-8 cursor-pointer list-none items-center gap-1.5 rounded-md px-1 text-xs font-semibold text-muted-foreground transition-colors hover:bg-muted/60 [&::-webkit-details-marker]:hidden"
                  : "hidden"}>
                  <ChevronRight className="h-3.5 w-3.5 shrink-0 transition-transform group-open:rotate-90" />
                  <span>编辑论文信息</span>
                </summary>
                <div className="min-w-0">
                  <EditableDetailFormV2 key={detailPaper.id}
                    paper={detailPaper}
                    embeddedMobile={isMobile}
                    onSave={(data) => onSavePaper(detailPaper.id, data)}
                    saving={isSavingPaper}
                    onDelete={() => {
                      setDeleteReason("")
                      setDeleteDialogPaper(detailPaper)
                    }}
                  />
                </div>
              </details>
            )}
            {detailPaper && (
              <div className="lg:hidden">
                <details className="group min-w-0 rounded-lg border bg-card/50 p-3">
                  <summary className="-mx-1 flex min-h-8 cursor-pointer list-none items-center gap-1.5 rounded-md px-1 text-xs font-semibold text-muted-foreground transition-colors hover:bg-muted/60 [&::-webkit-details-marker]:hidden">
                    <ChevronRight className="h-3.5 w-3.5 shrink-0 transition-transform group-open:rotate-90" />
                    <span>结构化分类</span>
                  </summary>
                  <div className="pt-3">
                    <StructuredClassificationEditor
                      key={`mobile-classification-${detailPaper.id}`}
                      paper={detailPaper}
                      classificationOptions={classificationEditorOptions}
                      editable={role === "viewer" || role === "admin"}
                      saving={isSavingPaper}
                      onSave={(data) => onSavePaper(detailPaper.id, data)}
                      embeddedMobile
                    />
                  </div>
                </details>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>


  )
}
