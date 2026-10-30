import type { Dispatch, SetStateAction } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import type { LiteratureSearchTask } from "../lib/paper-types"
import { PAPER_DIALOG_BODY, PAPER_DIALOG_CONTROL, PAPER_DIALOG_FIELD, PAPER_DIALOG_SCROLL, PAPER_DIALOG_SECTION } from "./paper-dialog-constants"

interface LiteratureSearchDialogProps {
  open: boolean
  onOpenChange: Dispatch<SetStateAction<boolean>>
  entity: string
  setEntity: Dispatch<SetStateAction<string>>
  entities: { key: string; label: string }[]
  mode: "recent" | "range" | "years"
  setMode: Dispatch<SetStateAction<"recent" | "range" | "years">>
  days: string
  setDays: Dispatch<SetStateAction<string>>
  dateFrom: string
  setDateFrom: Dispatch<SetStateAction<string>>
  dateTo: string
  setDateTo: Dispatch<SetStateAction<string>>
  yearFrom: string
  setYearFrom: Dispatch<SetStateAction<string>>
  yearTo: string
  setYearTo: Dispatch<SetStateAction<string>>
  dryRun: boolean
  setDryRun: Dispatch<SetStateAction<boolean>>
  translateZh: boolean
  setTranslateZh: Dispatch<SetStateAction<boolean>>
  task: LiteratureSearchTask | null
  error: string
  submitting: boolean
  onStart: () => void
}

export function LiteratureSearchDialog({
  open,
  onOpenChange,
  entity,
  setEntity,
  entities,
  mode,
  setMode,
  days,
  setDays,
  dateFrom,
  setDateFrom,
  dateTo,
  setDateTo,
  yearFrom,
  setYearFrom,
  yearTo,
  setYearTo,
  dryRun,
  setDryRun,
  translateZh,
  setTranslateZh,
  task,
  error,
  submitting,
  onStart,
}: LiteratureSearchDialogProps) {
  return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className={`${PAPER_DIALOG_SCROLL} sm:max-w-2xl`}>
          <DialogHeader className="pr-6">
            <DialogTitle>课题论文检索</DialogTitle>
            <DialogDescription>
              从 PMC、Europe PMC 和 Google Scholar 检索所选课题相关论文，去重后补全元数据并进入待审核列表。
            </DialogDescription>
          </DialogHeader>

          <div className={PAPER_DIALOG_BODY}>
            <section className={PAPER_DIALOG_SECTION}>
              <div className="mb-4">
                <p className="text-sm font-medium">检索范围</p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">选择课题和时间范围。检索任务将在后台运行。</p>
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className={PAPER_DIALOG_FIELD}>
                <Label className="text-[11px] text-muted-foreground">课题</Label>
                <Select value={entity} onValueChange={(v) => v && setEntity(v)}>
                  <SelectTrigger className={PAPER_DIALOG_CONTROL}><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {entities.map((b) => (
                      <SelectItem key={b.key} value={b.label}>{b.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className={PAPER_DIALOG_FIELD}>
                <Label className="text-[11px] text-muted-foreground">时间范围</Label>
                <Select value={mode} onValueChange={(value) => value && setMode(value as "recent" | "range" | "years")}>
                  <SelectTrigger className={PAPER_DIALOG_CONTROL}><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="recent">最近 N 天</SelectItem>
                    <SelectItem value="range">指定日期区间</SelectItem>
                    <SelectItem value="years">年份区间</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {mode === "recent" ? (
                <div className={PAPER_DIALOG_FIELD}>
                  <Label className="text-[11px] text-muted-foreground">最近天数（1–1095）</Label>
                  <Input className={PAPER_DIALOG_CONTROL} type="number" min={1} max={1095} value={days} onChange={(e) => setDays(e.target.value)} />
                </div>
              ) : mode === "range" ? (
                <div className="grid grid-cols-2 gap-3">
                  <div className={PAPER_DIALOG_FIELD}>
                    <Label className="text-[11px] text-muted-foreground">开始日期</Label>
                    <Input className={PAPER_DIALOG_CONTROL} type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
                  </div>
                  <div className={PAPER_DIALOG_FIELD}>
                    <Label className="text-[11px] text-muted-foreground">结束日期</Label>
                    <Input className={PAPER_DIALOG_CONTROL} type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
                  </div>
                  <p className="text-xs text-muted-foreground sm:col-span-2">日期区间最长三年。Google Scholar 仅支持按年份筛选。</p>
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-3">
                  <div className={PAPER_DIALOG_FIELD}>
                    <Label className="text-[11px] text-muted-foreground">起始年份</Label>
                    <Input className={PAPER_DIALOG_CONTROL} type="number" min={2000} max={2100} value={yearFrom} onChange={(e) => setYearFrom(e.target.value)} />
                  </div>
                  <div className={PAPER_DIALOG_FIELD}>
                    <Label className="text-[11px] text-muted-foreground">结束年份</Label>
                    <Input className={PAPER_DIALOG_CONTROL} type="number" min={2000} max={2100} value={yearTo} onChange={(e) => setYearTo(e.target.value)} />
                  </div>
                </div>
              )}
              <label className="flex min-h-9 cursor-pointer items-center gap-2 border-t pt-4 text-sm sm:col-span-2">
                <Checkbox checked={dryRun} onCheckedChange={(checked) => setDryRun(checked === true)} />
                <span>
                  <span className="block font-medium">仅预览</span>
                  <span className="block text-xs font-normal text-muted-foreground">不修改数据库，也不下载 PDF</span>
                </span>
              </label>
              <label className="flex min-h-9 cursor-pointer items-center gap-2 text-sm sm:col-span-2">
                <Checkbox checked={translateZh} onCheckedChange={(checked) => setTranslateZh(checked === true)} />
                <span>
                  <span className="block font-medium">补齐中文摘要</span>
                  <span className="block text-xs font-normal text-muted-foreground">原文摘要补全后调用项目配置的 LLM 模型翻译；失败不会阻断入库</span>
                </span>
              </label>
              </div>
            </section>

            {error && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                {error}
              </div>
            )}

            {task && (
              <section className={`${PAPER_DIALOG_SECTION} space-y-4`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Badge variant={task.status === "failed" ? "destructive" : task.status === "succeeded" ? "default" : "secondary"}>
                      {task.status === "queued" ? "等待中" : task.status === "running" ? "运行中" : task.status === "succeeded" ? "已完成" : "失败"}
                    </Badge>
                    <span className="text-xs text-muted-foreground">{task.stage}</span>
                  </div>
                  <span className="text-[10px] text-muted-foreground">任务 {task.task_id.slice(0, 8)}</span>
                </div>

                {Object.keys(task.report || {}).length > 0 && (
                  <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                    {[
                      ["raw_results", "原始结果"],
                      ["after_dedup", "去重后"],
                      ["new_papers", "发现新增"],
                      [task.request.dry_run ? "would_import" : "imported", task.request.dry_run ? "预计导入" : "成功导入"],
                      ["existing_papers", "库内已有"],
                      ["excluded", "排除/失败"],
                      ["entity_patched", "更新课题"],
                      ["existing_enriched", "已有论文补全"],
                      ["existing_errors", "已有论文失败"],
                      ["translated_zh", "新增中文摘要"],
                    ].map(([key, label]) => task.report[key] != null ? (
                      <div key={key} className="rounded-md bg-muted/60 px-2 py-1.5">
                        <p className="text-[10px] text-muted-foreground">{label}</p>
                        <p className="font-semibold">{task.report[key]}</p>
                      </div>
                    ) : null)}
                  </div>
                )}

                {task.error && <p className="text-xs text-destructive">{task.error}</p>}
                <div className="max-h-56 overflow-y-auto rounded-md bg-slate-950 p-3 font-mono text-[10px] leading-relaxed text-slate-200 scrollbar-none">
                  {task.logs.length ? task.logs.map((line, index) => (
                    <div key={`${index}-${line.slice(0, 20)}`} className="whitespace-pre-wrap break-all">{line}</div>
                  )) : <span className="text-slate-400">等待任务输出…</span>}
                </div>
              </section>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>关闭</Button>
            <Button
              onClick={onStart}
              disabled={submitting || !!(task && ["queued", "running"].includes(task.status))}
            >
              {submitting ? "提交中…" : task && ["queued", "running"].includes(task.status) ? "任务运行中" : dryRun ? "开始预览" : "开始检索并入库"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>


  )
}
