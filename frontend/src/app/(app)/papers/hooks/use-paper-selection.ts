import { useState, type MouseEvent } from "react"
import { apiClient } from "@/lib/api"
import type { Paper } from "@/lib/types"
import type { AppliedPaperFilters, PapersResponse } from "../lib/paper-types"

interface UsePaperSelectionOptions {
  data?: PapersResponse
  visiblePapers: Paper[]
  applied: AppliedPaperFilters
  sortBy: string
  sortOrder: string
}

export function usePaperSelection({ data, visiblePapers, applied, sortBy, sortOrder }: UsePaperSelectionOptions) {
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [lastSelectedId, setLastSelectedId] = useState<number | null>(null)
  const [selectAllFiltered, setSelectAllFiltered] = useState(false)

  function toggleSelect(id: number, e?: MouseEvent) {
    const isShift = e?.shiftKey

    if (isShift && lastSelectedId !== null && data?.items) {
      // Shift+点击：范围选择（根据当前项状态决定添加或移除）
      const ids = data.items.map((p) => p.id)
      const startIdx = ids.indexOf(lastSelectedId)
      const endIdx = ids.indexOf(id)
      if (startIdx !== -1 && endIdx !== -1) {
        const [min, max] = [Math.min(startIdx, endIdx), Math.max(startIdx, endIdx)]
        const rangeIds = ids.slice(min, max + 1)
        // 如果当前点击项已被选中，则移除整个范围；否则添加
        const shouldRemove = selectAllFiltered || selectedIds.has(id)
        setSelectedIds((prev) => {
          const next = selectAllFiltered
            ? new Set(visiblePapers.map((paper) => paper.id))
            : new Set(prev)
          rangeIds.forEach((rid) => {
            if (shouldRemove) next.delete(rid)
            else next.add(rid)
          })
          return next
        })
        setSelectAllFiltered(false)
      }
      setLastSelectedId(id)
    } else {
      // 普通 / Ctrl+点击：单个 toggle（保持其他选中）
      setSelectedIds((prev) => {
        const next = selectAllFiltered
          ? new Set(visiblePapers.map((paper) => paper.id))
          : new Set(prev)
        if (selectAllFiltered || next.has(id)) { next.delete(id) }
        else { next.add(id) }
        // 手动选择/取消选择时，重置全选状态
        setSelectAllFiltered(false)
        return next
      })
      setLastSelectedId(id)
    }
  }

  async function getExplicitlySelectedPapers(): Promise<Paper[]> {
    const ids = Array.from(selectedIds)
    const paperMap = new Map(visiblePapers.map((paper) => [paper.id, paper]))
    const missingIds = ids.filter((id) => !paperMap.has(id))

    // 桌面端翻页后，已选 ID 仍然保留；按 ID 补取不在当前页的论文对象。
    for (let index = 0; index < missingIds.length; index += 200) {
      const chunk = missingIds.slice(index, index + 200)
      const response = await apiClient.get<PapersResponse>("/api/papers", {
        params: {
          paper_ids: chunk.join(","),
          page: 1,
          page_size: chunk.length,
        },
      })
      response.data.items.forEach((paper) => paperMap.set(paper.id, paper))
    }

    return ids.flatMap((id) => {
      const paper = paperMap.get(id)
      return paper ? [paper] : []
    })
  }

  function setPaperSelected(id: number, checked: boolean) {
    setSelectedIds((prev) => {
      // 取消“全部筛选结果”中的单篇时，退回当前已加载论文的显式 ID 集合。
      const next = selectAllFiltered
        ? new Set(visiblePapers.map((paper) => paper.id))
        : new Set(prev)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
    setSelectAllFiltered(false)
    setLastSelectedId(id)
  }

  function toggleSelectAll() {
    if (!data?.items) return
    if (selectAllFiltered) {
      setSelectAllFiltered(false)
      setSelectedIds(new Set())
      setLastSelectedId(null)
      return
    }

    const pageIds = data.items.map((paper) => paper.id)
    const allCurrentPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id))
    setSelectAllFiltered(false)
    setSelectedIds((prev) => {
      const next = new Set(prev)
      pageIds.forEach((id) => {
        if (allCurrentPageSelected) next.delete(id)
        else next.add(id)
      })
      return next
    })
  }

  function toggleSelectAllFiltered() {
    if (selectAllFiltered) {
      setSelectAllFiltered(false)
      setSelectedIds(new Set())
    } else {
      setSelectAllFiltered(true)
      // 同步当前视图的 ID，保持桌面分页与移动端无限列表的多选语义一致。
      setSelectedIds(new Set(visiblePapers.map((p) => p.id)))
    }
  }

  /** 获取当前筛选条件下所有论文（分页获取，突破 page_size=200 限制） */
  async function getAllFilteredPapers(): Promise<Paper[]> {
    const PAGE_SIZE = 200
    const baseParams = {
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
      // 全选后的批量操作必须沿用列表排序；导出接口会按这里返回的 ID 顺序写入 Excel。
      sort_by: sortBy || undefined,
      sort_order: sortOrder,
    }

    // 第一页：获取总数 + 首批数据
    const first = await apiClient.get("/api/papers", {
      params: { ...baseParams, page: 1, page_size: PAGE_SIZE },
    })
    const total: number = first.data?.total ?? 0
    if (total === 0) return []

    const allPapers: Paper[] = first.data?.items ?? []

    // 如果还有更多，继续分页获取
    let page = 2
    while (allPapers.length < total && allPapers.length < 10000) {
      const resp = await apiClient.get("/api/papers", {
        params: { ...baseParams, page, page_size: PAGE_SIZE },
      })
      const items: Paper[] = resp.data?.items ?? []
      if (items.length === 0) break
      allPapers.push(...items)
      page++
    }

    return allPapers
  }

  return {
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
  }
}
