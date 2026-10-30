import { useEffect, useRef } from "react"
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query"
import { apiClient } from "@/lib/api"
import { Paper } from "@/lib/types"
import type { AppliedPaperFilters, ConfigOptions, LiteratureSearchTask, PaperFilterOptions, PapersResponse } from "../lib/paper-types"
import { getApiErrorMessage } from "@/lib/api-errors"

const PAGE_SIZE = 20

interface UsePaperQueriesArgs {
  applied: AppliedPaperFilters
  page: number
  sortBy: string
  sortOrder: "asc" | "desc"
  isMobile: boolean
  deviceReady: boolean
  literatureSearchTask: LiteratureSearchTask | null
  setLiteratureSearchTask: (task: LiteratureSearchTask) => void
  setLiteratureSearchError: (message: string) => void
}

export function usePaperQueries({
  applied, page, sortBy, sortOrder, isMobile, deviceReady,
  literatureSearchTask, setLiteratureSearchTask, setLiteratureSearchError,
}: UsePaperQueriesArgs) {
  const qc = useQueryClient()
  const {
    data,
    isLoading,
    isFetching,
    isError: isPapersError,
    error: papersError,
    refetch,
  } = useQuery<PapersResponse>({
    queryKey: [
      "papers",
      applied.q,
      applied.entity_label,
      applied.journal,
      applied.yearFrom,
      applied.yearTo,
      applied.country,
      applied.province,
      applied.city,
      applied.institutionType,
      applied.affiliation,
      applied.researchField,
      applied.researchTopic,
      applied.neuroscienceSubtopic,
      applied.tag,
      applied.subjectDisease,
      applied.ageMin,
      applied.ageMax,
      applied.studyDesign,
      applied.paradigmFilter,
      applied.hasMultimodal,
      applied.multimodalTechnique,
      applied.multimodalTiming,
      applied.reviewOutcome,
      page,
      sortBy,
      sortOrder,
    ],
    queryFn: () =>
      apiClient
        .get("/api/papers", {
          params: {
            q: applied.q || undefined,
            entity_label: applied.entity_label || undefined,
            journal: applied.journal || undefined,
            year_from: applied.yearFrom || undefined,
            year_to: applied.yearTo || undefined,
            country: applied.country || undefined,
            province: applied.province || undefined,
            city: applied.city || undefined,
            institution_type: applied.institutionType || undefined,
            affiliation: applied.affiliation || undefined,
            research_field: applied.researchField || undefined,
            research_topic: applied.researchTopic || undefined,
            neuroscience_subtopic: applied.neuroscienceSubtopic || undefined,
            tag: applied.tag || undefined,
            subject_disease: applied.subjectDisease || undefined,
            age_min: applied.ageMin || undefined,
            age_max: applied.ageMax || undefined,
            study_design: applied.studyDesign || undefined,
            paradigm: applied.paradigmFilter || undefined,
            has_multimodal: applied.hasMultimodal || undefined,
            multimodal_technique: applied.multimodalTechnique || undefined,
            multimodal_timing: applied.multimodalTiming || undefined,
            review_outcome: applied.reviewOutcome || undefined,
            page,
            page_size: PAGE_SIZE,
            sort_by: sortBy || undefined,
            sort_order: sortOrder,
          },
        })
        .then((r) => r.data),
    enabled: deviceReady && !isMobile,
    // 当列表中存在进行中的论文时自动轮询（每 5 秒）
    refetchInterval: (query) => {
      const papers = query.state.data?.items as Paper[] | undefined
      const active = papers?.some(
        (p) =>
          p.article_status === "generating" ||
          p.is_processing
      )
      return active ? 5000 : false
    },
  })



  useEffect(() => {
    if (!literatureSearchTask?.task_id || !["queued", "running"].includes(literatureSearchTask.status)) return
    let cancelled = false
    const poll = async () => {
      try {
        const res = await apiClient.get(`/api/literature-search/tasks/${literatureSearchTask.task_id}`)
        if (cancelled) return
        const next = res.data as LiteratureSearchTask
        setLiteratureSearchTask(next)
        if (next.status === "succeeded") {
          qc.invalidateQueries({ queryKey: ["papers"] })
          qc.invalidateQueries({ queryKey: ["paper-filter-options"] })
        }
      } catch (error: unknown) {
        if (!cancelled) setLiteratureSearchError(getApiErrorMessage(error, "获取任务状态失败"))
      }
    }
    void poll()
    const timer = window.setInterval(poll, 2000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [literatureSearchTask?.task_id, literatureSearchTask?.status, qc, setLiteratureSearchError, setLiteratureSearchTask])

    // Infinite query for mobile view
  const {
    data: infiniteData,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading: isInfiniteLoading,
    isError: isInfiniteError,
    error: infiniteError,
    isFetchNextPageError,
    refetch: refetchInfinite,
  } = useInfiniteQuery<PapersResponse>({
    queryKey: [
      "papers-infinite",
      applied.q,
      applied.entity_label,
      applied.journal,
      applied.yearFrom,
      applied.yearTo,
      applied.country,
      applied.province,
      applied.city,
      applied.institutionType,
      applied.affiliation,
      applied.researchField,
      applied.researchTopic,
      applied.neuroscienceSubtopic,
      applied.tag,
      applied.subjectDisease,
      applied.ageMin,
      applied.ageMax,
      applied.studyDesign,
      applied.paradigmFilter,
      applied.hasMultimodal,
      applied.multimodalTechnique,
      applied.multimodalTiming,
      applied.reviewOutcome,
      sortBy,
      sortOrder,
    ],
    queryFn: async ({ pageParam = 1 }) => {
      const res = await apiClient.get("/api/papers", {
        params: {
          q: applied.q || undefined,
          entity_label: applied.entity_label || undefined,
          journal: applied.journal || undefined,
          year_from: applied.yearFrom || undefined,
          year_to: applied.yearTo || undefined,
          country: applied.country || undefined,
          province: applied.province || undefined,
          city: applied.city || undefined,
          institution_type: applied.institutionType || undefined,
          affiliation: applied.affiliation || undefined,
          research_field: applied.researchField || undefined,
          research_topic: applied.researchTopic || undefined,
          neuroscience_subtopic: applied.neuroscienceSubtopic || undefined,
          tag: applied.tag || undefined,
          subject_disease: applied.subjectDisease || undefined,
          age_min: applied.ageMin || undefined,
          age_max: applied.ageMax || undefined,
          study_design: applied.studyDesign || undefined,
          paradigm: applied.paradigmFilter || undefined,
          has_multimodal: applied.hasMultimodal || undefined,
          multimodal_technique: applied.multimodalTechnique || undefined,
          multimodal_timing: applied.multimodalTiming || undefined,
          review_outcome: applied.reviewOutcome || undefined,
          page: pageParam,
          page_size: 20,
          sort_by: sortBy || undefined,
          sort_order: sortOrder,
        },
      })
      return res.data as PapersResponse
    },
    initialPageParam: 1,
    getNextPageParam: (lastPage) => {
      const { page, page_size, total } = lastPage
      return page * page_size < total ? page + 1 : undefined
    },
    enabled: deviceReady && isMobile,
  })

  const mobilePapers = infiniteData?.pages.flatMap((result) => result.items) ?? []
  const mobileTotal = infiniteData?.pages[0]?.total ?? 0
  const visiblePapers = isMobile ? mobilePapers : (data?.items ?? [])
  const filteredTotal = isMobile ? mobileTotal : (data?.total ?? 0)

  const { data: filterOptions } = useQuery<PaperFilterOptions>({
    queryKey: [
      "paper-filter-options", applied.q, applied.entity_label, applied.journal,
      applied.yearFrom, applied.yearTo, applied.country, applied.province,
      applied.city, applied.institutionType, applied.affiliation,
      applied.researchField, applied.researchTopic, applied.neuroscienceSubtopic,
      applied.tag, applied.subjectDisease, applied.ageMin, applied.ageMax,
      applied.studyDesign, applied.paradigmFilter, applied.hasMultimodal,
      applied.multimodalTechnique, applied.multimodalTiming,
      applied.reviewOutcome,
    ],
    queryFn: () => apiClient.get("/api/papers/filter-options", {
      params: {
        q: applied.q || undefined,
        entity_label: applied.entity_label || undefined,
        journal: applied.journal || undefined,
        year_from: applied.yearFrom || undefined,
        year_to: applied.yearTo || undefined,
        country: applied.country || undefined,
        province: applied.province || undefined,
        city: applied.city || undefined,
        institution_type: applied.institutionType || undefined,
        affiliation: applied.affiliation || undefined,
        research_field: applied.researchField || undefined,
        research_topic: applied.researchTopic || undefined,
        neuroscience_subtopic: applied.neuroscienceSubtopic || undefined,
        tag: applied.tag || undefined,
        subject_disease: applied.subjectDisease || undefined,
        age_min: applied.ageMin || undefined,
        age_max: applied.ageMax || undefined,
        study_design: applied.studyDesign || undefined,
        paradigm: applied.paradigmFilter || undefined,
        has_multimodal: applied.hasMultimodal || undefined,
        multimodal_technique: applied.multimodalTechnique || undefined,
        multimodal_timing: applied.multimodalTiming || undefined,
        review_outcome: applied.reviewOutcome || undefined,
      },
    }).then((r) => r.data),
    enabled: deviceReady,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
    placeholderData: (previous) => previous,
  })

  const { data: configOptions } = useQuery<ConfigOptions>({
    queryKey: ["config-options"],
    queryFn: () => apiClient.get("/api/config/options").then((response) => response.data),
    enabled: deviceReady,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  // Infinite scroll for mobile
  const loadMoreRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const hasNextRef = useRef(hasNextPage)
  const fetchingRef = useRef(isFetchingNextPage)
  const fetchNextRef = useRef(fetchNextPage)

  useEffect(() => {
    hasNextRef.current = hasNextPage
    fetchingRef.current = isFetchingNextPage
    fetchNextRef.current = fetchNextPage
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  useEffect(() => {
    if (!deviceReady || !isMobile) return
    const el = loadMoreRef.current
    const root = scrollContainerRef.current
    if (!el) return
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && hasNextRef.current && !fetchingRef.current) {
          fetchNextRef.current()
        }
      },
      { root, threshold: 0.1 }
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [deviceReady, isMobile, infiniteData?.pages.length])

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1

  return {
    data, isLoading, isFetching, isPapersError, papersError, refetch,
    fetchNextPage, hasNextPage, isFetchingNextPage, isInfiniteLoading,
    isInfiniteError, infiniteError, isFetchNextPageError, refetchInfinite,
    mobilePapers, mobileTotal, visiblePapers, filteredTotal, loadMoreRef, scrollContainerRef,
    totalPages, filterOptions, configOptions,
  }
}
