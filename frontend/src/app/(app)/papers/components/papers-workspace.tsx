"use client"

import { useState, useMemo, useEffect } from "react"
import { useRouter } from "next/navigation"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { useSession } from "next-auth/react"
import { apiClient } from "@/lib/api"
import { usePaperFilters } from "../hooks/use-paper-filters"
import { usePaperQueries } from "../hooks/use-paper-queries"
import { usePaperSelection } from "../hooks/use-paper-selection"
import { usePaperWorkspaceFiles } from "../hooks/use-paper-workspace-files"
import { usePaperColumns, PAPERS_COLUMN_DEFS } from "../hooks/use-paper-columns"
import { useLiteratureSearch } from "../hooks/use-literature-search"
import { usePaperWorkspaceGeneration } from "../hooks/use-paper-workspace-generation"
import { usePaperCitation } from "../hooks/use-paper-citation"
import { DesktopFilterPanel, MobileFilterPanel } from "./filter-panel"
import { MobileBatchActions } from "./batch-actions"
import { MobileSortPanel, PapersWorkspaceFooter, PapersWorkspaceToolbar } from "./papers-workspace-toolbar"
import { DesktopPaperTable, MobilePaperCard } from "./paper-table"
import { PaperColumnDialog } from "./paper-column-dialog"
import { PaperDetailViewDialog } from "./paper-detail-view-dialog"
import { PaperGenerationDialogs } from "./paper-generation-dialogs"
import { LiteratureSearchDialog } from "./paper-literature-search-dialog"
import { PaperImportDialog } from "./paper-import-dialog"
import { AddPdfDialog } from "./paper-add-pdf-dialog"
import { DeletePaperDialog, QuotaRequestDialog } from "./paper-confirmation-dialogs"
import type { ClassificationEditorOptions } from "./paper-detail-dialog"
import type { Paper } from "@/lib/types"
import type { ConfigOptions, PaperDetail, PapersResponse } from "../lib/paper-types"
import { getApiError, getApiErrorDetail, getApiErrorMessage, isRecord } from "@/lib/api-errors"
import { downloadFileForUser } from "@/lib/file-download"
import { useDevice } from "@/hooks/use-device"
import { Skeleton } from "@/components/ui/skeleton"
import { QueryErrorNotice } from "@/components/query-error-notice"

const TARGET_LABEL = process.env.NEXT_PUBLIC_TARGET_LABEL || "qMRLab"

function getRequestErrorMessage(error: unknown, fallback: string): string {
  const value = error as { response?: { data?: { detail?: unknown } }; message?: unknown }
  const detail = value?.response?.data?.detail
  if (typeof detail === "string") return detail
  if (detail != null) return JSON.stringify(detail)
  return typeof value?.message === "string" ? value.message : fallback
}





interface QuotaSnapshot {
  actor_id: string
  business_date: string
  timezone: string
  enabled: boolean
  limit: number | null
  used: number
  reserved: number
  remaining: number | null
}

interface QuotaExceededDetail extends QuotaSnapshot {
  code: "QUOTA_EXCEEDED"
  message: string
  requested_new: number
  paper_ids: number[]
}

function isQuotaExceededDetail(value: unknown): value is QuotaExceededDetail {
  if (!isRecord(value)) return false
  return value.code === "QUOTA_EXCEEDED" &&
    typeof value.actor_id === "string" &&
    typeof value.business_date === "string" &&
    typeof value.timezone === "string" &&
    typeof value.enabled === "boolean" &&
    (typeof value.limit === "number" || value.limit === null) &&
    typeof value.used === "number" &&
    typeof value.reserved === "number" &&
    (typeof value.remaining === "number" || value.remaining === null) &&
    typeof value.message === "string" &&
    typeof value.requested_new === "number" &&
    Array.isArray(value.paper_ids) && value.paper_ids.every((id) => typeof id === "number")
}






function configuredTaxonomyCatalog(options: ConfigOptions | undefined, dimension: string) {
  const definition = options?.taxonomy?.dimensions?.[dimension]
  const items = definition?.items || []
  const groups = new Map<string, string[]>()
  for (const item of items) {
    if (!item.group) continue
    groups.set(item.group, [...(groups.get(item.group) || []), item.value || item.label])
  }
  return {
    label: definition?.label || dimension,
    values: items.map((item) => item.value || item.label),
    groups: Array.from(groups.entries()).map(([label, values]) => ({ label, options: values })),
  }
}







export default function PapersPage() {
  const { data: session } = useSession()
  const role = session?.user?.role ?? "viewer"
  const [entityLabels, setEntityLabels] = useState<string[]>([])
  const [targetConfigured, setTargetConfigured] = useState(true)
  const canUpload = role === "admin" || role === "viewer"
  const { isMobile, isReady: deviceReady } = useDevice()
  const columnState = usePaperColumns(role)
  const {
    columns,
    columnDialogOpen,
    setColumnDialogOpen,
    effectiveColumns,
    totalTableWidth,
    titlePinned,
    suppressSortClickRef,
    startColumnResize,
    toggleColumn,
    moveColumn,
    resetColumns,
    columnDragIdx,
    handleColumnDragStart,
    handleColumnDragOver,
    handleColumnDragEnd,
  } = columnState

  const filters = usePaperFilters()
  const {
    syncURL, searchParams, pathname,
    q, setQ, entityLabel, setEntityLabel, journal, setJournal, yearFrom, setYearFrom, yearTo, setYearTo,
    country, setCountry, province, setProvince, city, setCity,
    institutionType, setInstitutionType, affiliation, setAffiliation, researchField, setResearchField,
    researchTopic, setResearchTopic, neuroscienceSubtopic, setNeuroscienceSubtopic, tag, setTag,
    subjectDisease, setSubjectDisease, ageMin, setAgeMin, ageMax, setAgeMax, studyDesign, setStudyDesign,
    paradigmFilter, setParadigmFilter, hasMultimodal, setHasMultimodal, multimodalTechnique, setMultimodalTechnique,
    multimodalTiming, setMultimodalTiming,
    reviewOutcome, setReviewOutcome,
    page, setPage, sortBy, setSortBy,
    sortOrder, setSortOrder, applied, setApplied,
  } = filters

  useEffect(() => {
    let active = true
    apiClient.get("/api/papers/entities").then(({ data }) => {
      if (!active) return
      setEntityLabels(Array.isArray(data?.items) ? data.items : [])
      const configured = typeof data?.target_label === "string" && Boolean(data.target_label)
      setTargetConfigured(configured)
      if (!configured && !searchParams?.get("entity_label")) {
        setEntityLabel("")
        setApplied((current) => ({ ...current, entity_label: "" }))
      }
    }).catch(() => {
      if (active) setEntityLabels([])
    })
    return () => { active = false }
  }, [searchParams, setApplied, setEntityLabel])

  const [detailPaper, setDetailPaper] = useState<PaperDetail | null>(null)
  const [showOriginalAbstract, setShowOriginalAbstract] = useState(false)
  const [deleteDialogPaper, setDeleteDialogPaper] = useState<Paper | null>(null)
  const [deleteReason, setDeleteReason] = useState("")
  const { citationCopyState, handleCopyCitation } = usePaperCitation(getRequestErrorMessage)
  const [isExporting, setIsExporting] = useState(false)
  const [isDownloading, setIsDownloading] = useState(false)
  const [quotaRequestOpen, setQuotaRequestOpen] = useState(false)
  const [quotaExceeded, setQuotaExceeded] = useState<QuotaExceededDetail | null>(null)
  const [quotaRequestAction, setQuotaRequestAction] = useState("")
  const [quotaRequestPaperIds, setQuotaRequestPaperIds] = useState<number[]>([])
  const [quotaRequestReason, setQuotaRequestReason] = useState("")
  const [quotaRequestSubmitting, setQuotaRequestSubmitting] = useState(false)
  // NOTE: generatingId / genBatchIndex 已移除 — 统一由后端 article_status 驱动

  // Multi-select
  const [showMobileFilters, setShowMobileFilters] = useState(false) // 移动端展开完整筛选
  const [showMobileSort, setShowMobileSort] = useState(false)

  const literatureSearch = useLiteratureSearch()
  const {
    literatureSearchDialogOpen, setLiteratureSearchDialogOpen,
    literatureSearchMode, setLiteratureSearchMode,
    literatureSearchDays, setLiteratureSearchDays,
    literatureSearchDateFrom, setLiteratureSearchDateFrom,
    literatureSearchDateTo, setLiteratureSearchDateTo,
    literatureSearchYearFrom, setLiteratureSearchYearFrom,
    literatureSearchYearTo, setLiteratureSearchYearTo,
    literatureSearchEntity, setLiteratureSearchEntity,
    literatureSearchEntities,
    literatureSearchDryRun, setLiteratureSearchDryRun,
    literatureSearchTranslateZh, setLiteratureSearchTranslateZh,
    literatureSearchTask, setLiteratureSearchTask,
    literatureSearchSubmitting,
    literatureSearchError, setLiteratureSearchError,
    openLiteratureSearchDialog, startLiteratureSearch,
  } = literatureSearch

  const router = useRouter()
  const qc = useQueryClient()

  function invalidatePaperViews() {
    qc.invalidateQueries({ queryKey: ["papers"] })
    qc.invalidateQueries({ queryKey: ["papers-infinite"] })
    qc.invalidateQueries({ queryKey: ["paper-filter-options"] })
  }

  const quotaQuery = useQuery<QuotaSnapshot>({
    queryKey: ["governance", "quota", "me"],
    queryFn: () => apiClient.get("/api/governance/quota/me").then((r) => r.data),
    enabled: role === "limited",
    refetchOnWindowFocus: true,
  })

  function handleQuotaError(error: unknown, action: string, paperIds: number[]): boolean {
    const detail = getApiErrorDetail(error)
    if (getApiError(error)?.response?.status !== 429 || !isQuotaExceededDetail(detail)) return false
    setQuotaExceeded(detail)
    setQuotaRequestAction(action)
    setQuotaRequestPaperIds(paperIds)
    setQuotaRequestReason("")
    setQuotaRequestOpen(true)
    qc.invalidateQueries({ queryKey: ["governance", "quota", "me"] })
    return true
  }

  async function submitQuotaRequest() {
    if (!quotaRequestReason.trim() || quotaRequestPaperIds.length === 0) return
    setQuotaRequestSubmitting(true)
    try {
      await apiClient.post("/api/governance/access-requests", {
        request_type: "exact_scope",
        action: quotaRequestAction,
        paper_ids: quotaRequestPaperIds,
        reason: quotaRequestReason.trim(),
      })
      setQuotaRequestOpen(false)
      alert("申请已提交，管理员批准后可重新执行本次操作")
    } catch (error: unknown) {
      alert(getApiErrorMessage(error, "申请提交失败"))
    } finally {
      setQuotaRequestSubmitting(false)
    }
  }

  const generateDraft = useMutation({
    mutationFn: ({
      paperId,
      profile,
      themes,
      model,
      force,
    }: {
      paperId: number
      profile: string
      themes: string[]
      model: string
      force: boolean
    }) =>
      apiClient.post(`/api/papers/${paperId}/generate-draft`, {
        profile,
        themes,
        model: model === "auto" ? null : model,
        force_regen: force,
      }),
    onMutate: async ({ paperId }) => {
      // Optimistic: 立即在缓存中标记为 generating，无需等服务端回包
      qc.setQueriesData<PapersResponse>({ queryKey: ["papers"], exact: false }, (old) => {
        if (!old?.items) return old
        return {
          ...old,
          items: old.items.map((p) =>
            p.id === paperId ? { ...p, article_status: "generating" } : p
          ),
        }
      })
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["papers"] })
      qc.invalidateQueries({ queryKey: ["drafts"] })
    },
  })

  const batchGenerateDraft = useMutation({
    mutationFn: ({
      paperIds,
      profile,
      themes,
      model,
      force,
    }: {
      paperIds: number[]
      profile: string
      themes: string[]
      model: string
      force: boolean
    }) =>
      apiClient.post("/api/papers/batch-generate-draft", {
        paper_ids: paperIds,
        profile,
        themes,
        model: model === "auto" ? null : model,
        force_regen: force,
      }),
    onMutate: async ({ paperIds }) => {
      qc.setQueriesData<PapersResponse>({ queryKey: ["papers"], exact: false }, (old) => {
        if (!old?.items) return old
        const idSet = new Set(paperIds)
        return {
          ...old,
          items: old.items.map((p) =>
            idSet.has(p.id) ? { ...p, article_status: "generating" } : p
          ),
        }
      })
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["papers"] })
      qc.invalidateQueries({ queryKey: ["drafts"] })
    },
  })

  // Video generation from paper (视频生成服务)
  const [genVidPaperId, setGenVidPaperId] = useState<number | null>(null)
  const [genVidRes, setGenVidRes] = useState("720P")
  const [genVidDur, setGenVidDur] = useState(5)
  const [genVidRatio, setGenVidRatio] = useState("16:9")
  const [genVidStyle, setGenVidStyle] = useState("professional")
  const [genVidDialog, setGenVidDialog] = useState(false)
  const [updateDoiDialog, setUpdateDoiDialog] = useState(false)
  const [updateDoiText, setUpdateDoiText] = useState("")
  const [updateDoiLoading, setUpdateDoiLoading] = useState(false)

  const generateVideo = useMutation({
    mutationFn: ({ paperId, resolution, duration, ratio, style }: {
      paperId: number; resolution: string; duration: number; ratio: string; style: string
    }) => apiClient.post(`/api/papers/${paperId}/generate-video`, { resolution, duration, ratio, style }),
    onSuccess: (res) => {
      setGenVidDialog(false)
      const d = res.data
      alert(`视频任务已提交！\n标题: ${d?.title || "—"}\n分镜: ${d?.scenes || 0} 个场景\n素材: ${d?.content_source || "摘要"}\n约 2-5 分钟完成，在视频库查看`)
    },
    onError: (err: unknown) => alert(`生成失败: ${getApiErrorMessage(err, "未知错误")}`),
  })

  const batchReview = useMutation({
    mutationFn: ({
      paperIds,
      profile,
      themes,
      model,
    }: {
      paperIds: number[]
      profile: string
      themes: string[]
      model: string
    }) =>
      apiClient.post("/api/papers/batch-review", {
        paper_ids: paperIds,
        profile,
        themes,
        model: model === "auto" ? null : model,
      }),
    onMutate: async ({ paperIds }) => {
      // 立即在缓存中标记为 generating，无需等服务端回包
      qc.setQueriesData<PapersResponse>({ queryKey: ["papers"], exact: false }, (old) => {
        if (!old?.items) return old
        const idSet = new Set(paperIds)
        return {
          ...old,
          items: old.items.map((p) =>
            idSet.has(p.id) ? { ...p, article_status: "generating" } : p
          ),
        }
      })
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["papers"] })
      qc.invalidateQueries({ queryKey: ["drafts"] })
    },
  })

  const deletePaper = useMutation({
    mutationFn: ({ paperId, reason }: { paperId: number; reason: string }) =>
      apiClient.delete(`/api/papers/${paperId}`, { data: { reason } }),
    onSuccess: (_response, variables) => {
      invalidatePaperViews()
      qc.invalidateQueries({ queryKey: ["drafts"] })
      setSelectedIds((previous) => {
        const next = new Set(previous)
        next.delete(variables.paperId)
        return next
      })
      if (detailPaper?.id === variables.paperId) setDetailPaper(null)
      setDeleteDialogPaper(null)
      setDeleteReason("")
    },
    onError: (error: unknown) => {
      alert(`删除失败：${getRequestErrorMessage(error, "未知错误")}`)
    },
  })

  const updatePaper = useMutation({
    mutationFn: ({ paperId, data }: { paperId: number; data: Record<string, unknown> }) =>
      apiClient.patch(`/api/papers/${paperId}`, data),
    onSuccess: () => {
      invalidatePaperViews()
      qc.invalidateQueries({ queryKey: ["paper-tags"] })
      setDetailPaper(null)
    },
    onError: (error: unknown) => {
      alert(`保存失败：${getRequestErrorMessage(error, "未知错误")}`)
    },
  })



  const queryState = usePaperQueries({
    applied, page, sortBy, sortOrder, isMobile, deviceReady,
    literatureSearchTask, setLiteratureSearchTask, setLiteratureSearchError,
  })
  const {
    data, isLoading, isFetching, isPapersError, papersError, refetch,
    fetchNextPage, hasNextPage, isFetchingNextPage, isInfiniteLoading,
    isInfiniteError, infiniteError, isFetchNextPageError, refetchInfinite,
    mobilePapers, mobileTotal, visiblePapers, filteredTotal, loadMoreRef, scrollContainerRef,
    totalPages, filterOptions, configOptions,
  } = queryState

  const {
    selectedIds,
    setSelectedIds,
    lastSelectedId,
    setLastSelectedId,
    selectAllFiltered,
    setSelectAllFiltered,
    toggleSelect,
    getExplicitlySelectedPapers,
    setPaperSelected,
    toggleSelectAll,
    toggleSelectAllFiltered,
    getAllFilteredPapers,
  } = usePaperSelection({ data, visiblePapers, applied, sortBy, sortOrder })

  const generation = usePaperWorkspaceGeneration({
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
    onNavigateWorkspace: () => router.push("/workspace"),
    setDetailPaper,
    setShowOriginalAbstract,
  })
  const {
    genDialogOpen, setGenDialogOpen, genPaper, genBatchPapers,
    genProfile, setGenProfile, genThemes, setGenThemes, genModel, setGenModel, genForce, setGenForce,
    reviewDialogOpen, setReviewDialogOpen, reviewProfile, setReviewProfile, reviewThemes, setReviewThemes,
    reviewModel, setReviewModel, reviewLoading, wordSummaryDialogOpen, setWordSummaryDialogOpen,
    wordSummaryModel, setWordSummaryModel, wordSummaryLoading, wordSummaryProgress, wordSummaryStage,
    openDetail, openGenerateDialog, openBatchGenerateDialog, submitReview,
    submitWordSummary, submitGenerate,
  } = generation

  const workspaceFiles = usePaperWorkspaceFiles({
    selectedIds,
    selectAllFiltered,
    getAllFilteredPapers,
    detailPaper,
    setDetailPaper,
    handleQuotaError,
    refetch,
    setIsDownloading,
  })
  const {
    uploadDialogOpen, setUploadDialogOpen, doiText, setDoiText, doiResults,
    isUploadingDois, pdfFiles, pdfResults, setPdfResults,
    setIsUploadingPdfs, isUploadingSinglePdf, singlePdfInputRef, rowPdfUploadId,
    addPdfDialogOpen, setAddPdfDialogOpen, addPdfPaper, setAddPdfPaper,
    pdfDownloadLoading, pdfDownloadResult, setPdfDownloadResult, pdfDropHover, setPdfDropHover,
    openUploadDialog, handleBatchDownloadPdf, handleDoiUpload,
    handleSinglePdfUpload, handleAutoDownloadPdf, handlePdfDropUpload,
    handleOpenPdf,
  } = workspaceFiles

  const classificationEditorOptions = useMemo<ClassificationEditorOptions>(() => {
    const researchTopics = configuredTaxonomyCatalog(configOptions, "research_topics")
    const neuroscienceSubtopics = configuredTaxonomyCatalog(configOptions, "neuroscience_subtopics")
    const diseases = configuredTaxonomyCatalog(configOptions, "diseases")
    const studyDesigns = configuredTaxonomyCatalog(configOptions, "study_design")
    const paradigms = configuredTaxonomyCatalog(configOptions, "paradigms")
    const multimodal = configuredTaxonomyCatalog(configOptions, "multimodal")
    const dimensions = configOptions?.taxonomy?.dimensions || {}
    return {
      labels: Object.fromEntries(Object.entries(dimensions).map(([key, value]) => [key, value.label || key])),
      researchTopics: researchTopics.values,
      researchTopicGroups: researchTopics.groups,
      neuroscienceSubtopics: neuroscienceSubtopics.values,
      diseases: diseases.values,
      diseaseGroups: diseases.groups,
      studyDesigns: studyDesigns.values,
      studyDesignGroups: studyDesigns.groups,
      paradigms: paradigms.values,
      paradigmGroups: paradigms.groups,
      multimodalMethods: multimodal.values,
      multimodalMethodGroups: multimodal.groups,
      multimodalStates: filterOptions?.multimodal || [],
      multimodalTimings: configOptions?.multimodal_timings || [],
    }
  }, [configOptions, filterOptions?.multimodal])

  function handleSearch() {
    setApplied({
      q, entity_label: entityLabel, journal, yearFrom, yearTo, country, province, city,
      institutionType, affiliation, researchField, researchTopic, neuroscienceSubtopic, tag,
      reviewOutcome,
      subjectDisease, ageMin, ageMax, studyDesign, paradigmFilter,
      hasMultimodal, multimodalTechnique, multimodalTiming,
    })
    setPage(1)
    setSelectedIds(new Set())
    setSelectAllFiltered(false)
    setLastSelectedId(null)
    syncURL({
      q: q || undefined,
      entity_label: entityLabel || undefined,
      journal: journal || undefined,
      year_from: yearFrom || undefined,
      year_to: yearTo || undefined,
      country: country || undefined,
      province: province || undefined,
      city: city || undefined,
      institution_type: institutionType || undefined,
      department: undefined,
      affiliation: affiliation || undefined,
      rf: researchField || undefined,
      topic: researchTopic || undefined,
      neuro: neuroscienceSubtopic || undefined,
      tag: tag || undefined,
      disease: subjectDisease || undefined,
      age_min: ageMin || undefined,
      age_max: ageMax || undefined,
      design: studyDesign || undefined,
      paradigm: paradigmFilter || undefined,
      multimodal: hasMultimodal || undefined,
      technique: multimodalTechnique || undefined,
      timing: multimodalTiming || undefined,
      review_outcome: reviewOutcome || "all",
      page: "1",
    })
  }

  function applyReviewOutcome(value: string) {
    setReviewOutcome(value)
    setApplied((previous) => ({ ...previous, reviewOutcome: value }))
    setPage(1)
    setSelectedIds(new Set())
    setSelectAllFiltered(false)
    setLastSelectedId(null)
    syncURL({ review_outcome: value || "all", page: "1" })
  }

  function closeMobileFilters() {
    // 关闭时丢弃尚未“应用”的表单改动，保持弹窗行为一致。
    setEntityLabel(applied.entity_label)
    setYearFrom(applied.yearFrom)
    setYearTo(applied.yearTo)
    setCountry(applied.country)
    setProvince(applied.province)
    setCity(applied.city)
    setInstitutionType(applied.institutionType)
    setTag(applied.tag)
    setResearchTopic(applied.researchTopic)
    setNeuroscienceSubtopic(applied.neuroscienceSubtopic)
    setSubjectDisease(applied.subjectDisease)
    setAgeMin(applied.ageMin)
    setAgeMax(applied.ageMax)
    setStudyDesign(applied.studyDesign)
    setParadigmFilter(applied.paradigmFilter)
    setHasMultimodal(applied.hasMultimodal)
    setMultimodalTechnique(applied.multimodalTechnique)
    setMultimodalTiming(applied.multimodalTiming)
    setReviewOutcome(applied.reviewOutcome)
    setShowMobileFilters(false)
  }

  function handleReset() {
    setQ("")
    setEntityLabel(TARGET_LABEL)
    setJournal("")
    setYearFrom("")
    setYearTo("")
    setCountry("")
    setProvince("")
    setCity("")
    setInstitutionType("")
    setAffiliation("")
    setResearchField("")
    setResearchTopic("")
    setNeuroscienceSubtopic("")
    setTag("")
    setSubjectDisease("")
    setAgeMin("")
    setAgeMax("")
    setStudyDesign("")
    setParadigmFilter("")
    setHasMultimodal("")
    setMultimodalTechnique("")
    setMultimodalTiming("")
    setReviewOutcome("approved")
    setApplied({
      q: "", entity_label: TARGET_LABEL, journal: "", yearFrom: "", yearTo: "",
      country: "", province: "", city: "", institutionType: "",
      affiliation: "", researchField: "", researchTopic: "", neuroscienceSubtopic: "", tag: "",
      reviewOutcome: "approved",
      subjectDisease: "", ageMin: "", ageMax: "", studyDesign: "",
      paradigmFilter: "", hasMultimodal: "", multimodalTechnique: "", multimodalTiming: "",
    })
    setPage(1)
    setSortBy("internal_id")
    setSortOrder("desc")
    setSelectedIds(new Set())
    setSelectAllFiltered(false)
    setLastSelectedId(null)
    window.history.replaceState(null, "", pathname)
  }

  function handleSort(field: string) {
    let newOrder: "asc" | "desc"
    if (sortBy === field) {
      newOrder = sortOrder === "asc" ? "desc" : "asc"
    } else {
      newOrder = "desc"
    }
    setSortBy(field)
    setSortOrder(newOrder)
    setPage(1)
    syncURL({ sort: field, order: newOrder, page: "1" })
  }

  async function handleExport() {
    setIsExporting(true)
    let paperIds: number[] = []
    try {
      // 获取要导出的论文 ID（和 PDF 下载逻辑一致）
      if (selectAllFiltered) {
        const allPapers = await getAllFilteredPapers()
        paperIds = allPapers.map((p) => p.id)
      } else {
        paperIds = Array.from(selectedIds)
      }

      if (paperIds.length === 0) {
        alert("没有可导出的论文")
        return
      }

      const params: Record<string, unknown> = {
        paper_ids: paperIds.join(","),
      }
      // Step 1: Generate Excel and get temp URL
      const response = await apiClient.get("/api/papers/export", { params })
      await downloadFileForUser({
        url: response.data.download_url,
        filename: response.data.filename || "papers.xlsx",
        fileType: "xlsx",
      })
    } catch (err: unknown) {
      if (handleQuotaError(err, "paper.export_excel", paperIds)) return
      const msg = err instanceof Error ? err.message : String(err)
      console.error("导出 Excel 失败:", err)
      alert(`导出 Excel 失败: ${msg}`)
    } finally {
      setIsExporting(false)
    }
  }

  const filterPanelProps = {
    filters,
    filterOptions,
    configOptions,
    entityLabels,
    role,
    targetConfigured,
    showMobileFilters,
    setShowMobileFilters,
    setSelectedIds,
    setLastSelectedId,
    onCloseMobileFilters: closeMobileFilters,
    onApply: handleSearch,
    onReset: handleReset,
    onReviewChange: applyReviewOutcome,
  }

  const currentPageSelectedCount = data?.items?.filter((paper) => selectedIds.has(paper.id)).length ?? 0
  const isAllSelected = selectAllFiltered || (
    (data?.items?.length ?? 0) > 0 && currentPageSelectedCount === data?.items?.length
  )

  return (
    <div className="flex h-full min-h-0 min-w-0">
      <MobileBatchActions
        canUpload={canUpload}
        role={role}
        visibleCount={visiblePapers.length}
        isExporting={isExporting}
        isDownloading={isDownloading}
        selectedCount={selectedIds.size}
        selectAllFiltered={selectAllFiltered}
        onUpload={openUploadDialog}
        onExport={handleExport}
        onDownloadPdf={handleBatchDownloadPdf}
        onOpenSummary={() => { setWordSummaryModel("auto"); setWordSummaryDialogOpen(true) }}
      />

      <MobileFilterPanel {...filterPanelProps} />

      <MobileSortPanel
        showMobileSort={showMobileSort}
        setShowMobileSort={setShowMobileSort}
        setSortBy={setSortBy}
        setSortOrder={setSortOrder}
        setPage={setPage}
        syncURL={syncURL}
        sortBy={sortBy}
        sortOrder={sortOrder}
      />

      <DesktopFilterPanel {...filterPanelProps} />

      {/* Right content */}
      <main className="flex-1 flex flex-col overflow-hidden">
        <PapersWorkspaceToolbar
          role={role}
          selectedIds={selectedIds}
          setSelectedIds={setSelectedIds}
          setLastSelectedId={setLastSelectedId}
          selectAllFiltered={selectAllFiltered}
          setSelectAllFiltered={setSelectAllFiltered}
          toggleSelectAllFiltered={toggleSelectAllFiltered}
          data={data}
          quotaSnapshot={quotaQuery.data}
          openBatchGenerateDialog={openBatchGenerateDialog}
          setReviewProfile={setReviewProfile}
          setReviewThemes={setReviewThemes}
          setReviewModel={setReviewModel}
          setReviewDialogOpen={setReviewDialogOpen}
          deletePending={deletePaper.isPending}
          setDeleteReason={setDeleteReason}
          setDeleteDialogPaper={setDeleteDialogPaper}
          isFetching={isFetching}
          isLoading={isLoading}
          setColumnDialogOpen={setColumnDialogOpen}
          canUpload={canUpload}
          openUploadDialog={openUploadDialog}
          handleExport={handleExport}
          isExporting={isExporting}
          handleBatchDownloadPdf={handleBatchDownloadPdf}
          isDownloading={isDownloading}
          setWordSummaryModel={setWordSummaryModel}
          setWordSummaryDialogOpen={setWordSummaryDialogOpen}
        />

        <div ref={scrollContainerRef} className="min-h-0 flex-1 overflow-auto overscroll-contain pb-4">
          {!deviceReady || (!isMobile && isLoading) || (isMobile && isInfiniteLoading) ? (
            <div className="p-6 flex flex-col gap-3">
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full rounded-lg" />
              ))}
            </div>
          ) : (isMobile ? isInfiniteError : isPapersError) ? (
            <QueryErrorNotice
              className="m-6"
              message={`论文列表加载失败：${getApiErrorMessage(isMobile ? infiniteError : papersError, "请稍后重试")}`}
              onRetry={() => { void (isMobile ? refetchInfinite() : refetch()) }}
              retrying={isMobile ? isFetchingNextPage : isFetching}
            />
          ) : (data?.items?.length || mobilePapers.length) ? (
            <div>
            <DesktopPaperTable
              data={data}
              effectiveColumns={effectiveColumns}
              columnDefs={PAPERS_COLUMN_DEFS}
              totalTableWidth={totalTableWidth}
              titlePinned={titlePinned}
              currentPageSelectedCount={currentPageSelectedCount}
              isAllSelected={isAllSelected}
              selectedIds={selectedIds}
              selectAllFiltered={selectAllFiltered}
              setSelectAllFiltered={setSelectAllFiltered}
              suppressSortClickRef={suppressSortClickRef}
              startColumnResize={startColumnResize}
              handleSort={handleSort}
              sortBy={sortBy}
              sortOrder={sortOrder}
              toggleSelect={toggleSelect}
              toggleSelectAll={toggleSelectAll}
              lastSelectedId={lastSelectedId}
              setLastSelectedId={setLastSelectedId}
              visiblePapers={visiblePapers}
              setSelectedIds={setSelectedIds}
              openDetail={openDetail}
              rowPdfUploadId={rowPdfUploadId}
              handleOpenPdf={handleOpenPdf}
              setAddPdfPaper={setAddPdfPaper}
              setPdfDownloadResult={setPdfDownloadResult}
              setPdfDropHover={setPdfDropHover}
              setAddPdfDialogOpen={setAddPdfDialogOpen}
              role={role}
              openGenerateDialog={openGenerateDialog}
              handleCopyCitation={handleCopyCitation}
              citationCopyState={citationCopyState}
            />
            {/* Mobile card list with infinite scroll */}
            <div className="md:hidden space-y-2 p-2 scrollbar-none">
              {mobilePapers.map((paper) => {
                const isSelected = selectAllFiltered || selectedIds.has(paper.id)
                return (
                  <MobilePaperCard
                    key={paper.id}
                    paper={paper}
                    isSelected={isSelected}
                    onSelect={(id) => setPaperSelected(id, true)}
                    onDeselect={(id) => setPaperSelected(id, false)}
                    onTap={() => openDetail(paper)}
                  />
                )
              })}
              {/* Infinite scroll sentinel */}
              <div ref={loadMoreRef} className="py-4 text-center text-xs text-muted-foreground">
                {isFetchingNextPage ? "加载中…" : isFetchNextPageError ? (
                  <QueryErrorNotice
                    className="mx-auto max-w-sm p-3"
                    message={`加载更多论文失败：${getApiErrorMessage(infiniteError, "请稍后重试")}`}
                    onRetry={() => { void fetchNextPage() }}
                  />
                ) : hasNextPage ? "继续上滑加载" : null}
              </div>
            </div>
            </div>
          ) : (
            <div className="p-8 text-center text-sm text-muted-foreground">
              未找到符合条件的论文
            </div>
          )}
        </div>

        <PapersWorkspaceFooter
          data={data}
          isFetching={isFetching}
          isLoading={isLoading}
          page={page}
          totalPages={totalPages}
          mobileTotal={mobileTotal}
          selectedIds={selectedIds}
          selectAllFiltered={selectAllFiltered}
          toggleSelectAllFiltered={toggleSelectAllFiltered}
          setPage={setPage}
          setLastSelectedId={setLastSelectedId}
          syncURL={syncURL}
          q={q}
          setQ={setQ}
          handleSearch={handleSearch}
          showMobileFilters={showMobileFilters}
          setShowMobileFilters={setShowMobileFilters}
          showMobileSort={showMobileSort}
          setShowMobileSort={setShowMobileSort}
        />
      </main>

      <PaperColumnDialog
        open={columnDialogOpen}
        onOpenChange={setColumnDialogOpen}
        columns={columns}
        columnDefs={PAPERS_COLUMN_DEFS}
        role={role}
        dragIndex={columnDragIdx}
        onDragStart={handleColumnDragStart}
        onDragOver={handleColumnDragOver}
        onDragEnd={handleColumnDragEnd}
        onToggle={toggleColumn}
        onMove={moveColumn}
        onReset={resetColumns}
      />

      <PaperDetailViewDialog
        detailPaper={detailPaper}
        setDetailPaper={setDetailPaper}
        showOriginalAbstract={showOriginalAbstract}
        setShowOriginalAbstract={setShowOriginalAbstract}
        isMobile={isMobile}
        role={role}
        onGenerateDraft={openGenerateDialog}
        onGenerateVideo={(paperId) => { setGenVidPaperId(paperId); setGenVidDialog(true) }}
        setUpdateDoiText={setUpdateDoiText}
        setUpdateDoiDialog={setUpdateDoiDialog}
        singlePdfInputRef={singlePdfInputRef}
        isUploadingSinglePdf={isUploadingSinglePdf}
        handleSinglePdfUpload={handleSinglePdfUpload}
        handleOpenPdf={handleOpenPdf}
        handleCopyCitation={handleCopyCitation}
        citationCopyState={citationCopyState}
        classificationEditorOptions={classificationEditorOptions}
        isSavingPaper={updatePaper.isPending}
        onSavePaper={(paperId, data) => updatePaper.mutate({ paperId, data })}
        onOpenDraft={(draftId) => router.push(draftId ? `/workspace?draft=${draftId}` : "/workspace")}
        setDeleteReason={setDeleteReason}
        setDeleteDialogPaper={setDeleteDialogPaper}
        onRefetch={refetch}
      />

      <DeletePaperDialog
        paper={deleteDialogPaper}
        setPaper={setDeleteDialogPaper}
        reason={deleteReason}
        setReason={setDeleteReason}
        isDeleting={deletePaper.isPending}
        onDelete={(paperId, reason) => deletePaper.mutate({ paperId, reason })}
      />
      <PaperGenerationDialogs
        genDialogOpen={genDialogOpen}
        setGenDialogOpen={setGenDialogOpen}
        genBatchPapers={genBatchPapers}
        genPaper={genPaper}
        selectAllFiltered={selectAllFiltered}
        filteredTotal={filteredTotal}
        selectedCount={selectedIds.size}
        genProfile={genProfile}
        setGenProfile={setGenProfile}
        genThemes={genThemes}
        setGenThemes={setGenThemes}
        genModel={genModel}
        setGenModel={setGenModel}
        genForce={genForce}
        setGenForce={setGenForce}
        submitGenerate={submitGenerate}
        updateDoiDialog={updateDoiDialog}
        setUpdateDoiDialog={setUpdateDoiDialog}
        updateDoiText={updateDoiText}
        setUpdateDoiText={setUpdateDoiText}
        updateDoiLoading={updateDoiLoading}
        setUpdateDoiLoading={setUpdateDoiLoading}
        detailPaper={detailPaper}
        setDetailPaper={setDetailPaper}
        onRefetch={invalidatePaperViews}
        genVidDialog={genVidDialog}
        setGenVidDialog={setGenVidDialog}
        genVidPaperId={genVidPaperId}
        genVidRes={genVidRes}
        setGenVidRes={setGenVidRes}
        genVidDur={genVidDur}
        setGenVidDur={setGenVidDur}
        genVidRatio={genVidRatio}
        setGenVidRatio={setGenVidRatio}
        genVidStyle={genVidStyle}
        setGenVidStyle={setGenVidStyle}
        generateVideo={generateVideo}
        reviewDialogOpen={reviewDialogOpen}
        setReviewDialogOpen={setReviewDialogOpen}
        reviewProfile={reviewProfile}
        setReviewProfile={setReviewProfile}
        reviewThemes={reviewThemes}
        setReviewThemes={setReviewThemes}
        reviewModel={reviewModel}
        setReviewModel={setReviewModel}
        reviewLoading={reviewLoading}
        submitReview={submitReview}
        wordSummaryDialogOpen={wordSummaryDialogOpen}
        setWordSummaryDialogOpen={setWordSummaryDialogOpen}
        wordSummaryModel={wordSummaryModel}
        setWordSummaryModel={setWordSummaryModel}
        wordSummaryLoading={wordSummaryLoading}
        wordSummaryProgress={wordSummaryProgress}
        wordSummaryStage={wordSummaryStage}
        submitWordSummary={submitWordSummary}
      />

      <LiteratureSearchDialog
        open={literatureSearchDialogOpen}
        onOpenChange={setLiteratureSearchDialogOpen}
        entity={literatureSearchEntity}
        setEntity={setLiteratureSearchEntity}
        entities={literatureSearchEntities}
        mode={literatureSearchMode}
        setMode={setLiteratureSearchMode}
        days={literatureSearchDays}
        setDays={setLiteratureSearchDays}
        dateFrom={literatureSearchDateFrom}
        setDateFrom={setLiteratureSearchDateFrom}
        dateTo={literatureSearchDateTo}
        setDateTo={setLiteratureSearchDateTo}
        yearFrom={literatureSearchYearFrom}
        setYearFrom={setLiteratureSearchYearFrom}
        yearTo={literatureSearchYearTo}
        setYearTo={setLiteratureSearchYearTo}
        dryRun={literatureSearchDryRun}
        setDryRun={setLiteratureSearchDryRun}
        translateZh={literatureSearchTranslateZh}
        setTranslateZh={setLiteratureSearchTranslateZh}
        task={literatureSearchTask}
        error={literatureSearchError}
        submitting={literatureSearchSubmitting}
        onStart={startLiteratureSearch}
      />

      <PaperImportDialog
        open={uploadDialogOpen}
        onOpenChange={setUploadDialogOpen}
        role={role}
        onOpenLiteratureSearch={openLiteratureSearchDialog}
        doiText={doiText}
        setDoiText={setDoiText}
        onDoiUpload={handleDoiUpload}
        isUploadingDois={isUploadingDois}
        doiResults={doiResults}
        pdfResults={pdfResults}
        setPdfResults={setPdfResults}
        pdfFiles={pdfFiles}
        setIsUploadingPdfs={setIsUploadingPdfs}
        onRefetch={refetch}
      />

      <AddPdfDialog
        open={addPdfDialogOpen}
        onOpenChange={setAddPdfDialogOpen}
        paper={addPdfPaper}
        onAutoDownload={handleAutoDownloadPdf}
        downloadLoading={pdfDownloadLoading}
        downloadResult={pdfDownloadResult}
        dropHover={pdfDropHover}
        setDropHover={setPdfDropHover}
        onDropUpload={handlePdfDropUpload}
      />

      <QuotaRequestDialog
        open={quotaRequestOpen}
        onOpenChange={setQuotaRequestOpen}
        quotaExceeded={quotaExceeded}
        paperIds={quotaRequestPaperIds}
        reason={quotaRequestReason}
        setReason={setQuotaRequestReason}
        submitting={quotaRequestSubmitting}
        onSubmit={submitQuotaRequest}
      />

    </div>
  )
}
