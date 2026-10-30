"use client"

import { MobileHeaderActions } from "@/components/mobile-header-actions"
import { Button } from "@/components/ui/button"
import { FileSpreadsheet, FileText, Sparkles, Upload } from "lucide-react"

interface MobileBatchActionsProps {
  canUpload: boolean
  role: string
  visibleCount: number
  isExporting: boolean
  isDownloading: boolean
  selectedCount: number
  selectAllFiltered: boolean
  onUpload: () => void
  onExport: () => void
  onDownloadPdf: () => void
  onOpenSummary: () => void
}

export function MobileBatchActions({
  canUpload,
  role,
  visibleCount,
  isExporting,
  isDownloading,
  selectedCount,
  selectAllFiltered,
  onUpload,
  onExport,
  onDownloadPdf,
  onOpenSummary,
}: MobileBatchActionsProps) {
  return (
    <MobileHeaderActions>
      <div className="flex items-center gap-2">
        {canUpload && (
          <Button
            variant="outline"
            className="h-auto py-1.5 px-2.5 flex flex-col items-center gap-0.5 min-w-[52px]"
            onClick={onUpload}
          >
            <Upload className="h-3.5 w-3.5" />
            <span className="text-[10px] leading-tight">导入</span>
          </Button>
        )}
        <Button
          variant="outline"
          className="h-auto py-1.5 px-2.5 flex flex-col items-center gap-0.5 min-w-[52px]"
          onClick={onExport}
          disabled={isExporting || visibleCount === 0 || (selectedCount === 0 && !selectAllFiltered)}
        >
          <FileSpreadsheet className="h-3.5 w-3.5" />
          <span className="text-[10px] leading-tight">{isExporting ? "导出中" : "Excel"}</span>
        </Button>
        {role !== "limited" && (
          <>
            <Button
              variant="outline"
              className="h-auto py-1.5 px-2.5 flex flex-col items-center gap-0.5 min-w-[52px]"
              disabled={(selectedCount === 0 && !selectAllFiltered) || isDownloading}
              onClick={onDownloadPdf}
            >
              <FileText className="h-3.5 w-3.5" />
              <span className="text-[10px] leading-tight">{isDownloading ? "打包中" : "PDF"}</span>
            </Button>
          </>
        )}
        <Button
          variant="outline"
          className="h-auto py-1.5 px-2.5 flex flex-col items-center gap-0.5 min-w-[52px]"
          disabled={selectedCount < 1 && !selectAllFiltered}
          onClick={onOpenSummary}
        >
          <Sparkles className="h-3.5 w-3.5" />
          <span className="text-[10px] leading-tight">总结</span>
        </Button>
      </div>
    </MobileHeaderActions>
  )
}
