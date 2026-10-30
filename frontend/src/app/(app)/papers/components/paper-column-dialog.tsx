import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ChevronDown, ChevronUp, GripVertical } from "lucide-react"

interface PapersColumnState {
  key: string
  visible: boolean
  width: number
  pinned?: boolean
}

interface PapersColumnDef {
  key: string
  label: string
  defaultWidth: number
  sortable?: boolean
  defaultVisible: boolean
  center?: boolean
}

interface PaperColumnDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  columns: PapersColumnState[]
  columnDefs: PapersColumnDef[]
  role: string
  dragIndex: number | null
  onDragStart: (index: number) => void
  onDragOver: (event: React.DragEvent, index: number) => void
  onDragEnd: () => void
  onToggle: (key: string) => void
  onMove: (key: string, direction: -1 | 1) => void
  onReset: () => void
}

export function PaperColumnDialog({
  open,
  onOpenChange,
  columns,
  columnDefs,
  role,
  dragIndex,
  onDragStart,
  onDragOver,
  onDragEnd,
  onToggle,
  onMove,
  onReset,
}: PaperColumnDialogProps) {
  return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[24rem]">
          <DialogHeader>
            <DialogTitle>自定义显示列</DialogTitle>
            <DialogDescription>勾选要显示的列，拖动或点箭头调整顺序；拖动表头右缘可调整列宽，配置自动保存在本机。</DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            {columns.map((col, idx) => {
              const def = columnDefs.find((d) => d.key === col.key)
              if (!def) return null
              const roleBlocked = role === "limited" && (col.key === "attachments" || col.key === "citation")
              return (
                <div
                  key={col.key}
                  draggable
                  onDragStart={() => onDragStart(idx)}
                  onDragOver={(e) => onDragOver(e, idx)}
                  onDragEnd={onDragEnd}
                  className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 cursor-grab active:cursor-grabbing ${
                    dragIndex === idx ? "bg-primary/10 border-primary" : ""
                  }`}
                >
                  <GripVertical className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                  <input
                    type="checkbox"
                    className="accent-primary cursor-pointer disabled:cursor-not-allowed"
                    checked={col.visible}
                    disabled={roleBlocked}
                    onChange={() => onToggle(col.key)}
                    aria-label={`显示${def.label}`}
                  />
                  <span className="flex-1 text-sm">
                    {def.label}
                    {roleBlocked && <span className="ml-1 text-xs text-muted-foreground">（当前角色不可用）</span>}
                  </span>
                  <button
                    type="button"
                    className="h-7 w-7 rounded-md border text-muted-foreground hover:bg-muted disabled:opacity-40"
                    disabled={idx === 0}
                    onClick={() => onMove(col.key, -1)}
                    aria-label={`上移${def.label}`}
                  >
                    <ChevronUp className="mx-auto h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    className="h-7 w-7 rounded-md border text-muted-foreground hover:bg-muted disabled:opacity-40"
                    disabled={idx === columns.length - 1}
                    onClick={() => onMove(col.key, 1)}
                    aria-label={`下移${def.label}`}
                  >
                    <ChevronDown className="mx-auto h-3.5 w-3.5" />
                  </button>
                </div>
              )
            })}
          </div>
          <DialogFooter>
            <Button size="sm" variant="ghost" onClick={onReset}>恢复默认</Button>
            <Button size="sm" onClick={() => onOpenChange(false)}>完成</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
  )
}
