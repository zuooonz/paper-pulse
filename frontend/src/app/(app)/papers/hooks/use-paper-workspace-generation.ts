import { useEffect, useState, type Dispatch, type SetStateAction } from "react"
import { apiClient } from "@/lib/api"
import { getApiError, getApiErrorMessage } from "@/lib/api-errors"
import { STORAGE_KEYS } from "@/lib/storage"
import { downloadFileForUser } from "@/lib/file-download"
import type { Paper } from "@/lib/types"
import type { ConfigOptions, PaperDetail } from "../lib/paper-types"

const TARGET_LABEL = process.env.NEXT_PUBLIC_TARGET_LABEL || "qMRLab"

type GenerateDraftInput = { paperId: number; profile: string; themes: string[]; model: string; force: boolean }
type BatchGenerateDraftInput = { paperIds: number[]; profile: string; themes: string[]; model: string; force: boolean }
type BatchReviewInput = { paperIds: number[]; profile: string; themes: string[]; model: string }

interface UsePaperWorkspaceGenerationOptions {
  configOptions?: ConfigOptions
  targetConfigured: boolean
  getAllFilteredPapers: () => Promise<Paper[]>
  getExplicitlySelectedPapers: () => Promise<Paper[]>
  setSelectedIds: Dispatch<SetStateAction<Set<number>>>
  setLastSelectedId: Dispatch<SetStateAction<number | null>>
  selectAllFiltered: boolean
  setSelectAllFiltered: Dispatch<SetStateAction<boolean>>
  generateDraft: { mutate: (input: GenerateDraftInput) => void }
  batchGenerateDraft: { mutateAsync: (input: BatchGenerateDraftInput) => Promise<unknown> }
  batchReview: { mutateAsync: (input: BatchReviewInput) => Promise<{ data: { success: boolean; error?: string } }> }
  handleQuotaError: (error: unknown, action: string, paperIds: number[]) => boolean
  onNavigateWorkspace: () => void
  setDetailPaper: Dispatch<SetStateAction<PaperDetail | null>>
  setShowOriginalAbstract: Dispatch<SetStateAction<boolean>>
}

export function usePaperWorkspaceGeneration({
  configOptions,
  targetConfigured,
  getAllFilteredPapers,
  getExplicitlySelectedPapers,
  setSelectedIds,
  setLastSelectedId,
  selectAllFiltered,
  setSelectAllFiltered,
  generateDraft,
  batchGenerateDraft,
  batchReview,
  handleQuotaError,
  onNavigateWorkspace,
  setDetailPaper,
  setShowOriginalAbstract,
}: UsePaperWorkspaceGenerationOptions) {
  const requireTargetEntity = configOptions?.generation?.require_target_entity ?? true
  const [genDialogOpen, setGenDialogOpen] = useState(false)
  const [genPaper, setGenPaper] = useState<Paper | null>(null)
  const [genBatchPapers, setGenBatchPapers] = useState<Paper[]>([])
  const [genProfile, setGenProfile] = useState("default")
  const [genThemes, setGenThemes] = useState<string[]>([])
  const [genModel, setGenModel] = useState("auto")
  const [genForce, setGenForce] = useState(false)

  // Review dialog
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false)
  const [reviewProfile, setReviewProfile] = useState("default")
  const [reviewThemes, setReviewThemes] = useState<string[]>([])
  const [reviewModel, setReviewModel] = useState("auto")
  const [reviewLoading, setReviewLoading] = useState(false)

  // Word summary dialog
  const [wordSummaryDialogOpen, setWordSummaryDialogOpen] = useState(false)
  const [wordSummaryModel, setWordSummaryModel] = useState("auto")
  const [wordSummaryLoading, setWordSummaryLoading] = useState(false)
  const [wordSummaryProgress, setWordSummaryProgress] = useState(0)
  const [wordSummaryStage, setWordSummaryStage] = useState("")

  async function openDetail(paper: Paper) {
    setShowOriginalAbstract(false)
    try {
      const res = await apiClient.get(`/api/papers/${paper.id}`)
      setDetailPaper(res.data as PaperDetail)
    } catch {
      // Fallback to list data if API fails
      setDetailPaper(paper as PaperDetail)
    }
  }

  function openGenerateDialog(paper: Paper) {
    setGenPaper(paper)
    setGenBatchPapers([])
    setGenProfile("default")
    setGenThemes(configOptions?.themes[0] ? [configOptions.themes[0].value] : [])
    setGenModel("auto")
    // 已生成过的论文默认勾选强制重新生成，避免按钮 disabled
    setGenForce(paper.article_status === "generated")
    setGenDialogOpen(true)
  }

  function openBatchGenerateDialog() {
    setGenPaper(null)
    setGenBatchPapers([])
    setGenProfile("default")
    setGenThemes(configOptions?.themes[0] ? [configOptions.themes[0].value] : [])
    setGenModel("auto")
    setGenForce(false)
    setGenDialogOpen(true)
  }

  async function submitReview() {
    let papers: Paper[]
    if (selectAllFiltered) {
      papers = await getAllFilteredPapers()
    } else {
      papers = await getExplicitlySelectedPapers()
    }
    if (requireTargetEntity) {
      papers = papers.filter((paper) => targetConfigured && paper.entity_label === TARGET_LABEL)
    }
    if (!papers.length || papers.length < 2) {
      alert(requireTargetEntity ? `至少需要选择 2 篇${TARGET_LABEL} 论文` : "至少需要选择 2 篇论文")
      return
    }
    // 超过 20 篇自动截断
    if (papers.length > 20) {
      papers = papers.slice(0, 20)
    }
    setReviewLoading(true)
    setReviewDialogOpen(false)
    try {
      const res = await batchReview.mutateAsync({
        paperIds: papers.map((p) => p.id),
        profile: reviewProfile,
        themes: reviewThemes,
        model: reviewModel,
      })
      if (res.data.success) {
        // Navigate to workspace to see the new draft
        onNavigateWorkspace()
      } else {
        console.error("综述生成失败:", res.data.error || "未知错误")
        alert("综述生成失败: " + (res.data.error || "未知错误"))
      }
    } catch (e: unknown) {
      console.error("综述生成失败:", getApiErrorMessage(e, "未知错误"))
    } finally {
      setReviewLoading(false)
      setSelectedIds(new Set())
      setLastSelectedId(null)
      setSelectAllFiltered(false)
    }
  }

  async function pollWordSummaryJob(jobId: string, paperIds: number[]) {
    setWordSummaryLoading(true)
    let transientFailures = 0
    try {
      for (;;) {
        try {
          const response = await apiClient.get(`/api/papers/summary-jobs/${jobId}`, { timeout: 30_000 })
          transientFailures = 0
          const job = response.data
          setWordSummaryProgress(Number(job.progress || 0))
          setWordSummaryStage(job.stage || "正在处理")
          if (job.status === "succeeded" && job.result?.download_url) {
            await downloadFileForUser({
              url: job.result.download_url,
              filename: job.result.filename || "论文总结.docx",
              fileType: "docx",
            })
            window.localStorage.removeItem(STORAGE_KEYS.wordSummaryJob)
            setWordSummaryDialogOpen(false)
            setSelectedIds(new Set())
            setLastSelectedId(null)
            setSelectAllFiltered(false)
            return
          }
          if (job.status === "failed") {
            const detail = job.error?.detail || "生成任务失败"
            window.localStorage.removeItem(STORAGE_KEYS.wordSummaryJob)
            const syntheticError = { response: { status: job.error?.code === "QUOTA_EXCEEDED" ? 429 : 500, data: { detail } } }
            if (handleQuotaError(syntheticError, "paper.generate_word_summary", paperIds)) return
            const terminalError = new Error(typeof detail === "string" ? detail : detail.message || JSON.stringify(detail))
            Object.assign(terminalError, { terminal: true })
            throw terminalError
          }
        } catch (error: unknown) {
          const failure = error as { terminal?: boolean; response?: { status?: number } }
          if (failure.terminal) throw error
          if (failure.response?.status && failure.response.status < 500) throw error
          transientFailures += 1
          if (transientFailures >= 5) {
            throw new Error("暂时无法读取总结进度，服务器任务仍会继续；重新打开论文库可恢复查看")
          }
        }
        await new Promise((resolve) => window.setTimeout(resolve, 1500))
      }
    } catch (error: unknown) {
      const failure = error as { response?: { data?: { detail?: unknown } }; message?: string }
      const detail = failure.response?.data?.detail || failure.message || "未知错误"
      alert("论文总结生成失败: " + (typeof detail === "string" ? detail : JSON.stringify(detail)))
    } finally {
      setWordSummaryLoading(false)
    }
  }

  async function submitWordSummary() {
    let papers: Paper[]
    if (selectAllFiltered) {
      papers = await getAllFilteredPapers()
    } else {
      papers = await getExplicitlySelectedPapers()
    }
    if (!papers.length || papers.length < 1) return
    // 超过 100 篇自动截断
    if (papers.length > 100) {
      papers = papers.slice(0, 100)
    }
    setWordSummaryLoading(true)
    setWordSummaryProgress(0)
    setWordSummaryStage("正在创建总结任务")
    // 不立即关闭弹窗，让用户看到"总结中..."进度提示
    try {
      const res = await apiClient.post("/api/papers/summary-jobs", {
        paper_ids: papers.map((p) => p.id),
        profile: "report",
        model: wordSummaryModel === "auto" ? null : wordSummaryModel,
      }, { timeout: 30_000 })

      const jobId = res.data.job_id
      if (!jobId) throw new Error("服务器未返回总结任务编号")
      const paperIds = papers.map((p) => p.id)
      window.localStorage.setItem(STORAGE_KEYS.wordSummaryJob, JSON.stringify({ job_id: jobId, paper_ids: paperIds }))
      await pollWordSummaryJob(jobId, paperIds)
    } catch (e: unknown) {
      if (handleQuotaError(e, "paper.generate_word_summary", papers.map((p) => p.id))) return
      const apiError = getApiError(e)
      const responseData = apiError?.response?.data
      let errorMsg = getApiErrorMessage(e, "未知错误")
      if (apiError?.code === "ECONNABORTED" || apiError?.message?.includes("timeout")) {
        errorMsg = "生成超时，请减少论文数量后重试"
      } else if (typeof responseData === "string" && responseData.trim()) {
        errorMsg = responseData
      }
      alert("论文总结生成失败: " + errorMsg)
    } finally {
      setWordSummaryLoading(false)
    }
  }

  async function submitGenerate() {
    // 批量模式
    if (!genPaper) {
      // 获取实际论文列表
      let allPapers: Paper[]
      if (selectAllFiltered) {
        allPapers = await getAllFilteredPapers()
      } else {
        allPapers = await getExplicitlySelectedPapers()
      }
      if (requireTargetEntity) {
        allPapers = allPapers.filter((paper) => targetConfigured && paper.entity_label === TARGET_LABEL)
      }
      if (allPapers.length < 2) {
        alert(requireTargetEntity ? `至少需要选择 2 篇${TARGET_LABEL} 论文` : "至少需要选择 2 篇论文")
        return
      }
      // 超过 100 篇自动截断
      if (allPapers.length > 100) {
        allPapers = allPapers.slice(0, 100)
      }
      setGenDialogOpen(false)
      const paperIds = allPapers.map((p) => p.id)
      try {
        await batchGenerateDraft.mutateAsync({
          paperIds,
          profile: genProfile,
          themes: genThemes,
          model: genModel,
          force: genForce,
        })
      } catch (e: unknown) {
        console.error("批量生成失败:", getApiErrorMessage(e, "未知错误"))
      }
      setSelectedIds(new Set())
      setLastSelectedId(null)
      setSelectAllFiltered(false)
      return
    }

    // 单篇模式
    if (!genPaper) return
    setGenDialogOpen(false)
    generateDraft.mutate({
      paperId: genPaper.id,
      profile: genProfile,
      themes: genThemes,
      model: genModel,
      force: genForce,
    })
  }

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEYS.wordSummaryJob)
    if (!stored) return
    try {
      const value = JSON.parse(stored) as { job_id?: string; paper_ids?: number[] }
      if (value.job_id) {
        const resumeTimer = window.setTimeout(() => {
          setWordSummaryDialogOpen(true)
          void pollWordSummaryJob(value.job_id!, value.paper_ids ?? [])
        }, 0)
        return () => window.clearTimeout(resumeTimer)
      }
    } catch {
      window.localStorage.removeItem(STORAGE_KEYS.wordSummaryJob)
    }
    // Resume exactly once when the paper library mounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return {
    genDialogOpen, setGenDialogOpen, genPaper, setGenPaper, genBatchPapers, setGenBatchPapers,
    genProfile, setGenProfile, genThemes, setGenThemes, genModel, setGenModel, genForce, setGenForce,
    reviewDialogOpen, setReviewDialogOpen, reviewProfile, setReviewProfile, reviewThemes, setReviewThemes,
    reviewModel, setReviewModel, reviewLoading, wordSummaryDialogOpen, setWordSummaryDialogOpen,
    wordSummaryModel, setWordSummaryModel, wordSummaryLoading, wordSummaryProgress, wordSummaryStage,
    openDetail, openGenerateDialog, openBatchGenerateDialog, submitReview, pollWordSummaryJob,
    submitWordSummary, submitGenerate,
  }
}
