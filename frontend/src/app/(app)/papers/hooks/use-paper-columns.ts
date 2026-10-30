import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type DragEvent } from "react"
import { STORAGE_KEYS } from "@/lib/storage"

export interface PapersColumnDef {
  key: string
  label: string
  defaultWidth: number
  sortable?: boolean
  defaultVisible: boolean
  center?: boolean
}

export const PAPERS_COLUMN_DEFS: PapersColumnDef[] = [
  { key: "internal_id", label: "Cite ID", defaultWidth: 64, sortable: true, defaultVisible: true, center: true },
  { key: "title", label: "标题", defaultWidth: 340, sortable: true, defaultVisible: true },
  { key: "first_author", label: "第一作者", defaultWidth: 120, defaultVisible: false },
  { key: "first_author_affiliation", label: "第一作者单位", defaultWidth: 180, defaultVisible: false },
  { key: "corresponding_author", label: "通讯作者", defaultWidth: 120, defaultVisible: false },
  { key: "corresponding_author_affiliation", label: "通讯作者单位", defaultWidth: 180, defaultVisible: false },
  { key: "region", label: "地区", defaultWidth: 100, defaultVisible: false, center: true },
  { key: "subject_diseases", label: "疾病", defaultWidth: 140, defaultVisible: false },
  { key: "journal_name", label: "期刊", defaultWidth: 128, sortable: true, defaultVisible: true },
  { key: "year", label: "发表年份", defaultWidth: 64, sortable: true, defaultVisible: true, center: true },
  { key: "impact_factor", label: "IF", defaultWidth: 64, sortable: true, defaultVisible: true, center: true },
  { key: "cas_quartile", label: "JCR 分区", defaultWidth: 80, sortable: true, defaultVisible: true, center: true },
  { key: "entity_label", label: "课题", defaultWidth: 80, sortable: true, defaultVisible: true, center: true },
  { key: "review_outcome", label: "审核", defaultWidth: 68, sortable: true, defaultVisible: false, center: true },
  { key: "metadata_complete", label: "元数据", defaultWidth: 68, sortable: true, defaultVisible: false, center: true },
  { key: "attachments", label: "附件", defaultWidth: 96, defaultVisible: true, center: true },
  { key: "article", label: "推文", defaultWidth: 96, defaultVisible: true, center: true },
  { key: "citation", label: "引用", defaultWidth: 56, defaultVisible: true, center: true },
]

export interface PapersColumnState {
  key: string
  visible: boolean
  width: number
  /** 标题列专用：首次拖拽后钉为固定宽度，不再自动吸收剩余空间 */
  pinned?: boolean
}

const PAPERS_COLUMNS_KEY = STORAGE_KEYS.papersColumns

function defaultPapersColumns(): PapersColumnState[] {
  return PAPERS_COLUMN_DEFS.map((d) => ({ key: d.key, visible: d.defaultVisible, width: d.defaultWidth }))
}

function loadPapersColumns(): PapersColumnState[] {
  const defaults = defaultPapersColumns()
  if (typeof window === "undefined") return defaults
  try {
    const stored = JSON.parse(localStorage.getItem(PAPERS_COLUMNS_KEY) || "[]") as PapersColumnState[]
    if (!Array.isArray(stored)) return defaults
    const validKeys = new Set(PAPERS_COLUMN_DEFS.map((d) => d.key))
    const merged: PapersColumnState[] = stored
      .filter((c) => c && validKeys.has(c.key))
      .map((c) => ({
        key: c.key,
        visible: Boolean(c.visible),
        width: typeof c.width === "number" && c.width >= 56 ? c.width : (PAPERS_COLUMN_DEFS.find((d) => d.key === c.key)?.defaultWidth ?? 96),
        pinned: Boolean(c.pinned),
      }))
    for (const d of defaults) {
      if (!merged.some((c) => c.key === d.key)) merged.push(d)
    }
    return merged
  } catch {
    return defaults
  }
}



export function usePaperColumns(role: string) {
  // 自定义显示列：显隐/顺序/列宽，持久化到 localStorage
  const [columns, setColumns] = useState<PapersColumnState[]>(loadPapersColumns)
  const [columnDialogOpen, setColumnDialogOpen] = useState(false)
  useEffect(() => {
    try { localStorage.setItem(PAPERS_COLUMNS_KEY, JSON.stringify(columns)) } catch { /* 存储不可用时忽略 */ }
  }, [columns])
  const visibleColumns = useMemo(() => columns.filter((c) => c.visible), [columns])
  // 受限用户使用固定的基础数据列，无显示列自定义
  const effectiveColumns = useMemo(() => {
    if (role === "limited") {
      const allowed = new Set(["internal_id", "title", "journal_name", "year", "impact_factor", "cas_quartile", "entity_label"])
      return PAPERS_COLUMN_DEFS.filter((d) => allowed.has(d.key)).map((d): PapersColumnState => ({ key: d.key, visible: true, width: d.defaultWidth }))
    }
    return visibleColumns
  }, [visibleColumns, role])
  // 表格总宽 = 选择框列(40px) + 各数据列宽之和，保证拖出的列宽 1:1 生效
  const totalTableWidth = useMemo(
    () => 40 + effectiveColumns.reduce((sum, c) => sum + c.width, 0),
    [effectiveColumns],
  )
  // 标题列被手动拖宽/窄过后变为固定宽，表格不再强制撑满页面
  const titlePinned = columns.find((c) => c.key === "title")?.pinned ?? false
  const columnResizeRef = useRef<{ key: string; startX: number; startWidth: number; moved: boolean } | null>(null)
  const suppressSortClickRef = useRef(false)
  const startColumnResize = (e: ReactPointerEvent, key: string) => {
    e.preventDefault()
    e.stopPropagation()
    const col = columns.find((c) => c.key === key)
    if (!col) return
    let startWidth = col.width
    if (key === "title" && !col.pinned) {
      // 弹性标题列首次拖拽：以当前实际渲染宽度为起点钉为固定宽
      const th = (e.target as HTMLElement).closest("th")
      if (th) startWidth = Math.round(th.getBoundingClientRect().width)
      setColumns((prev) => prev.map((c) => (c.key === "title" ? { ...c, width: startWidth, pinned: true } : c)))
    }
    columnResizeRef.current = { key, startX: e.clientX, startWidth, moved: false }
    const onMove = (ev: PointerEvent) => {
      const start = columnResizeRef.current
      if (!start) return
      if (Math.abs(ev.clientX - start.startX) > 3) start.moved = true
      const next = Math.max(56, Math.round(start.startWidth + ev.clientX - start.startX))
      setColumns((prev) => prev.map((c) => (c.key === start.key ? { ...c, width: next } : c)))
    }
    const onUp = () => {
      // 拖拽结束后浏览器会补发一次 click，吞掉它避免误触排序
      if (columnResizeRef.current?.moved) suppressSortClickRef.current = true
      columnResizeRef.current = null
      window.removeEventListener("pointermove", onMove)
    }
    window.addEventListener("pointermove", onMove)
    window.addEventListener("pointerup", onUp, { once: true })
  }
  const toggleColumn = (key: string) => {
    setColumns((prev) => prev.map((c) => (c.key === key ? { ...c, visible: !c.visible } : c)))
  }
  const moveColumn = (key: string, dir: -1 | 1) => {
    setColumns((prev) => {
      const idx = prev.findIndex((c) => c.key === key)
      const target = idx + dir
      if (idx < 0 || target < 0 || target >= prev.length) return prev
      const next = [...prev]
      const tmp = next[idx]
      next[idx] = next[target]
      next[target] = tmp
      return next
    })
  }
  const resetColumns = () => setColumns(defaultPapersColumns())
  // 显示列弹窗内拖拽排序
  const [columnDragIdx, setColumnDragIdx] = useState<number | null>(null)
  const handleColumnDragStart = (idx: number) => setColumnDragIdx(idx)
  const handleColumnDragOver = (e: DragEvent, idx: number) => {
    e.preventDefault()
    if (columnDragIdx === null || columnDragIdx === idx) return
    setColumns((prev) => {
      const next = [...prev]
      const [moved] = next.splice(columnDragIdx, 1)
      next.splice(idx, 0, moved)
      return next
    })
    setColumnDragIdx(idx)
  }
  const handleColumnDragEnd = () => setColumnDragIdx(null)

  return {
    columns,
    setColumns,
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
  }
}
