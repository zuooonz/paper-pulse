import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import type { Dispatch, MutableRefObject, PointerEvent as ReactPointerEvent, SetStateAction } from "react"
import type { Paper } from "@/lib/types"
import {
  ArrowDown,
  ArrowUp,
  FileText,
  Quote,
  Sparkles,
} from "lucide-react"
import { canFormatAPA } from "@/lib/citation"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { authorAffiliationsText, authorNamesText, authorPairsTitle, paperAuthorEntries, safeArrayStr, truncate } from "../lib/paper-format"
import type { PapersResponse } from "../lib/paper-types"

const DOMAIN_NAME = process.env.NEXT_PUBLIC_DOMAIN_NAME || "qMRI"

// ── Paper card for mobile ──
export function MobilePaperCard({
  paper,
  isSelected,
  onSelect,
  onDeselect,
  onTap,
}: {
  paper: Paper
  isSelected: boolean
  onSelect: (id: number) => void
  onDeselect: (id: number) => void
  onTap: () => void
}) {
  return (
    <div
      className={`rounded-lg border p-2 transition-[background-color,border-color] duration-150 ${
        isSelected ? "bg-accent/50 border-border shadow-sm" : "bg-card border-transparent hover:bg-accent/50 hover:border-border"
      }`}
    >
        <div className="flex items-start gap-2.5">
          <Checkbox
            checked={isSelected}
            onCheckedChange={(checked) => {
              if (checked === true) onSelect(paper.id)
              else onDeselect(paper.id)
            }}
            className="mt-0.5 size-5 shrink-0 bg-background"
            aria-label={isSelected ? "取消选择论文" : "选择论文"}
          />
          <button type="button" onClick={onTap} className="min-w-0 flex-1 space-y-1.5 text-left">
            <div className="flex min-w-0 items-start gap-2">
              <h3 className="min-w-0 flex-1 text-sm font-medium leading-5 line-clamp-2">{paper.title}</h3>
              {paper.entity_label && (
                <Badge variant="outline" className="text-[10px] shrink-0">{paper.entity_label}</Badge>
              )}
            </div>
            <div className="flex min-w-0 items-center gap-x-2 gap-y-1 text-xs text-muted-foreground flex-wrap">
              {paper.journal_name && <span className="max-w-full truncate">{paper.journal_name}</span>}
              {paper.year && <span className="shrink-0">{paper.year}</span>}
              {paper.impact_factor && <span className="shrink-0">IF {paper.impact_factor}</span>}
            </div>
            {paper.first_authors && paper.first_authors.length > 0 && (
              <div className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
                <span className="shrink-0">{truncate(paper.first_authors[0].name, 20)}</span>
                {paper.first_authors[0].affiliation && (
                  <span className="min-w-0 truncate">· {paper.first_authors[0].affiliation}</span>
                )}
              </div>
            )}
          </button>
        </div>
    </div>
  )
}

interface DesktopPaperColumn {
  key: string
  width: number
  pinned?: boolean
}

interface DesktopPaperColumnDef {
  key: string
  label: string
  sortable?: boolean
}

interface CitationCopyState {
  paperId: number
  source: "list" | "detail"
  status: "success" | "error"
  message: string
}

interface DesktopPaperTableProps {
  data?: PapersResponse
  effectiveColumns: DesktopPaperColumn[]
  columnDefs: DesktopPaperColumnDef[]
  totalTableWidth: number
  titlePinned: boolean
  currentPageSelectedCount: number
  isAllSelected: boolean
  selectedIds: Set<number>
  selectAllFiltered: boolean
  setSelectAllFiltered: Dispatch<SetStateAction<boolean>>
  suppressSortClickRef: MutableRefObject<boolean>
  startColumnResize: (event: ReactPointerEvent, key: string) => void
  handleSort: (field: string) => void
  sortBy: string
  sortOrder: "asc" | "desc"
  toggleSelect: (id: number) => void
  toggleSelectAll: () => void
  lastSelectedId: number | null
  setLastSelectedId: Dispatch<SetStateAction<number | null>>
  visiblePapers: Paper[]
  setSelectedIds: Dispatch<SetStateAction<Set<number>>>
  openDetail: (paper: Paper) => void | Promise<void>
  rowPdfUploadId: number | null
  handleOpenPdf: (paper: Paper) => void | Promise<void>
  setAddPdfPaper: Dispatch<SetStateAction<Paper | null>>
  setPdfDownloadResult: Dispatch<SetStateAction<{ success: boolean; message: string; method?: string } | null>>
  setPdfDropHover: Dispatch<SetStateAction<boolean>>
  setAddPdfDialogOpen: Dispatch<SetStateAction<boolean>>
  role: string
  openGenerateDialog: (paper: Paper) => void
  handleCopyCitation: (paper: Paper, source: "list" | "detail") => void | Promise<void>
  citationCopyState: CitationCopyState | null
}

export function DesktopPaperTable({
  data,
  effectiveColumns,
  columnDefs,
  totalTableWidth,
  titlePinned,
  currentPageSelectedCount,
  isAllSelected,
  selectedIds,
  selectAllFiltered,
  setSelectAllFiltered,
  suppressSortClickRef,
  startColumnResize,
  handleSort,
  sortBy,
  sortOrder,
  toggleSelect,
  toggleSelectAll,
  lastSelectedId,
  setLastSelectedId,
  visiblePapers,
  setSelectedIds,
  openDetail,
  rowPdfUploadId,
  handleOpenPdf,
  setAddPdfPaper,
  setPdfDownloadResult,
  setPdfDropHover,
  setAddPdfDialogOpen,
  role,
  openGenerateDialog,
  handleCopyCitation,
  citationCopyState,
}: DesktopPaperTableProps) {
  return (
    <div className="hidden md:block">
          <Table className="table-fixed" style={{ width: titlePinned ? totalTableWidth : `max(${totalTableWidth}px, 100%)` }} containerClassName="overflow-x-visible!">
            <TableHeader>
              <TableRow>
                <TableHead className="w-10 text-center">
                  <input
                    type="checkbox"
                    checked={isAllSelected}
                    ref={(el) => {
                      if (el) el.indeterminate = currentPageSelectedCount > 0 && !isAllSelected
                    }}
                    onChange={() => toggleSelectAll()}
                    className="accent-primary cursor-pointer"
                    aria-label="全选当前页"
                    title="全选当前页"
                  />
                </TableHead>
                {effectiveColumns.map((col) => {
                    const def = columnDefs.find((d) => d.key === col.key)
                  if (!def) return null
                  const active = def.sortable && sortBy === col.key
                  return (
                    <TableHead
                      key={col.key}
                      style={col.key === "title" && !col.pinned ? undefined : { width: col.width }}
                      onClick={def.sortable ? () => {
                        if (suppressSortClickRef.current) {
                          suppressSortClickRef.current = false
                          return
                        }
                        handleSort(col.key)
                      } : undefined}
                      className={`relative select-none text-center ${def.sortable ? "cursor-pointer" : ""}`}
                    >
                      <div className="flex items-center justify-center gap-1">
                        {def.label}
                        {active && (
                          sortOrder === "asc" ? (
                            <ArrowUp className="h-3 w-3 text-primary shrink-0" />
                          ) : (
                            <ArrowDown className="h-3 w-3 text-primary shrink-0" />
                          )
                        )}
                      </div>
                      <span
                        onPointerDown={(e) => startColumnResize(e, col.key)}
                        onClick={(e) => e.stopPropagation()}
                        className="absolute right-0 top-0 h-full w-3 cursor-col-resize touch-none hover:bg-primary/20"
                        title="拖动调整列宽"
                      />
                    </TableHead>
                  )
                })}
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.items?.map((paper, idx) => (
                <TableRow
                  key={paper.id}
                  data-state={selectAllFiltered || selectedIds.has(paper.id) ? "selected" : undefined}
                  className="cursor-pointer select-none"
                  onClick={(e) => {
                    if (e.ctrlKey || e.metaKey) {
                      e.preventDefault()
                      toggleSelect(paper.id)
                    } else if (e.shiftKey && lastSelectedId !== null && data?.items) {
                      e.preventDefault()
                      const ids = data.items.map((p) => p.id)
                      const startIdx = ids.indexOf(lastSelectedId)
                      const endIdx = idx
                        if (startIdx !== -1) {
                          const [min, max] = [Math.min(startIdx, endIdx), Math.max(startIdx, endIdx)]
                          const rangeIds = ids.slice(min, max + 1)
                          const shouldRemove = selectAllFiltered || selectedIds.has(paper.id)
                          setSelectedIds((prev) => {
                            const next = selectAllFiltered
                              ? new Set(visiblePapers.map((item) => item.id))
                              : new Set(prev)
                            rangeIds.forEach((rid) => {
                              if (shouldRemove) next.delete(rid)
                              else next.add(rid)
                            })
                            return next
                          })
                            setSelectAllFiltered(false)
                        }
                      setLastSelectedId(paper.id)
                    } else {
                      openDetail(paper)
                    }
                  }}
                >
                  <TableCell className="w-10 text-center">
                    <input
                      type="checkbox"
                      checked={selectAllFiltered || selectedIds.has(paper.id)}
                      onClick={(e) => {
                        e.stopPropagation()
                        toggleSelect(paper.id)
                      }}
                      className="accent-primary cursor-pointer"
                      aria-label={`选择论文 ${paper.internal_id ?? paper.id}`}
                    />
                  </TableCell>
                  {effectiveColumns.map((col) => {
                    switch (col.key) {
                      case "internal_id":
                        return (
                          <TableCell key={col.key} style={{ width: col.width }} className="text-muted-foreground text-xs text-center">
                            {paper.internal_id ?? "—"}
                          </TableCell>
                        )
                      case "title":
                        return (
                          <TableCell key={col.key} style={col.pinned ? { width: col.width } : undefined} title={paper.title ?? ""}>
                            <div className="flex min-w-0 items-center gap-1.5">
                              <div className="text-sm leading-snug truncate">{paper.title ?? "—"}</div>
                            </div>
                            {paper.title_zh && (
                              <div className="text-xs text-muted-foreground truncate mt-0.5">
                                {paper.title_zh}
                              </div>
                            )}
                          </TableCell>
                        )
                      case "first_author": {
                        const list = paperAuthorEntries(paper.first_authors)
                        return (
                          <TableCell key={col.key} style={{ width: col.width }} className="text-xs truncate" title={authorPairsTitle(list)}>
                            {authorNamesText(list)}
                          </TableCell>
                        )
                      }
                      case "first_author_affiliation": {
                        const list = paperAuthorEntries(paper.first_authors)
                        return (
                          <TableCell key={col.key} style={{ width: col.width }} className="text-xs text-muted-foreground truncate" title={authorPairsTitle(list)}>
                            {authorAffiliationsText(list)}
                          </TableCell>
                        )
                      }
                      case "corresponding_author": {
                        const list = paperAuthorEntries(paper.corresponding_authors)
                        return (
                          <TableCell key={col.key} style={{ width: col.width }} className="text-xs truncate" title={authorPairsTitle(list)}>
                            {authorNamesText(list)}
                          </TableCell>
                        )
                      }
                      case "corresponding_author_affiliation": {
                        const list = paperAuthorEntries(paper.corresponding_authors)
                        return (
                          <TableCell key={col.key} style={{ width: col.width }} className="text-xs text-muted-foreground truncate" title={authorPairsTitle(list)}>
                            {authorAffiliationsText(list)}
                          </TableCell>
                        )
                      }
                      case "region":
                        return (
                          <TableCell key={col.key} style={{ width: col.width }} className="text-xs text-center" title={paper.corresponding_region ?? ""}>
                            {paper.corresponding_region || "—"}
                          </TableCell>
                        )
                      case "subject_diseases": {
                        const text = safeArrayStr(paper.subject_diseases ?? paper.subject_disease ?? null) || "—"
                        return (
                          <TableCell key={col.key} style={{ width: col.width }} className="text-xs text-muted-foreground truncate" title={text}>
                            {text}
                          </TableCell>
                        )
                      }
                      case "journal_name":
                        return (
                          <TableCell
                            key={col.key}
                            style={{ width: col.width }}
                            className="text-xs text-muted-foreground truncate"
                            title={paper.journal_name ?? ""}
                          >
                            {paper.journal_name ?? "—"}
                          </TableCell>
                        )
                      case "year":
                        return (
                          <TableCell key={col.key} style={{ width: col.width }} className="text-xs text-center">
                            {paper.year ?? "—"}
                          </TableCell>
                        )
                      case "impact_factor":
                        return (
                          <TableCell key={col.key} style={{ width: col.width }} className="text-xs text-center">
                            {paper.impact_factor?.toFixed(1) ?? "—"}
                          </TableCell>
                        )
                      case "cas_quartile":
                        return (
                          <TableCell key={col.key} style={{ width: col.width }} className="text-xs text-center">
                            {paper.jcr_quartile ?? "—"}
                          </TableCell>
                        )
                      case "entity_label":
                        return (
                          <TableCell key={col.key} style={{ width: col.width }} className="text-xs text-center">
                            {paper.entity_label ? (
                              <Badge variant="default" className="text-[10px] px-1.5 py-0 bg-primary hover:bg-primary/90">{paper.entity_label}</Badge>
                            ) : (
                              <Badge variant="secondary" className="text-[10px] px-1.5 py-0">{DOMAIN_NAME}</Badge>
                            )}
                          </TableCell>
                        )
                      case "review_outcome": {
                        if (!paper.can_review) {
                          return <TableCell key={col.key} style={{ width: col.width }} className="text-center text-[11px] text-muted-foreground">
                            —
                          </TableCell>
                        }
                        const labels: Record<string, string> = { unreviewed: "未审核", approved: "已通过", excluded: "已排除" }
                        return <TableCell key={col.key} style={{ width: col.width }} className="text-center">
                          <Badge variant={paper.review_outcome === "approved" ? "default" : paper.review_outcome === "excluded" ? "outline" : "secondary"} className="px-1.5 py-0 text-[10px]">
                            {labels[paper.review_outcome] || paper.review_outcome}
                          </Badge>
                        </TableCell>
                      }
                      case "metadata_complete":
                        return <TableCell key={col.key} style={{ width: col.width }} className="text-center text-[11px] text-muted-foreground">
                          {paper.metadata_complete ? "完整" : "不全"}
                        </TableCell>
                      case "attachments":
                        return (
                          <TableCell key={col.key} style={{ width: col.width }} className="text-center">
                            <div className="flex items-center justify-center gap-1">
                              <Button
                                size="xs"
                                variant={paper.has_pdf ? "default" : "outline"}
                                className="h-7 w-7 p-0"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  if (paper.has_pdf) {
                                    void handleOpenPdf(paper)
                                  } else {
                                    setAddPdfPaper(paper)
                                    setPdfDownloadResult(null)
                                    setPdfDropHover(false)
                                    setAddPdfDialogOpen(true)
                                  }
                                }}
                                disabled={
                                  rowPdfUploadId === paper.id ||
                                  paper.is_processing
                                }
                                title={
                                  paper.is_processing
                                    ? "处理中…"
                                    : paper.has_pdf
                                    ? "查看 PDF"
                                    : "添加 PDF"
                                }
                              >
                                {rowPdfUploadId === paper.id ||
                                paper.is_processing ? (
                                  <span className="text-[10px]">…</span>
                                ) : (
                                  <FileText className="h-3 w-3" />
                                )}
                              </Button>
                            </div>
                          </TableCell>
                        )
                      case "article":
                        return (
                          <TableCell key={col.key} style={{ width: col.width }} className="text-center">
                            <div className="flex items-center justify-center gap-1">
                              <Button
                                size="icon-sm"
                                variant={paper.article_status === "generated" ? "default" : "outline"}
                                className="h-7 w-7 p-0"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  if (role !== "limited" && paper.can_generate) {
                                    openGenerateDialog(paper)
                                  }
                                }}
                                disabled={
                                  role === "limited" || !paper.can_generate || paper.article_status === "generating"
                                }
                                title={
                                  role === "limited"
                                    ? "当前账号无生成权限"
                                      : paper.article_status === "generating"
                                    ? "推文生成中…"
                                    : !paper.can_generate
                                    ? "论文需完成处理并审核通过后才能生成"
                                    : paper.article_status === "generated"
                                    ? "重新生成推文"
                                    : "生成推文"
                                }
                              >
                                <Sparkles className={paper.article_status === "generating" ? "h-3.5 w-3.5 animate-pulse" : "h-3.5 w-3.5"} />
                              </Button>
                            </div>
                          </TableCell>
                        )
                      case "citation":
                        return (
                          <TableCell key={col.key} style={{ width: col.width }} className="text-center">
                            <div className="relative inline-flex">
                              <Button
                                size="xs"
                                variant="ghost"
                                className="h-7 w-7 p-0"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  handleCopyCitation(paper, "list")
                                }}
                                disabled={!canFormatAPA({
                                  title: paper.title,
                                  journal_name: paper.journal_name,
                                  year: paper.year,
                                })}
                                title="复制 APA 引用"
                              >
                                <Quote className="h-3 w-3" />
                              </Button>
                              {citationCopyState?.source === "list" && citationCopyState.paperId === paper.id && (
                                <span
                                  role="status"
                                  aria-live="polite"
                                  className={`absolute -top-7 left-1/2 z-20 w-max -translate-x-1/2 rounded px-2 py-1 text-[10px] text-white shadow ${citationCopyState.status === "success" ? "bg-emerald-600" : "bg-destructive"}`}
                                >
                                  {citationCopyState.status === "success" ? "已复制" : citationCopyState.message}
                                </span>
                              )}
                            </div>
                          </TableCell>
                        )
                      default:
                        return null
                    }
                  })}
                </TableRow>
              ))}
            </TableBody>
          </Table>


    </div>
  )
}
