import type { Dispatch, SetStateAction } from "react"
import type { Paper } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { CloudUpload, Download, Upload } from "lucide-react"
import { PAPER_DIALOG_SCROLL, PAPER_DIALOG_SECTION } from "./paper-dialog-constants"

interface PdfDownloadResult {
  success: boolean
  message: string
  method?: string
}

interface AddPdfDialogProps {
  open: boolean
  onOpenChange: Dispatch<SetStateAction<boolean>>
  paper: Paper | null
  onAutoDownload: (paperId: number) => void | Promise<void>
  downloadLoading: boolean
  downloadResult: PdfDownloadResult | null
  dropHover: boolean
  setDropHover: Dispatch<SetStateAction<boolean>>
  onDropUpload: (paperId: number, files: FileList | null) => void | Promise<void>
}

export function AddPdfDialog({
  open,
  onOpenChange,
  paper,
  onAutoDownload,
  downloadLoading,
  downloadResult,
  dropHover,
  setDropHover,
  onDropUpload,
}: AddPdfDialogProps) {
  return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className={`${PAPER_DIALOG_SCROLL} sm:max-w-2xl`}>
          <DialogHeader className="pr-6">
            <DialogTitle>添加 PDF</DialogTitle>
            <DialogDescription className="whitespace-normal break-words">
              {paper?.title}
            </DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-1 gap-4 py-1 sm:grid-cols-2">
            {/* Left: auto-download */}
            <section className={`${PAPER_DIALOG_SECTION} flex flex-col gap-4`}>
              <div>
                <h3 className="text-sm font-semibold flex items-center gap-1.5">
                  <Download className="h-3.5 w-3.5" />
                  自动下载
                </h3>
                <p className="text-xs text-muted-foreground mt-1">
                  通过 DOI 从开放获取数据库自动检索 PDF
                </p>
              </div>

              {!paper?.doi ? (
                <div className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-md p-2">
                  该论文暂无 DOI，无法自动下载
                </div>
              ) : (
                <Button
                  size="sm"
                  onClick={() => paper && onAutoDownload(paper.id)}
                  disabled={downloadLoading}
                  className="w-full"
                >
                  {downloadLoading ? "下载中…" : "开始自动下载"}
                </Button>
              )}

              {downloadResult && (
                <div
                  className={`text-xs rounded-md p-2 border ${
                    downloadResult.success
                      ? "bg-green-50 border-green-200 text-green-700"
                      : "bg-red-50 border-red-200 text-red-700"
                  }`}
                >
                  {downloadResult.success ? "✓ " : "✗ "}
                  {downloadResult.message}
                  {downloadResult.method && `（来源: ${downloadResult.method}）`}
                </div>
              )}
            </section>

            {/* Right: manual upload */}
            <section className={`${PAPER_DIALOG_SECTION} flex flex-col gap-4`}>
              <div>
                <h3 className="text-sm font-semibold flex items-center gap-1.5">
                  <Upload className="h-3.5 w-3.5" />
                  手动上传
                </h3>
                <p className="text-xs text-muted-foreground mt-1">
                  选择本地 PDF 文件或拖放到此区域
                </p>
              </div>

              <div
                className={`flex min-h-[140px] flex-1 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed transition-colors ${
                  dropHover
                    ? "border-primary bg-primary/5"
                    : "border-muted-foreground/25 hover:border-muted-foreground/50"
                }`}
                onClick={() => {
                  const input = document.createElement("input")
                  input.type = "file"
                  input.accept = ".pdf"
                  input.onchange = (ev) => {
                    const files = (ev.target as HTMLInputElement).files
                    if (files && paper) {
                      onDropUpload(paper.id, files)
                    }
                  }
                  input.click()
                }}
                onDragOver={(e) => {
                  e.preventDefault()
                  setDropHover(true)
                }}
                onDragLeave={() => setDropHover(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDropHover(false)
                  if (paper) {
                    onDropUpload(paper.id, e.dataTransfer.files)
                  }
                }}
              >
                <CloudUpload
                  className={`h-8 w-8 ${
                    dropHover ? "text-primary" : "text-muted-foreground/60"
                  }`}
                />
                <span className="text-xs text-muted-foreground text-center px-4">
                  点击选择文件
                  <br />
                  或拖放 PDF 到此处
                </span>
              </div>
            </section>
          </div>
        </DialogContent>
      </Dialog>


  )
}
