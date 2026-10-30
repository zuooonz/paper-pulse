import type { Dispatch, SetStateAction } from "react"
import type { Paper } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"

interface QuotaExceededDialogInfo {
  business_date: string
  limit: number | null
  used: number
  requested_new: number
}

interface DeletePaperDialogProps {
  paper: Paper | null
  setPaper: Dispatch<SetStateAction<Paper | null>>
  reason: string
  setReason: Dispatch<SetStateAction<string>>
  isDeleting: boolean
  onDelete: (paperId: number, reason: string) => void
}

export function DeletePaperDialog({ paper, setPaper, reason, setReason, isDeleting, onDelete }: DeletePaperDialogProps) {
  return (
      <Dialog
        open={!!paper}
        onOpenChange={(open) => {
          if (!open && !isDeleting) {
            setPaper(null)
            setReason("")
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader className="pr-6">
            <DialogTitle>删除论文</DialogTitle>
            <DialogDescription className="space-y-1">
              <span className="block">
                即将删除 Cite ID: {paper?.internal_id ?? paper?.id}
              </span>
              <span className="block break-words">《{paper?.title}》</span>
              <span className="block text-destructive">此操作不可恢复，请填写删除原因。</span>
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="paper-delete-reason">
              删除原因 <span className="text-destructive">*</span>
            </Label>
            <Textarea
              id="paper-delete-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value.slice(0, 500))}
              placeholder="例如：与正式发表版本重复，当前记录为预印本"
              rows={4}
              autoFocus
              disabled={isDeleting}
            />
            <div className="text-right text-xs text-muted-foreground">
              {reason.length}/500
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={isDeleting}
              onClick={() => {
                setPaper(null)
                setReason("")
              }}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              disabled={!paper || reason.trim().length < 2 || isDeleting}
              onClick={() => {
                if (!paper) return
                onDelete(paper.id, reason.trim())
              }}
            >
              {isDeleting ? "删除中..." : "确认删除"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>


  )
}

interface QuotaRequestDialogProps {
  open: boolean
  onOpenChange: Dispatch<SetStateAction<boolean>>
  quotaExceeded: QuotaExceededDialogInfo | null
  paperIds: number[]
  reason: string
  setReason: Dispatch<SetStateAction<string>>
  submitting: boolean
  onSubmit: () => void | Promise<void>
}

export function QuotaRequestDialog({ open, onOpenChange, quotaExceeded, paperIds, reason, setReason, submitting, onSubmit }: QuotaRequestDialogProps) {
  return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>申请临时论文权限</DialogTitle>
            <DialogDescription>
              {quotaExceeded
                ? `北京时间 ${quotaExceeded.business_date}：限额 ${quotaExceeded.limit} 篇，已用 ${quotaExceeded.used} 篇，本次新增 ${quotaExceeded.requested_new} 篇。`
                : "本次操作超过今日额度。"}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="rounded-md bg-muted p-3 text-xs text-muted-foreground">
              申请范围：{paperIds.length} 篇论文
              <div className="mt-1 max-h-20 overflow-y-auto font-mono">{paperIds.join(", ")}</div>
            </div>
            <Textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="请说明客户、项目或业务用途"
              maxLength={1000}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
            <Button onClick={onSubmit} disabled={!reason.trim() || submitting}>
              {submitting ? "提交中..." : "提交申请"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
  )
}
