import type { Dispatch, SetStateAction } from "react"
import { apiClient } from "@/lib/api"
import { getApiErrorMessage } from "@/lib/api-errors"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { ChevronRight, Search } from "lucide-react"
import type { DoiResultItem, PdfResultItem } from "../lib/paper-types"
import { truncate } from "../lib/paper-format"
import { PAPER_DIALOG_BODY, PAPER_DIALOG_SCROLL, PAPER_DIALOG_SECTION } from "./paper-dialog-constants"

interface PaperImportDialogProps {
  open: boolean
  onOpenChange: Dispatch<SetStateAction<boolean>>
  role: string
  onOpenLiteratureSearch: () => void | Promise<void>
  doiText: string
  setDoiText: Dispatch<SetStateAction<string>>
  onDoiUpload: () => void | Promise<void>
  isUploadingDois: boolean
  doiResults: DoiResultItem[] | null
  pdfResults: PdfResultItem[] | null
  setPdfResults: Dispatch<SetStateAction<PdfResultItem[] | null>>
  pdfFiles: File[]
  setIsUploadingPdfs: Dispatch<SetStateAction<boolean>>
  onRefetch: () => void
}

export function PaperImportDialog({
  open,
  onOpenChange,
  role,
  onOpenLiteratureSearch,
  doiText,
  setDoiText,
  onDoiUpload,
  isUploadingDois,
  doiResults,
  pdfResults,
  setPdfResults,
  pdfFiles,
  setIsUploadingPdfs,
  onRefetch,
}: PaperImportDialogProps) {
  return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className={`${PAPER_DIALOG_SCROLL} sm:max-w-2xl`}>
          <DialogHeader className="pr-6">
            <DialogTitle>导入论文</DialogTitle>
            <DialogDescription>
              通过 DOI 批量导入论文，系统自动拉取 CrossRef 元数据并尝试下载 PDF。
            </DialogDescription>
          </DialogHeader>

          <div className={PAPER_DIALOG_BODY}>
            {(role === "admin" || role === "viewer") && (
              <button
                type="button"
                className="flex w-full items-center justify-between gap-3 rounded-lg border bg-muted/30 p-4 text-left transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                onClick={() => {
                  onOpenChange(false)
                  window.setTimeout(() => {
                    void onOpenLiteratureSearch()
                  }, 0)
                }}
              >
                <span className="flex min-w-0 items-center gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <Search className="h-4 w-4" />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">检索</span>
                    <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">
                      从多个文献来源检索目标课题相关论文
                    </span>
                  </span>
                </span>
                <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
              </button>
            )}

            {/* DOI Import Section */}
            <section className={PAPER_DIALOG_SECTION}>
              <p className="mb-3 text-xs leading-relaxed text-muted-foreground">
                每行输入一个 DOI，系统会自动从 CrossRef 获取元数据并创建/更新论文。按 Shift+Enter 换行，按 Enter 直接提交。
              </p>
              <textarea
                className="h-32 w-full resize-none rounded-lg border bg-background p-3 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-ring focus:ring-3 focus:ring-ring/50"
                placeholder={`10.1002/hbm.12345\n10.1016/j.neuroimage.2024.120456\n...`}
                value={doiText}
                onChange={(e) => setDoiText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault()
                    onDoiUpload()
                  }
                }}
              />
              <div className="mt-3 flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">
                  {doiText
                    .split(/\n/)
                    .map((s) => s.trim())
                    .filter(Boolean).length}{" "}
                  条 DOI
                </span>
                <Button
                  size="sm"
                  onClick={onDoiUpload}
                  disabled={isUploadingDois || !doiText.trim()}
                >
                  {isUploadingDois ? "导入中..." : "开始导入"}
                </Button>
              </div>
            </section>

            {/* DOI Results */}
            {doiResults && (
              <div className="max-h-60 overflow-auto rounded-lg border scrollbar-none">
                <div className="px-3 py-1.5 text-xs font-medium bg-muted border-b">DOI 导入结果</div>
                <table className="w-full text-sm">
                  <thead className="bg-muted">
                    <tr>
                      <th className="px-3 py-2 text-left text-xs font-medium">DOI</th>
                      <th className="px-3 py-2 text-left text-xs font-medium w-16">状态</th>
                      <th className="px-3 py-2 text-left text-xs font-medium">结果</th>
                    </tr>
                  </thead>
                  <tbody>
                    {doiResults.map((r, i) => (
                      <tr key={i} className="border-t">
                        <td className="px-3 py-2 text-xs font-mono">{r.doi}</td>
                        <td className="px-3 py-2">
                          <Badge
                            variant={
                              r.status === "error"
                                ? "destructive"
                                : r.status === "created"
                                ? "default"
                                : r.status === "updated"
                                ? "default"
                                : "secondary"
                            }
                            className="text-[10px]"
                          >
                            {r.status === "created"
                              ? "新增"
                              : r.status === "updated"
                              ? "已更新"
                              : r.status === "error"
                              ? "失败"
                              : r.status}
                          </Badge>
                        </td>
                        <td className="px-3 py-2 text-xs text-muted-foreground">
                          {r.paper_id ? (
                            <span>
                              {r.status === "updated" ? (
                                <span className="text-amber-600 font-medium">#{r.paper_id} 已存在</span>
                              ) : (
                                <span>#{r.paper_id}</span>
                              )}{" "}
                              {truncate(r.title, 30)}
                            </span>
                          ) : (
                            r.message
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* PDF Results */}
            {pdfResults && (
              <div className="max-h-60 overflow-auto rounded-lg border scrollbar-none">
                <div className="px-3 py-1.5 text-xs font-medium bg-muted border-b">PDF 导入结果</div>
                <table className="w-full text-sm">
                  <thead className="bg-muted">
                    <tr>
                      <th className="px-3 py-2 text-left text-xs font-medium">文件名</th>
                      <th className="px-3 py-2 text-left text-xs font-medium w-16">状态</th>
                      <th className="px-3 py-2 text-left text-xs font-medium w-16">匹配度</th>
                      <th className="px-3 py-2 text-left text-xs font-medium">结果</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pdfResults.map((r, i) => (
                      <tr key={i} className="border-t">
                        <td className="px-3 py-2 text-xs" title={r.filename}>
                          {truncate(r.filename, 24)}
                        </td>
                        <td className="px-3 py-2">
                          <Badge
                            variant={
                              r.status === "error" || r.status === "mismatch" || r.status === "no_doi"
                                ? "destructive"
                                : r.status === "created"
                                ? "default"
                                : r.status === "linked" || r.status === "replaced"
                                ? "default"
                                : r.status === "existing_with_pdf"
                                ? "secondary"
                                : "outline"
                            }
                            className="text-[10px]"
                          >
                            {r.status === "created"
                              ? "新录入"
                              : r.status === "linked"
                              ? "已关联"
                              : r.status === "replaced"
                              ? "已覆盖"
                              : r.status === "existing_with_pdf"
                              ? "已有PDF"
                              : r.status === "mismatch"
                              ? "不匹配"
                              : r.status === "no_doi"
                              ? "无DOI"
                              : r.status === "skipped"
                              ? "跳过"
                              : r.status === "error"
                              ? "失败"
                              : r.status}
                          </Badge>
                        </td>
                        <td className="px-3 py-2 text-xs text-muted-foreground">
                          {r.similarity > 0 ? `${(r.similarity * 100).toFixed(0)}%` : "—"}
                        </td>
                        <td className="px-3 py-2 text-xs text-muted-foreground">
                          {r.paper_id ? (
                            <span className="flex items-center gap-2">
                              {r.status === "existing_with_pdf" && (
                                <span className="text-amber-600 font-medium">#{r.paper_id} 已存在 PDF: </span>
                              )}
                              {r.status === "linked" && (
                                <span className="text-green-600 font-medium">#{r.paper_id} 新关联: </span>
                              )}
                              {r.status === "replaced" && (
                                <span className="text-blue-600 font-medium">#{r.paper_id} 已覆盖: </span>
                              )}
                              {r.status === "created" && <span>#{r.paper_id} </span>}
                              {truncate(r.title, 24)}
                              {r.status === "existing_with_pdf" && (
                                <button
                                  className="text-primary hover:underline text-[10px]"
                                  onClick={async () => {
                                    const file = pdfFiles.find((f) => f.name === r.filename)
                                    if (!file) return
                                    setIsUploadingPdfs(true)
                                    try {
                                      const formData = new FormData()
                                      formData.append("files", file)
                                      const res = await apiClient.post("/api/papers/batch-pdf", formData, {
                                        headers: { "Content-Type": "multipart/form-data" },
                                        params: { replace_existing: true },
                                      })
                                      const updated = res.data.results[0]
                                      setPdfResults((prev) =>
                                        prev
                                          ? prev.map((item) =>
                                              item.filename === r.filename ? updated : item
                                            )
                                          : prev
                                      )
                                      onRefetch()
                                    } catch (e: unknown) {
                                      alert(getApiErrorMessage(e, "覆盖失败"))
                                    } finally {
                                      setIsUploadingPdfs(false)
                                    }
                                  }}
                                >
                                  覆盖
                                </button>
                              )}
                            </span>
                          ) : (
                            r.message
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>


  )
}
