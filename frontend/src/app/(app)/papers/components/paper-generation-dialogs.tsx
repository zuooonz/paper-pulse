import type { Dispatch, SetStateAction } from "react"
import { apiClient } from "@/lib/api"
import { getApiErrorMessage } from "@/lib/api-errors"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import type { Paper } from "@/lib/types"
import type { ConfigOptions, PaperDetail } from "../lib/paper-types"
import { truncate } from "../lib/paper-format"
import { PAPER_DIALOG_BODY, PAPER_DIALOG_CONTROL, PAPER_DIALOG_FIELD, PAPER_DIALOG_SCROLL } from "./paper-dialog-constants"

interface VideoGenerationInput {
  paperId: number
  resolution: string
  duration: number
  ratio: string
  style: string
}

interface PaperGenerationDialogsProps {
  configOptions?: ConfigOptions
  genDialogOpen: boolean
  setGenDialogOpen: Dispatch<SetStateAction<boolean>>
  genBatchPapers: Paper[]
  genPaper: Paper | null
  selectAllFiltered: boolean
  filteredTotal: number
  selectedCount: number
  genProfile: string
  setGenProfile: Dispatch<SetStateAction<string>>
  genThemes: string[]
  setGenThemes: Dispatch<SetStateAction<string[]>>
  genModel: string
  setGenModel: Dispatch<SetStateAction<string>>
  genForce: boolean
  setGenForce: Dispatch<SetStateAction<boolean>>
  submitGenerate: () => void
  updateDoiDialog: boolean
  setUpdateDoiDialog: Dispatch<SetStateAction<boolean>>
  updateDoiText: string
  setUpdateDoiText: Dispatch<SetStateAction<string>>
  updateDoiLoading: boolean
  setUpdateDoiLoading: Dispatch<SetStateAction<boolean>>
  detailPaper: PaperDetail | null
  setDetailPaper: Dispatch<SetStateAction<PaperDetail | null>>
  onRefetch: () => void
  genVidDialog: boolean
  setGenVidDialog: Dispatch<SetStateAction<boolean>>
  genVidPaperId: number | null
  genVidRes: string
  setGenVidRes: Dispatch<SetStateAction<string>>
  genVidDur: number
  setGenVidDur: Dispatch<SetStateAction<number>>
  genVidRatio: string
  setGenVidRatio: Dispatch<SetStateAction<string>>
  genVidStyle: string
  setGenVidStyle: Dispatch<SetStateAction<string>>
  generateVideo: { mutate: (input: VideoGenerationInput) => void; isPending: boolean }
  reviewDialogOpen: boolean
  setReviewDialogOpen: Dispatch<SetStateAction<boolean>>
  reviewProfile: string
  setReviewProfile: Dispatch<SetStateAction<string>>
  reviewThemes: string[]
  setReviewThemes: Dispatch<SetStateAction<string[]>>
  reviewModel: string
  setReviewModel: Dispatch<SetStateAction<string>>
  reviewLoading: boolean
  submitReview: () => void
  wordSummaryDialogOpen: boolean
  setWordSummaryDialogOpen: Dispatch<SetStateAction<boolean>>
  wordSummaryModel: string
  setWordSummaryModel: Dispatch<SetStateAction<string>>
  wordSummaryLoading: boolean
  wordSummaryProgress: number
  wordSummaryStage: string
  submitWordSummary: () => void
}

export function PaperGenerationDialogs({
  configOptions,
  genDialogOpen,
  setGenDialogOpen,
  genBatchPapers,
  genPaper,
  selectAllFiltered,
  filteredTotal,
  selectedCount,
  genProfile,
  setGenProfile,
  genThemes,
  setGenThemes,
  genModel,
  setGenModel,
  genForce,
  setGenForce,
  submitGenerate,
  updateDoiDialog,
  setUpdateDoiDialog,
  updateDoiText,
  setUpdateDoiText,
  updateDoiLoading,
  setUpdateDoiLoading,
  detailPaper,
  setDetailPaper,
  onRefetch,
  genVidDialog,
  setGenVidDialog,
  genVidPaperId,
  genVidRes,
  setGenVidRes,
  genVidDur,
  setGenVidDur,
  genVidRatio,
  setGenVidRatio,
  genVidStyle,
  setGenVidStyle,
  generateVideo,
  reviewDialogOpen,
  setReviewDialogOpen,
  reviewProfile,
  setReviewProfile,
  reviewThemes,
  setReviewThemes,
  reviewModel,
  setReviewModel,
  reviewLoading,
  submitReview,
  wordSummaryDialogOpen,
  setWordSummaryDialogOpen,
  wordSummaryModel,
  setWordSummaryModel,
  wordSummaryLoading,
  wordSummaryProgress,
  wordSummaryStage,
  submitWordSummary,
}: PaperGenerationDialogsProps) {
  return (
    <>
      <Dialog open={genDialogOpen} onOpenChange={setGenDialogOpen}>
        <DialogContent className={`${PAPER_DIALOG_SCROLL} sm:max-w-md`}>
          <DialogHeader className="pr-6">
            <DialogTitle>
              {genBatchPapers.length > 0 ? "批量生成推文" : "生成推文"}
            </DialogTitle>
            <DialogDescription>
              {genPaper
                ? `论文: ${truncate(genPaper.title, 40)}`
                : selectAllFiltered
                  ? filteredTotal > 100
                    ? `已选择 ${filteredTotal} 篇，将取前 100 篇生成（单次限制不超过 100 篇）`
                    : `已选择 ${filteredTotal} 篇`
                  : selectedCount > 100
                    ? `已选择 ${selectedCount} 篇，将取前 100 篇生成（单次限制不超过 100 篇）`
                    : `已选择 ${selectedCount} 篇`}
            </DialogDescription>
          </DialogHeader>

          <div className={PAPER_DIALOG_BODY}>
            {genBatchPapers.length > 0
              ? genBatchPapers.some((p) => p.article_status === "generated") && (
                  <div className="rounded-md bg-amber-50 border border-amber-200 p-3 text-xs text-amber-800">
                    选中的论文中有已生成过推文的。勾选&quot;强制覆盖&quot;将删除旧推文后重新生成；取消勾选则保留旧推文，另生成新推文。
                  </div>
                )
              : genPaper?.article_status === "generated" && (
                  <div className="rounded-md bg-amber-50 border border-amber-200 p-3 text-xs text-amber-800">
                    该论文已生成过推文。勾选&quot;强制覆盖&quot;将删除旧推文后重新生成；取消勾选则保留旧推文，另生成新推文。
                  </div>
                )}

            {/* Profile */}
            <div className={PAPER_DIALOG_FIELD}>
              <Label>生成风格（Profile）</Label>
              <Select value={genProfile} onValueChange={(v) => v && setGenProfile(v)}>
                <SelectTrigger className={PAPER_DIALOG_CONTROL}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="default">默认 (default)</SelectItem>
                  <SelectItem value="detail">深度解析 (detail)</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Themes */}
            <div className={PAPER_DIALOG_FIELD}>
              <Label>主题侧重</Label>
              <div className="flex flex-wrap gap-2.5">
                {(configOptions?.themes || []).map((theme) => (
                  <label
                    key={theme.value}
                    className={`inline-flex min-h-9 items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs cursor-pointer transition-colors ${
                      genThemes.includes(theme.value)
                        ? "bg-primary text-primary-foreground border-primary"
                        : "bg-background text-foreground border-input hover:bg-accent"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={genThemes.includes(theme.value)}
                      onChange={() => {
                        setGenThemes((prev) =>
                          prev.includes(theme.value)
                            ? prev.filter((t) => t !== theme.value)
                            : [...prev, theme.value]
                        )
                      }}
                    />
                    {theme.label}
                  </label>
                ))}
              </div>
            </div>

            {/* Model */}
            <div className={PAPER_DIALOG_FIELD}>
              <Label>模型</Label>
              <Select value={genModel} onValueChange={(v) => v && setGenModel(v)}>
                <SelectTrigger className={PAPER_DIALOG_CONTROL}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(configOptions?.models || []).map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Force regen */}
            <div className="flex min-h-9 items-center gap-2 border-t pt-4">
              <Checkbox
                id="force-regen"
                checked={genForce}
                onCheckedChange={(checked) => setGenForce(checked === true)}
              />
              <Label htmlFor="force-regen" className="cursor-pointer">
                强制覆盖（删除旧推文，重新生成）
              </Label>
            </div>
            {wordSummaryLoading && (
              <div className="space-y-2 rounded-lg border bg-muted/30 p-3">
                <div className="flex items-center justify-between gap-3 text-xs">
                  <span className="text-muted-foreground">{wordSummaryStage || "正在处理"}</span>
                  <span className="font-medium tabular-nums">{wordSummaryProgress}%</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary transition-[width] duration-500"
                    style={{ width: `${Math.max(3, wordSummaryProgress)}%` }}
                  />
                </div>
                <p className="text-[11px] leading-4 text-muted-foreground">
                  可以关闭页面，任务会在服务器继续执行；再次打开论文库可恢复进度。
                </p>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setGenDialogOpen(false)}>
              取消
            </Button>
            <Button
              onClick={submitGenerate}
              disabled={genPaper?.article_status === "generating"}
            >
              开始生成
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Update DOI Dialog */}
      <Dialog open={updateDoiDialog} onOpenChange={setUpdateDoiDialog}>
        <DialogContent className={`${PAPER_DIALOG_SCROLL} sm:max-w-sm`}>
          <DialogHeader className="pr-6">
            <DialogTitle>更新 DOI</DialogTitle>
            <DialogDescription>
              输入新的 DOI，系统将从 CrossRef 拉取最新元数据更新论文信息（标题、期刊、年份、卷期页码等）。
              适用于预印版更新为正式版。
            </DialogDescription>
          </DialogHeader>
          <div className={PAPER_DIALOG_BODY}>
            <div className={PAPER_DIALOG_FIELD}>
              <Label>新 DOI</Label>
              <Input value={updateDoiText} onChange={(e) => setUpdateDoiText(e.target.value)}
                placeholder="10.xxxx/xxxxx" className={`${PAPER_DIALOG_CONTROL} font-mono text-sm`} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setUpdateDoiDialog(false)}>取消</Button>
            <Button size="sm" disabled={updateDoiLoading || !updateDoiText.trim()}
              onClick={async () => {
                if (!detailPaper) return
                setUpdateDoiLoading(true)
                try {
                  const res = await apiClient.post(`/api/papers/${detailPaper.id}/update-doi`, { new_doi: updateDoiText.trim() })
                  alert(`DOI 更新成功！\n标题: ${res.data.title}\n期刊: ${res.data.journal_name} (${res.data.year})`)
                  setUpdateDoiDialog(false)
                  const updated = await apiClient.get(`/api/papers/${detailPaper.id}`)
                  setDetailPaper(updated.data)
                  onRefetch()
                } catch (e: unknown) {
                  alert("更新失败: " + getApiErrorMessage(e, "未知错误"))
                } finally { setUpdateDoiLoading(false) }
              }}>
              {updateDoiLoading ? "更新中…" : "更新"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Video Generation Dialog */}
      <Dialog open={genVidDialog} onOpenChange={setGenVidDialog}>
        <DialogContent className={`${PAPER_DIALOG_SCROLL} sm:max-w-sm`}>
          <DialogHeader className="pr-6">
            <DialogTitle>生成视频（AI 两阶段）</DialogTitle>
            <DialogDescription>
              大语言模型 撰写分镜脚本 → 视频生成服务 渲染视频。若论文已上传 PDF，会利用全文生成更丰富的故事情节。
            </DialogDescription>
          </DialogHeader>
          <div className={PAPER_DIALOG_BODY}>
            <div className={PAPER_DIALOG_FIELD}>
              <Label>视频风格</Label>
              <div className="grid grid-cols-3 gap-2">
                {[
                  { v: "professional", label: "专业", desc: "3D医学动画" },
                  { v: "popular_science", label: "科普", desc: "图解+大字幕" },
                  { v: "news", label: "资讯", desc: "动态数据图表" },
                ].map(s => (
                  <button key={s.v}
                    className={`min-h-14 rounded-lg border px-2 py-2 text-center transition-colors ${genVidStyle === s.v ? "bg-primary text-primary-foreground border-primary" : "hover:bg-accent"}`}
                    onClick={() => setGenVidStyle(s.v)}>
                    <div className="text-xs font-medium">{s.label}</div>
                    <div className="text-[9px] opacity-70">{s.desc}</div>
                  </button>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className={PAPER_DIALOG_FIELD}>
                <Label>分辨率</Label>
                <Select value={genVidRes} onValueChange={(v) => v && setGenVidRes(v)}>
                  <SelectTrigger className={PAPER_DIALOG_CONTROL}><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="720P">720P</SelectItem>
                    <SelectItem value="1080P">1080P</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className={PAPER_DIALOG_FIELD}>
                <Label>时长</Label>
                <Select value={String(genVidDur)} onValueChange={v => setGenVidDur(Number(v))}>
                  <SelectTrigger className={PAPER_DIALOG_CONTROL}><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="5">5 秒</SelectItem>
                    <SelectItem value="10">10 秒</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className={PAPER_DIALOG_FIELD}>
              <Label>画面比例</Label>
              <div className="flex gap-2">
                {[{ v: "16:9", label: "横屏 16:9" }, { v: "9:16", label: "竖屏 9:16" }, { v: "1:1", label: "方形 1:1" }].map(r => (
                  <Button key={r.v} variant={genVidRatio === r.v ? "default" : "outline"} className="h-9 flex-1 text-xs" onClick={() => setGenVidRatio(r.v)}>{r.label}</Button>
                ))}
              </div>
            </div>
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs leading-relaxed text-blue-700 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300">
              💡 大语言模型 先分析论文全文（含引言、方法、结果）生成分镜脚本，再由 视频生成服务 渲染为视频。有 PDF 全文时效果最佳。
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setGenVidDialog(false)}>取消</Button>
            <Button onClick={() => generateVideo.mutate({
              paperId: genVidPaperId!, resolution: genVidRes, duration: genVidDur,
              ratio: genVidRatio, style: genVidStyle,
            })} disabled={generateVideo.isPending}>
              {generateVideo.isPending ? "提交中…" : "生成视频"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Review Dialog */}
      <Dialog open={reviewDialogOpen} onOpenChange={setReviewDialogOpen}>
        <DialogContent className={`${PAPER_DIALOG_SCROLL} sm:max-w-md`}>
          <DialogHeader className="pr-6">
            <DialogTitle>生成综述推文</DialogTitle>
            <DialogDescription>
              {selectAllFiltered
                ? filteredTotal > 20
                  ? `已选择 ${filteredTotal} 篇，将取前 20 篇整合为一篇综述推文`
                  : `已选择 ${filteredTotal} 篇，将整合为一篇综述推文`
                : selectedCount > 20
                  ? `已选择 ${selectedCount} 篇，将取前 20 篇整合为一篇综述推文`
                  : `已选择 ${selectedCount} 篇，将整合为一篇综述推文`}
            </DialogDescription>
          </DialogHeader>

          <div className={PAPER_DIALOG_BODY}>
            {/* Profile */}
            <div className={PAPER_DIALOG_FIELD}>
              <Label>生成风格（Profile）</Label>
              <Select value={reviewProfile} onValueChange={(v) => v && setReviewProfile(v)}>
                <SelectTrigger className={PAPER_DIALOG_CONTROL}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="collection">合集 (collection)</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Themes */}
            <div className={PAPER_DIALOG_FIELD}>
              <Label>主题侧重</Label>
              <div className="flex flex-wrap gap-2.5">
                {(configOptions?.themes || []).map((theme) => (
                  <label
                    key={theme.value}
                    className={`inline-flex min-h-9 items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs cursor-pointer transition-colors ${
                      reviewThemes.includes(theme.value)
                        ? "bg-primary text-primary-foreground border-primary"
                        : "bg-background text-foreground border-input hover:bg-accent"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={reviewThemes.includes(theme.value)}
                      onChange={() => {
                        setReviewThemes((prev) =>
                          prev.includes(theme.value)
                            ? prev.filter((t) => t !== theme.value)
                            : [...prev, theme.value]
                        )
                      }}
                    />
                    {theme.label}
                  </label>
                ))}
              </div>
            </div>

            {/* Model */}
            <div className={PAPER_DIALOG_FIELD}>
              <Label>模型</Label>
              <Select value={reviewModel} onValueChange={(v) => v && setReviewModel(v)}>
                <SelectTrigger className={PAPER_DIALOG_CONTROL}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(configOptions?.models || []).map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setReviewDialogOpen(false)}>
              取消
            </Button>
            <Button onClick={submitReview} disabled={reviewLoading}>
              {reviewLoading ? "生成中…" : "生成综述"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Word Summary Dialog */}
      <Dialog open={wordSummaryDialogOpen} onOpenChange={setWordSummaryDialogOpen}>
        <DialogContent className={`${PAPER_DIALOG_SCROLL} sm:max-w-md`}>
          <DialogHeader className="pr-6">
            <DialogTitle>总结论文</DialogTitle>
            <DialogDescription>
              {selectAllFiltered
                ? filteredTotal > 100
                  ? `已选择 ${filteredTotal} 篇，将取前 100 篇生成（单次限制不超过 100 篇）`
                  : `已选择 ${filteredTotal} 篇，将生成一份 Word 总结报告`
                : selectedCount > 100
                  ? `已选择 ${selectedCount} 篇，将取前 100 篇生成（单次限制不超过 100 篇）`
                  : `已选择 ${selectedCount} 篇，将生成一份 Word 总结报告`}
            </DialogDescription>
          </DialogHeader>

          <div className={PAPER_DIALOG_BODY}>
            {/* Model */}
            <div className={PAPER_DIALOG_FIELD}>
              <Label>模型</Label>
              <Select value={wordSummaryModel} onValueChange={(v) => v && setWordSummaryModel(v)}>
                <SelectTrigger className={PAPER_DIALOG_CONTROL}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(configOptions?.models || []).map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setWordSummaryDialogOpen(false)}>
              {wordSummaryLoading ? "后台继续" : "取消"}
            </Button>
            <Button onClick={submitWordSummary} disabled={wordSummaryLoading}>
              {wordSummaryLoading ? "总结中..." : "生成并下载"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

    </>
  )
}
