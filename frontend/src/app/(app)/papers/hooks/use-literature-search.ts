import { useState } from "react"
import { apiClient } from "@/lib/api"
import { getApiErrorDetail, getApiErrorMessage, isRecord } from "@/lib/api-errors"
import type { LiteratureSearchTask } from "../lib/paper-types"

const TARGET_LABEL = process.env.NEXT_PUBLIC_TARGET_LABEL || "qMRLab"

export function useLiteratureSearch() {
  const [literatureSearchDialogOpen, setLiteratureSearchDialogOpen] = useState(false)
  const [literatureSearchMode, setLiteratureSearchMode] = useState<"recent" | "range" | "years">("recent")
  const [literatureSearchDays, setLiteratureSearchDays] = useState("60")
  const [literatureSearchDateFrom, setLiteratureSearchDateFrom] = useState("")
  const [literatureSearchDateTo, setLiteratureSearchDateTo] = useState("")
  const [literatureSearchYearFrom, setLiteratureSearchYearFrom] = useState(() => String(new Date().getFullYear() - 1))
  const [literatureSearchYearTo, setLiteratureSearchYearTo] = useState(() => String(new Date().getFullYear()))
  const [literatureSearchEntity, setLiteratureSearchEntity] = useState(TARGET_LABEL)
  const [literatureSearchEntities, setLiteratureSearchEntities] = useState<{key: string, label: string}[]>([])
  const [literatureSearchDryRun, setLiteratureSearchDryRun] = useState(false)
  const [literatureSearchTranslateZh, setLiteratureSearchTranslateZh] = useState(true)
  const [literatureSearchTask, setLiteratureSearchTask] = useState<LiteratureSearchTask | null>(null)
  const [literatureSearchSubmitting, setLiteratureSearchSubmitting] = useState(false)
  const [literatureSearchError, setLiteratureSearchError] = useState("")

  async function openLiteratureSearchDialog() {
    setLiteratureSearchDialogOpen(true)
    setLiteratureSearchError("")
    try {
      const res = await apiClient.get("/api/literature-search/tasks/latest")
      if (res.data) setLiteratureSearchTask(res.data as LiteratureSearchTask)
    } catch {
      // 首次使用或服务刚重启时没有历史任务，不影响创建新任务。
    }
    // 加载可检索课题列表
    try {
      const br = await apiClient.get("/api/literature-search/entities")
      if (Array.isArray(br.data)) setLiteratureSearchEntities(br.data)
    } catch { /* 降级：课题列表加载失败仍可使用 */ }
  }

  async function startLiteratureSearch() {
    setLiteratureSearchError("")
    const days = Number(literatureSearchDays)
    const yearFrom = Number(literatureSearchYearFrom)
    const yearTo = Number(literatureSearchYearTo)
    if (literatureSearchMode === "recent" && (!Number.isInteger(days) || days < 1 || days > 1095)) {
      setLiteratureSearchError("检索天数必须在 1–1095 之间")
      return
    }
    if (literatureSearchMode === "range") {
      const fromTime = Date.parse(`${literatureSearchDateFrom}T00:00:00Z`)
      const toTime = Date.parse(`${literatureSearchDateTo}T00:00:00Z`)
      if (!Number.isFinite(fromTime) || !Number.isFinite(toTime) || literatureSearchDateFrom > literatureSearchDateTo) {
        setLiteratureSearchError("请填写有效且顺序正确的起止日期")
        return
      }
      const [year, month, day] = literatureSearchDateFrom.split("-").map(Number)
      const maxDate = new Date(Date.UTC(year + 3, month, 0))
      maxDate.setUTCDate(Math.min(day, maxDate.getUTCDate()))
      const maxDateString = maxDate.toISOString().slice(0, 10)
      if (literatureSearchDateTo > maxDateString) {
        setLiteratureSearchError("日期范围不能超过三年")
        return
      }
    }
    if (literatureSearchMode === "years" && (
      !Number.isInteger(yearFrom) || !Number.isInteger(yearTo)
      || yearFrom < 2000 || yearTo > 2100 || yearFrom > yearTo
    )) {
      setLiteratureSearchError("请填写有效的起止年份")
      return
    }
    setLiteratureSearchSubmitting(true)
    try {
      const entityKey = literatureSearchEntities.find(b => b.label === literatureSearchEntity)?.key || literatureSearchEntities[0]?.key
      if (!entityKey) {
        setLiteratureSearchError("当前没有配置可检索课题")
        return
      }
      const res = await apiClient.post("/api/literature-search/tasks", {
        mode: literatureSearchMode,
        entity_key: entityKey,
        ...(literatureSearchMode === "recent" ? { days } : {}),
        ...(literatureSearchMode === "range" ? {
          date_from: literatureSearchDateFrom,
          date_to: literatureSearchDateTo,
        } : {}),
        ...(literatureSearchMode === "years" ? { year_from: yearFrom, year_to: yearTo } : {}),
        dry_run: literatureSearchDryRun,
        translate_zh: literatureSearchTranslateZh,
      })
      setLiteratureSearchTask(res.data as LiteratureSearchTask)
    } catch (error: unknown) {
      const detail = getApiErrorDetail(error)
      if (isRecord(detail) && typeof detail.task_id === "string") {
        try {
          const running = await apiClient.get(`/api/literature-search/tasks/${detail.task_id}`)
          setLiteratureSearchTask(running.data as LiteratureSearchTask)
        } catch { /* show the original conflict below */ }
      }
      setLiteratureSearchError(getApiErrorMessage(error, "启动检索失败"))
    } finally {
      setLiteratureSearchSubmitting(false)
    }
  }

  return {
    literatureSearchDialogOpen, setLiteratureSearchDialogOpen,
    literatureSearchMode, setLiteratureSearchMode,
    literatureSearchDays, setLiteratureSearchDays,
    literatureSearchDateFrom, setLiteratureSearchDateFrom,
    literatureSearchDateTo, setLiteratureSearchDateTo,
    literatureSearchYearFrom, setLiteratureSearchYearFrom,
    literatureSearchYearTo, setLiteratureSearchYearTo,
    literatureSearchEntity, setLiteratureSearchEntity,
    literatureSearchEntities, setLiteratureSearchEntities,
    literatureSearchDryRun, setLiteratureSearchDryRun,
    literatureSearchTranslateZh, setLiteratureSearchTranslateZh,
    literatureSearchTask, setLiteratureSearchTask,
    literatureSearchSubmitting, setLiteratureSearchSubmitting,
    literatureSearchError, setLiteratureSearchError,
    openLiteratureSearchDialog, startLiteratureSearch,
  }
}
