import type { Dispatch, SetStateAction } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { ArrowDown, ArrowUp, ArrowUpDown, Columns3, FileSpreadsheet, FileText, Filter, Search, Sparkles, Upload, X } from "lucide-react"
import type { Paper } from "@/lib/types"
import type { ConfigOptions, PapersResponse } from "../lib/paper-types"

const MOBILE_SORTABLE_COLUMNS = [
  { key: "internal_id", label: "Cite ID" },
  { key: "year", label: "发表年份" },
  { key: "impact_factor", label: "IF" },
  { key: "title", label: "标题" },
  { key: "journal_name", label: "期刊" },
]

interface MobileSortPanelProps {
  showMobileSort: boolean
  setShowMobileSort: Dispatch<SetStateAction<boolean>>
  setSortBy: (sort: string) => void
  setSortOrder: (order: "asc" | "desc") => void
  setPage: (page: number) => void
  syncURL: (params: Record<string, string>) => void
  sortBy: string
  sortOrder: "asc" | "desc"
}

export function MobileSortPanel(props: MobileSortPanelProps) {
  const { showMobileSort, setShowMobileSort, setSortBy, setSortOrder, setPage, syncURL, sortBy, sortOrder } = props
  return (
    <>
      {showMobileSort && (
        <>
          <div className="md:hidden fixed inset-0 bg-black/30 z-30" onClick={() => setShowMobileSort(false)} />
          <div className="md:hidden fixed left-3 right-3 bottom-3 z-40 rounded-xl border bg-card shadow-xl p-3 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold">排序</span>
              <Button size="icon-sm" variant="ghost" onClick={() => setShowMobileSort(false)} aria-label="关闭排序">
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {MOBILE_SORTABLE_COLUMNS.map((column) => (
                <button
                  key={column.key}
                  type="button"
                  onClick={() => { setSortBy(column.key); setPage(1); syncURL({ sort: column.key, page: "1" }) }}
                  className={`rounded-lg border px-3 py-2 text-xs text-left transition-colors ${sortBy === column.key ? "border-primary bg-accent text-foreground" : "border-border text-muted-foreground"}`}
                >
                  {column.label}
                </button>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Button
                size="sm"
                variant={sortOrder === "asc" ? "default" : "outline"}
                onClick={() => { setSortOrder("asc"); setPage(1); syncURL({ order: "asc", page: "1" }) }}
              >
                <ArrowUp className="h-3.5 w-3.5 mr-1" />升序
              </Button>
              <Button
                size="sm"
                variant={sortOrder === "desc" ? "default" : "outline"}
                onClick={() => { setSortOrder("desc"); setPage(1); syncURL({ order: "desc", page: "1" }) }}
              >
                <ArrowDown className="h-3.5 w-3.5 mr-1" />降序
              </Button>
            </div>
            <Button className="w-full" size="sm" onClick={() => setShowMobileSort(false)}>完成</Button>
          </div>
        </>
      )}
    </>

  )
}

interface QuotaSnapshot {
  enabled: boolean
  limit: number | null
  used: number
  reserved: number
  remaining: number | null
}

interface PapersWorkspaceToolbarProps {
  role: string
  selectedIds: Set<number>
  setSelectedIds: Dispatch<SetStateAction<Set<number>>>
  setLastSelectedId: Dispatch<SetStateAction<number | null>>
  selectAllFiltered: boolean
  setSelectAllFiltered: Dispatch<SetStateAction<boolean>>
  toggleSelectAllFiltered: () => void
  data?: PapersResponse
  quotaSnapshot?: QuotaSnapshot
  openBatchGenerateDialog: () => void
  setReviewProfile: Dispatch<SetStateAction<string>>
  setReviewThemes: Dispatch<SetStateAction<string[]>>
  setReviewModel: Dispatch<SetStateAction<string>>
  setReviewDialogOpen: Dispatch<SetStateAction<boolean>>
  configOptions?: ConfigOptions
  deletePending: boolean
  setDeleteReason: Dispatch<SetStateAction<string>>
  setDeleteDialogPaper: Dispatch<SetStateAction<Paper | null>>
  isFetching: boolean
  isLoading: boolean
  setColumnDialogOpen: Dispatch<SetStateAction<boolean>>
  canUpload: boolean
  openUploadDialog: () => void
  handleExport: () => void | Promise<void>
  isExporting: boolean
  handleBatchDownloadPdf: () => void | Promise<void>
  isDownloading: boolean
  setWordSummaryModel: Dispatch<SetStateAction<string>>
  setWordSummaryDialogOpen: Dispatch<SetStateAction<boolean>>
}

export function PapersWorkspaceToolbar(props: PapersWorkspaceToolbarProps) {
  const {
    role, selectedIds, setSelectedIds, setLastSelectedId, selectAllFiltered,
    setSelectAllFiltered, toggleSelectAllFiltered, data, quotaSnapshot,
    openBatchGenerateDialog, setReviewProfile, setReviewThemes, setReviewModel,
    setReviewDialogOpen, configOptions, deletePending, setDeleteReason,
    setDeleteDialogPaper, isFetching, isLoading, setColumnDialogOpen, canUpload,
    openUploadDialog, handleExport, isExporting, handleBatchDownloadPdf,
    isDownloading, setWordSummaryModel, setWordSummaryDialogOpen,
  } = props
  return (
        <div className="flex items-center justify-between pl-3 pr-3 md:pr-6 py-3 md:py-4 border-b gap-2 overflow-x-auto">
          <div className="hidden md:flex items-center gap-3 shrink-0">
            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground" title="全选所有筛选结果（不限当前页）">
              <input
                type="checkbox"
                checked={selectAllFiltered}
                ref={(el) => {
                  if (el) el.indeterminate = selectedIds.size > 0 && !selectAllFiltered
                }}
                onChange={() => toggleSelectAllFiltered()}
                className="accent-primary cursor-pointer"
                aria-label="全选所有筛选结果"
              />
              <span>全选筛选结果</span>
            </label>
            <span className="text-xs text-muted-foreground">
              已选择{" "}
              <span className="font-medium text-foreground">
                {selectAllFiltered ? data?.total ?? 0 : selectedIds.size}
              </span>{" "}
              篇
              {selectAllFiltered && (
                <span className="text-primary ml-1">（全部 {data?.total ?? 0} 篇）</span>
              )}
            </span>
            {role === "limited" && quotaSnapshot && (
              <Badge variant={quotaSnapshot.enabled && quotaSnapshot.remaining === 0 ? "destructive" : "secondary"}>
                {quotaSnapshot.enabled
                  ? `今日额度 ${quotaSnapshot.used + quotaSnapshot.reserved}/${quotaSnapshot.limit}`
                  : "额度限制未启用"}
              </Badge>
            )}
            <Button
              size="sm"
              variant="ghost"
              disabled={selectedIds.size === 0 && !selectAllFiltered}
              onClick={() => {
                setSelectedIds(new Set())
                setLastSelectedId(null)
                setSelectAllFiltered(false)
              }}
            >
              取消选择
            </Button>
            {role !== "limited" && (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={selectedIds.size < 2 && !selectAllFiltered}
                  onClick={openBatchGenerateDialog}
                >
                  批量生成推文
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={selectedIds.size < 2}
                  onClick={() => {
                    setReviewProfile("default")
                    setReviewThemes(configOptions?.themes[0] ? [configOptions.themes[0].value] : [])
                    setReviewModel("auto")
                    setReviewDialogOpen(true)
                  }}
                >
                  生成综述推文
                </Button>
              </>
            )}
            {(role === "admin" || role === "viewer") && (
              <Button
                size="sm"
                variant="destructive"
                disabled={selectedIds.size !== 1 || deletePending}
                onClick={() => {
                  const id = Array.from(selectedIds)[0]
                  const paper = data?.items.find((p) => p.id === id)
                  if (paper) {
                    setDeleteReason("")
                    setDeleteDialogPaper(paper)
                  }
                }}
              >
                {deletePending ? "删除中..." : "删除"}
              </Button>
            )}
          </div>
          <div className="hidden md:flex items-center gap-3 shrink-0">
            {isFetching && !isLoading && (
              <span className="text-xs text-muted-foreground">加载中...</span>
            )}
            {role !== "limited" && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setColumnDialogOpen(true)}
                title="自定义要显示的列、顺序和列宽"
              >
                <Columns3 className="h-4 w-4 mr-1" />
                显示列
              </Button>
            )}
            {canUpload && (
              <Button
                size="sm"
                variant="outline"
                onClick={openUploadDialog}
              >
                <Upload className="h-4 w-4 mr-1" />
                导入论文
              </Button>
            )}
            {/* 导出 Excel - 所有角色可见（销售角色导出简化版本） */}
            <Button
              size="sm"
              variant="outline"
              onClick={handleExport}
              disabled={isExporting || (!data?.items?.length) || (selectedIds.size === 0 && !selectAllFiltered)}
            >
              <FileSpreadsheet className="h-4 w-4 mr-1" />
              {isExporting ? "导出中..." : "导出 Excel"}
            </Button>
            <div className="hidden md:flex items-center gap-2">
            {role !== "limited" && (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={(selectedIds.size === 0 && !selectAllFiltered) || isDownloading}
                  onClick={handleBatchDownloadPdf}
                >
                  <FileText className="h-4 w-4 mr-1" />
                  {isDownloading ? "打包中..." : "下载 PDF"}
                </Button>
              </>
            )}
            <Button
              size="sm"
              variant="outline"
              disabled={selectedIds.size < 1 && !selectAllFiltered}
              onClick={() => {
                setWordSummaryModel("auto")
                setWordSummaryDialogOpen(true)
              }}
            >
              <Sparkles className="h-4 w-4 mr-1" />
              总结论文
            </Button>
            </div>
          </div>
        </div>


  )
}

interface PapersWorkspaceFooterProps {
  data?: PapersResponse
  isFetching: boolean
  isLoading: boolean
  page: number
  totalPages: number
  mobileTotal: number
  selectedIds: Set<number>
  selectAllFiltered: boolean
  toggleSelectAllFiltered: () => void
  setPage: (page: number) => void
  setLastSelectedId: Dispatch<SetStateAction<number | null>>
  syncURL: (params: Record<string, string>) => void
  q: string
  setQ: (value: string) => void
  handleSearch: () => void
  showMobileFilters: boolean
  setShowMobileFilters: Dispatch<SetStateAction<boolean>>
  showMobileSort: boolean
  setShowMobileSort: Dispatch<SetStateAction<boolean>>
}

export function PapersWorkspaceFooter(props: PapersWorkspaceFooterProps) {
  const {
    data, isFetching, isLoading, page, totalPages, mobileTotal, selectedIds,
    selectAllFiltered, toggleSelectAllFiltered, setPage, setLastSelectedId,
    syncURL, q, setQ, handleSearch, showMobileFilters, setShowMobileFilters,
    showMobileSort, setShowMobileSort,
  } = props
  return (
        <div data-bottom-bar className="shrink-0 border-t bg-background pb-[env(safe-area-inset-bottom,0px)]">
          {(data?.total ?? 0) > 0 && (
            <>
              {/* Desktop: pagination and result count */}
              <div className="hidden md:flex items-center justify-end px-6 py-2">
                <div className="flex items-center gap-3">
                  {isFetching && !isLoading && (
                    <span className="text-xs text-muted-foreground">加载中...</span>
                  )}
                  <span className="text-xs text-muted-foreground">
                    第 {page} 页，共 {data?.total ?? 0} 条
                  </span>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={page <= 1}
                      onClick={() => {
                        const newPage = page - 1
                        setPage(newPage)
                        setLastSelectedId(null)
                        syncURL({ page: String(newPage) })
                      }}
                    >
                      上一页
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={page >= totalPages}
                      onClick={() => {
                        const newPage = page + 1
                        setPage(newPage)
                        setLastSelectedId(null)
                        syncURL({ page: String(newPage) })
                      }}
                    >
                      下一页
                    </Button>
                  </div>
                </div>
              </div>
            </>
          )}
          {/* Mobile: status + persistent search/filter/sort bar */}
          <div className="md:hidden px-2 pt-1.5 pb-[max(0.5rem,env(safe-area-inset-bottom))] space-y-1.5 bg-background">
            <div className="flex items-center px-1 text-[11px] text-muted-foreground">
              <span>结果 {mobileTotal} 条 · 已选 {selectAllFiltered ? mobileTotal : selectedIds.size}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <Button
                type="button"
                size="sm"
                variant={selectAllFiltered ? "default" : "outline"}
                className="h-9 shrink-0 rounded-lg px-2.5 text-xs"
                onClick={toggleSelectAllFiltered}
                aria-label={selectAllFiltered ? "取消全选全部筛选结果" : "全选全部筛选结果"}
              >
                {selectAllFiltered ? "取消" : "全选"}
              </Button>
              <div className="relative flex-1 min-w-0">
                <Input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                  placeholder="搜索论文…"
                  className="h-9 pr-9 text-xs rounded-lg"
                />
                <button type="button" onClick={handleSearch} className="absolute right-1 top-1 h-7 w-7 inline-flex items-center justify-center rounded-md text-muted-foreground hover:bg-accent" aria-label="搜索">
                  <Search className="h-4 w-4" />
                </button>
              </div>
              <Button size="icon" variant={showMobileFilters ? "default" : "outline"} className="h-9 w-9 shrink-0 rounded-lg" onClick={() => setShowMobileFilters(true)} aria-label="过滤">
                <Filter className="h-4 w-4" />
              </Button>
              <Button size="icon" variant={showMobileSort ? "default" : "outline"} className="h-9 w-9 shrink-0 rounded-lg" onClick={() => setShowMobileSort(true)} aria-label="排序">
                <ArrowUpDown className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </div>

  )
}
