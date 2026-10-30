import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { X } from "lucide-react"
import type { ClassificationOptionGroup, FilterOption, PaperFilterOptions } from "../lib/paper-types"

export function GroupedFilterOptionGroups({
  options,
  groups,
}: {
  options: FilterOption[]
  groups: ClassificationOptionGroup[]
}) {
  if (!groups.length) {
    return options.map((option) => (
      <option key={option.value} value={option.value}>{option.value} ({option.count})</option>
    ))
  }
  const optionMap = new Map(options.map((option) => [option.value, option]))
  const included = new Set(groups.flatMap((group) => group.options))
  const remaining = options.filter((option) => !included.has(option.value))
  return (
    <>
      {groups.map((group) => {
        const available = group.options.map((value) => optionMap.get(value)).filter((option): option is FilterOption => Boolean(option))
        if (!available.length) return null
        return (
          <optgroup key={group.label} label={group.label}>
            {available.map((option) => (
              <option key={option.value} value={option.value}>{option.value} ({option.count})</option>
            ))}
          </optgroup>
        )
      })}
      {remaining.length > 0 && (
        <optgroup label="自定义分类">
          {remaining.map((option) => (
            <option key={option.value} value={option.value}>{option.value} ({option.count})</option>
          ))}
        </optgroup>
      )}
    </>
  )
}

import type { usePaperFilters } from "../hooks/use-paper-filters"

type PaperFilters = ReturnType<typeof usePaperFilters>

const REVIEW_FILTER_VALUES = [
  ["", "全部"], ["unreviewed", "未审核"], ["approved", "已通过"], ["excluded", "已排除"],
] as const

function LifecycleFilterControls({ filters, filterOptions, role, onReviewChange }: {
  filters: PaperFilters
  filterOptions?: PaperFilterOptions
  role: string
  onReviewChange: (value: string) => void
}) {
  if (role === "limited") return null
  const { reviewOutcome } = filters
  const reviewCounts = new Map((filterOptions?.review_outcomes || []).map((item) => [item.value, item.count]))
  return (
    <div>
      <label className="text-xs font-medium text-muted-foreground">审核状态</label>
      <div className="mt-1 flex flex-wrap gap-1">
        {REVIEW_FILTER_VALUES.filter(([value]) => value !== "excluded" || role === "admin" || role === "reviewer").map(([value, label]) => (
          <button key={value || "all"} type="button" onClick={() => onReviewChange(value)}
            aria-pressed={reviewOutcome === value}
            className={`rounded-full px-2 py-1 text-[10px] ${reviewOutcome === value ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}>
            {label}{value ? ` (${reviewCounts.get(value) ?? 0})` : ""}
          </button>
        ))}
      </div>
    </div>
  )
}

interface FilterPanelProps {
  filters: PaperFilters
  filterOptions?: PaperFilterOptions
  entityLabels: string[]
  role: string
  targetConfigured: boolean
  showMobileFilters: boolean
  setShowMobileFilters: (open: boolean) => void
  setSelectedIds: (ids: Set<number>) => void
  setLastSelectedId: (id: number | null) => void
  onCloseMobileFilters: () => void
  onApply: () => void
  onReset: () => void
  onReviewChange: (value: string) => void
}


export function MobileFilterPanel(props: FilterPanelProps) {
  const { filters, filterOptions, entityLabels, role, showMobileFilters,
    setShowMobileFilters, onCloseMobileFilters, onApply, onReset, onReviewChange } = props
  const {
    entityLabel, setEntityLabel, yearFrom, setYearFrom, yearTo, setYearTo,
    country, setCountry, province, setProvince, city, setCity,
    institutionType, setInstitutionType, researchTopic, setResearchTopic,
    neuroscienceSubtopic, setNeuroscienceSubtopic, subjectDisease, setSubjectDisease,
    ageMin, setAgeMin, ageMax, setAgeMax, studyDesign, setStudyDesign,
    paradigmFilter, setParadigmFilter, hasMultimodal, setHasMultimodal,
    multimodalTechnique, setMultimodalTechnique, multimodalTiming, setMultimodalTiming,
    tag, setTag,
  } = filters
  const allTagCategories = filterOptions?.tag_categories || []

  function handleMultimodalChange(value: string) {
    setHasMultimodal(value)
    if (value === "false") {
      setMultimodalTechnique("")
      setMultimodalTiming("")
    }
  }
  function handleMultimodalTechniqueChange(value: string) {
    setMultimodalTechnique(value)
    if (value) setHasMultimodal("true")
  }
  function handleMultimodalTimingChange(value: string) {
    setMultimodalTiming(value)
    if (value) setHasMultimodal("true")
  }

  function handleInstitutionTypeChange(value: string) { setInstitutionType(value) }
  function handleResearchTopicChange(value: string) { setResearchTopic(value) }
  function handleNeuroscienceSubtopicChange(value: string) { setNeuroscienceSubtopic(value) }
  return (
    <>
      {showMobileFilters && (
        <>
          {/* Backdrop */}
          <button type="button" aria-label="关闭过滤器" className="md:hidden fixed inset-0 bg-black/30 z-30" onClick={onCloseMobileFilters} />
          {/* Floating panel */}
          <div className="md:hidden fixed inset-x-3 bottom-[max(0.75rem,env(safe-area-inset-bottom,0px))] z-40 max-h-[calc(100dvh-4rem)] rounded-xl border bg-card shadow-xl overflow-hidden flex flex-col">
            <div className="shrink-0 px-3 py-2.5 flex items-center justify-between border-b">
              <span className="text-sm font-semibold">筛选论文</span>
              <Button size="icon-sm" variant="ghost" onClick={onCloseMobileFilters} aria-label="关闭过滤器">
                <X className="h-4 w-4" />
              </Button>
            </div>
            {/* Filters */}
            <div className="flex-1 min-h-0 overflow-y-auto scrollbar-none p-3 space-y-3">
              {/* 课题 */}
              <div className="space-y-1.5 rounded-lg border p-3">
                <label className="text-xs font-medium text-muted-foreground">课题</label>
                <select
                  value={entityLabel}
                  onChange={(e) => setEntityLabel(e.target.value)}
                  className="w-full h-9 text-xs border rounded-lg px-2 bg-background"
                >
                  <option value="">全部课题</option>
                  {entityLabels.map((b) => (
                    <option key={b} value={b}>{b}</option>
                  ))}
                </select>
              </div>

              <LifecycleFilterControls filters={filters} filterOptions={filterOptions} role={role} onReviewChange={onReviewChange} />

              {/* 年份 */}
              <div className="space-y-1.5 rounded-lg border p-3">
                <label className="text-xs font-medium text-muted-foreground">年份</label>
                <div className="flex items-center gap-1">
                  <Input
                    type="number"
                    placeholder="起始"
                    value={yearFrom}
                    onChange={(e) => setYearFrom(e.target.value)}
                    className="flex-1 text-xs h-9 rounded-lg"
                  />
                  <span className="text-muted-foreground text-xs">—</span>
                  <Input
                    type="number"
                    placeholder="结束"
                    value={yearTo}
                    onChange={(e) => setYearTo(e.target.value)}
                    className="flex-1 text-xs h-9 rounded-lg"
                  />
                </div>
              </div>

              {/* 区域 */}
              <div className="space-y-1.5 rounded-lg border p-3">
                <label className="text-xs font-medium text-muted-foreground">区域</label>
                <div className="grid grid-cols-3 gap-1">
                  <select
                    value={country}
                    onChange={(e) => {
                      const value = e.target.value
                      setCountry(value); setProvince(""); setCity("")
                    }}
                    className="h-9 min-w-0 text-xs border rounded-lg px-2 bg-background"
                  >
                    <option value="">国家/地区</option>
                    {(filterOptions?.countries || []).map((option) => (
                      <option key={option.value} value={option.value}>{option.value} ({option.count})</option>
                    ))}
                  </select>
                  <select
                    value={province}
                    onChange={(e) => {
                      const value = e.target.value
                      setProvince(value); setCity("")
                    }}
                    className="h-9 min-w-0 text-xs border rounded-lg px-2 bg-background"
                  >
                    <option value="">省份</option>
                    {(filterOptions?.provinces || []).map((option) => (
                      <option key={option.value} value={option.value}>{option.value} ({option.count})</option>
                    ))}
                  </select>
                  <select
                    value={city}
                    onChange={(e) => {
                      const value = e.target.value
                      setCity(value)
                    }}
                    className="h-9 min-w-0 text-xs border rounded-lg px-2 bg-background"
                  >
                    <option value="">城市</option>
                    {(filterOptions?.cities || []).map((option) => (
                      <option key={option.value} value={option.value}>{option.value} ({option.count})</option>
                    ))}
                  </select>
                </div>
              </div>

              {/* 通讯作者单位信息 */}
              <div className="space-y-1.5 rounded-lg border p-3">
                <label className="text-xs font-medium text-muted-foreground">通讯作者单位</label>
                <div className="grid grid-cols-1 gap-1">
                  <select
                    value={institutionType}
                    onChange={(e) => handleInstitutionTypeChange(e.target.value)}
                    className="h-9 min-w-0 text-xs border rounded-lg px-2 bg-background"
                  >
                    <option value="">单位性质</option>
                    {(filterOptions?.institution_types || []).map((option) => (
                      <option key={option.value} value={option.value}>{option.value} ({option.count})</option>
                    ))}
                  </select>
                </div>
              </div>

              {Boolean(filterOptions?.research_topics?.length) && (
                <div className="space-y-1.5 rounded-lg border p-3">
                  <label className="text-xs font-medium text-muted-foreground">{filterOptions?.classification_catalogs?.dimension_labels?.research_topics || "研究主题"}</label>
                  <select
                    value={researchTopic}
                    onChange={(e) => handleResearchTopicChange(e.target.value)}
                    className="h-9 w-full text-xs border rounded-lg px-2 bg-background"
                  >
                    <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.research_topics || "研究主题"}</option>
                    <GroupedFilterOptionGroups
                      options={filterOptions?.research_topics || []}
                      groups={filterOptions?.classification_catalogs?.research_topic_groups || []}
                    />
                  </select>
                </div>
              )}
              {Boolean(filterOptions?.neuroscience_subtopics?.length) && (
                <div className="space-y-1.5 rounded-lg border p-3">
                  <label className="text-xs font-medium text-muted-foreground">{filterOptions?.classification_catalogs?.dimension_labels?.neuroscience_subtopics || "补充分类"}</label>
                  <select
                    value={neuroscienceSubtopic}
                    onChange={(e) => handleNeuroscienceSubtopicChange(e.target.value)}
                    className="h-9 w-full text-xs border rounded-lg px-2 bg-background"
                  >
                    <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.neuroscience_subtopics || "补充分类"}</option>
                    {(filterOptions?.neuroscience_subtopics || []).map((option) => (
                      <option key={option.value} value={option.value}>{option.value} ({option.count})</option>
                    ))}
                  </select>
                </div>
              )}

              {(Boolean(filterOptions?.diseases?.length || filterOptions?.age_range?.min != null || filterOptions?.age_range?.max != null)) && (
              <div className="space-y-2 rounded-lg border p-3">
                {(filterOptions?.diseases?.length ?? 0) > 0 && <label className="text-xs font-medium text-muted-foreground">{filterOptions?.classification_catalogs?.dimension_labels?.diseases || "疾病与研究对象"}</label>}
                {(filterOptions?.diseases?.length ?? 0) > 0 && <select
                  value={subjectDisease}
                  onChange={(e) => setSubjectDisease(e.target.value)}
                  className="h-9 w-full text-xs border rounded-lg px-2 bg-background"
                >
                  <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.diseases || "疾病与研究对象"}</option>
                  <GroupedFilterOptionGroups
                    options={filterOptions?.diseases || []}
                    groups={filterOptions?.classification_catalogs?.disease_groups || []}
                  />
                </select>}
                <div className="flex items-center gap-1">
                  <Input
                    type="number"
                    min={0}
                    max={120}
                    step="0.1"
                    placeholder="最低年龄"
                    value={ageMin}
                    onChange={(e) => setAgeMin(e.target.value)}
                    className="min-w-0 flex-1 text-xs h-9 rounded-lg"
                  />
                  <span className="text-muted-foreground text-xs">—</span>
                  <Input
                    type="number"
                    min={0}
                    max={120}
                    step="0.1"
                    placeholder="最高年龄"
                    value={ageMax}
                    onChange={(e) => setAgeMax(e.target.value)}
                    className="min-w-0 flex-1 text-xs h-9 rounded-lg"
                  />
                </div>
              </div>
              )}

              {/* 研究设计（受限用户角色隐藏） */}
              {role !== "limited" && Boolean(filterOptions?.study_designs?.length || filterOptions?.paradigms?.length) && (
              <div className="space-y-1.5 rounded-lg border p-3">
                <label className="text-xs font-medium text-muted-foreground">{filterOptions?.classification_catalogs?.dimension_labels?.study_design || "研究设计"}</label>
                <div className="grid grid-cols-2 gap-1">
                  {Boolean(filterOptions?.study_designs?.length) && <select
                    value={studyDesign}
                    onChange={(e) => setStudyDesign(e.target.value)}
                    className="h-9 min-w-0 text-xs border rounded-lg px-2 bg-background"
                  >
                    <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.study_design || "研究设计"}</option>
                    <GroupedFilterOptionGroups
                      options={filterOptions?.study_designs || []}
                      groups={filterOptions?.classification_catalogs?.study_design_groups || []}
                    />
                  </select>}
                  {Boolean(filterOptions?.paradigms?.length) && <select
                    value={paradigmFilter}
                    onChange={(e) => setParadigmFilter(e.target.value)}
                    className="h-9 min-w-0 text-xs border rounded-lg px-2 bg-background"
                  >
                    <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.paradigms || "研究范式"}</option>
                    <GroupedFilterOptionGroups
                      options={filterOptions?.paradigms || []}
                      groups={filterOptions?.classification_catalogs?.paradigm_groups || []}
                    />
                  </select>}
                </div>
              </div>
              )}

              {/* 多模态 */}
              {Boolean(filterOptions?.multimodal?.length || filterOptions?.multimodal_techniques?.length || filterOptions?.multimodal_timings?.length) && <div className="space-y-1.5 rounded-lg border p-3">
                <label className="text-xs font-medium text-muted-foreground">{filterOptions?.classification_catalogs?.dimension_labels?.multimodal || "多模态技术"}</label>
                <div className="grid grid-cols-2 gap-1">
                  {Boolean(filterOptions?.multimodal?.length) && <select
                    value={hasMultimodal}
                    onChange={(e) => handleMultimodalChange(e.target.value)}
                    className="h-9 min-w-0 text-xs border rounded-lg px-2 bg-background"
                  >
                    <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.multimodal_presence || "是否联用"}</option>
                    {(filterOptions?.multimodal || []).map((option) => (
                      <option key={option.value} value={option.value}>{option.label} ({option.count})</option>
                    ))}
                  </select>}
                  {Boolean(filterOptions?.multimodal_techniques?.length) && <select
                    value={multimodalTechnique}
                    onChange={(e) => handleMultimodalTechniqueChange(e.target.value)}
                    disabled={hasMultimodal === "false"}
                    className="h-9 min-w-0 text-xs border rounded-lg px-2 bg-background"
                  >
                    <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.multimodal || "多模态技术"}</option>
                    <GroupedFilterOptionGroups
                      options={filterOptions?.multimodal_techniques || []}
                      groups={filterOptions?.classification_catalogs?.multimodal_method_groups || []}
                    />
                  </select>}
                  {Boolean(filterOptions?.multimodal_timings?.length) && <select
                    value={multimodalTiming}
                    onChange={(e) => handleMultimodalTimingChange(e.target.value)}
                    disabled={hasMultimodal === "false"}
                    className="col-span-2 h-9 min-w-0 rounded-lg border bg-background px-2 text-xs"
                  >
                    <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.multimodal_timing || "联用方式"}</option>
                    {(filterOptions?.multimodal_timings || []).map((option) => (
                      <option key={option.value} value={option.value}>{option.label} ({option.count})</option>
                    ))}
                  </select>}
                </div>
              </div>}

              {allTagCategories.length > 0 && <details className="rounded-lg border p-3">
                <summary className="cursor-pointer text-xs font-medium text-muted-foreground">标签</summary>
                <div className="mt-2 space-y-2">
                {allTagCategories.map((cat) => (
                  <div key={cat.name} className="space-y-0.5">
                    <span className="text-[10px] text-muted-foreground/60">{cat.name}</span>
                    <div className="flex flex-wrap gap-1">
                      {cat.tags.map((t) => (
                        <button
                          key={t.name}
                          onClick={() => {
                            const next = tag === t.name ? "" : t.name
                            setTag(next)
                          }}
                          className={`inline-flex min-h-8 items-center px-2.5 rounded-lg text-[11px] font-medium transition-colors ${
                            tag === t.name
                              ? "bg-primary text-primary-foreground"
                              : "bg-muted text-muted-foreground hover:bg-muted/80"
                          }`}
                        >
                          {t.name}
                          <span className="ml-0.5 text-[9px] opacity-60">{t.count}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
                {allTagCategories.length === 0 && (
                  <span className="text-[11px] text-muted-foreground">暂无标签</span>
                )}
                </div>
              </details>}
            </div>
            {/* Action buttons */}
            <div data-bottom-bar className="safe-area-bottom-padding shrink-0 grid grid-cols-2 gap-2 border-t bg-card p-3">
              <Button size="sm" variant="outline" onClick={onReset} className="text-xs h-9 rounded-lg">重置</Button>
              <Button size="sm" onClick={() => { onApply(); setShowMobileFilters(false) }} className="text-xs h-9 rounded-lg">应用筛选</Button>
            </div>
          </div>
        </>
      )}
    </>
  )
}

export function DesktopFilterPanel(props: FilterPanelProps) {
  const { filters, filterOptions, entityLabels, role, onApply, onReset,
    setSelectedIds, setLastSelectedId, onReviewChange } = props
  const {
    q, setQ, entityLabel, setEntityLabel, yearFrom, setYearFrom, yearTo, setYearTo,
    country, setCountry, province, setProvince, city, setCity,
    institutionType, setInstitutionType, researchTopic, setResearchTopic,
    neuroscienceSubtopic, setNeuroscienceSubtopic, subjectDisease, setSubjectDisease,
    ageMin, setAgeMin, ageMax, setAgeMax, studyDesign, setStudyDesign,
    paradigmFilter, setParadigmFilter, hasMultimodal, setHasMultimodal,
    multimodalTechnique, setMultimodalTechnique, multimodalTiming, setMultimodalTiming,
    tag, setTag, setPage, syncURL, setApplied,
  } = filters
  const allTagCategories = filterOptions?.tag_categories || []

  function handleMultimodalChange(value: string) {
    setHasMultimodal(value)
    if (value === "false") {
      setMultimodalTechnique("")
      setMultimodalTiming("")
    }
  }
  function handleMultimodalTechniqueChange(value: string) {
    setMultimodalTechnique(value)
    if (value) setHasMultimodal("true")
  }
  function handleMultimodalTimingChange(value: string) {
    setMultimodalTiming(value)
    if (value) setHasMultimodal("true")
  }

  function handleInstitutionTypeChange(value: string) { setInstitutionType(value) }
  function handleResearchTopicChange(value: string) { setResearchTopic(value) }
  function handleNeuroscienceSubtopicChange(value: string) { setNeuroscienceSubtopic(value) }
  return (
      <aside className="hidden md:flex w-72 shrink-0 border-r bg-sidebar flex-col overflow-hidden">
        <div className="min-h-0 flex-1 overflow-y-auto scrollbar-none p-4">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">
            筛选条件
          </p>
          <div className="flex flex-col gap-3">
            {/* 课题下拉 */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-muted-foreground">课题</label>
              <select
                value={entityLabel}
                onChange={(e) => {
                  const v = e.target.value
                  setEntityLabel(v)
                  setApplied((prev) => ({ ...prev, entity_label: v }))
                  setPage(1)
                  setSelectedIds(new Set())
                  setLastSelectedId(null)
                  syncURL({ entity_label: v || undefined, page: "1" })
                }}
                className="w-full text-xs border rounded-lg px-2 py-1.5 bg-background"
              >
                <option value="">全部课题</option>
                {entityLabels.map((b) => (
                  <option key={b} value={b}>{b}</option>
                ))}
              </select>
            </div>
            <LifecycleFilterControls filters={filters} filterOptions={filterOptions} role={role} onReviewChange={onReviewChange} />
            {/* 统一搜索框：作者 / 关键词 / 编号 / 单位 / 期刊 */}
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted-foreground">搜索</label>
              <Input
                placeholder="作者、单位、期刊、标题、DOI、编号…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && onApply()}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted-foreground">年份</label>
              <div className="flex items-center gap-1">
                <Input
                  type="number"
                  placeholder="起始"
                  value={yearFrom}
                  onChange={(e) => setYearFrom(e.target.value)}
                  className="w-full"
                />
                <span className="text-muted-foreground text-xs">—</span>
                <Input
                  type="number"
                  placeholder="结束"
                  value={yearTo}
                  onChange={(e) => setYearTo(e.target.value)}
                  className="w-full"
                />
              </div>
            </div>
            {/* 区域过滤：仅使用通讯作者单位 */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-muted-foreground">区域</label>
              <div className="grid grid-cols-3 gap-1">
                <select
                  value={country}
                  onChange={(e) => {
                    const value = e.target.value
                    setCountry(value); setProvince(""); setCity("")
                    setApplied((prev) => ({ ...prev, country: value, province: "", city: "" }))
                    setPage(1); setSelectedIds(new Set()); setLastSelectedId(null)
                    syncURL({ country: value || undefined, province: undefined, city: undefined, page: "1" })
                  }}
                  className="text-xs border rounded-lg px-1.5 py-1 bg-background"
                >
                  <option value="">国家/地区</option>
                  {(filterOptions?.countries || []).map((option) => (
                    <option key={option.value} value={option.value}>{option.value} ({option.count})</option>
                  ))}
                </select>
                <select
                  value={province}
                  onChange={(e) => {
                    const value = e.target.value
                    setProvince(value); setCity("")
                    setApplied((prev) => ({ ...prev, province: value, city: "" }))
                    setPage(1); setSelectedIds(new Set()); setLastSelectedId(null)
                    syncURL({ province: value || undefined, city: undefined, page: "1" })
                  }}
                  className="text-xs border rounded-lg px-1.5 py-1 bg-background"
                >
                  <option value="">省份</option>
                  {(filterOptions?.provinces || []).map((option) => (
                    <option key={option.value} value={option.value}>{option.value} ({option.count})</option>
                  ))}
                </select>
                <select
                  value={city}
                  onChange={(e) => {
                    const value = e.target.value
                    setCity(value)
                    setApplied((prev) => ({ ...prev, city: value }))
                    setPage(1); setSelectedIds(new Set()); setLastSelectedId(null)
                    syncURL({ city: value || undefined, page: "1" })
                  }}
                  className="text-xs border rounded-lg px-1.5 py-1 bg-background"
                >
                  <option value="">城市</option>
                  {(filterOptions?.cities || []).map((option) => (
                    <option key={option.value} value={option.value}>{option.value} ({option.count})</option>
                  ))}
                </select>
              </div>
            </div>
            {/* 通讯作者单位信息 */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-muted-foreground">通讯作者单位</label>
              <div className="grid grid-cols-1 gap-1">
                <select
                  value={institutionType}
                  onChange={(e) => handleInstitutionTypeChange(e.target.value)}
                  className="text-xs border rounded-lg px-1.5 py-1.5 bg-background"
                >
                  <option value="">单位性质</option>
                  {(filterOptions?.institution_types || []).map((option) => (
                    <option key={option.value} value={option.value}>{option.value} ({option.count})</option>
                  ))}
                </select>
              </div>
            </div>
            {Boolean(filterOptions?.research_topics?.length) && <div className="flex flex-col gap-1.5">
              <label className="text-xs text-muted-foreground">{filterOptions?.classification_catalogs?.dimension_labels?.research_topics || "研究主题"}</label>
              <select
                value={researchTopic}
                onChange={(e) => handleResearchTopicChange(e.target.value)}
                className="w-full text-xs border rounded-lg px-2 py-1.5 bg-background"
              >
                <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.research_topics || "研究主题"}</option>
                <GroupedFilterOptionGroups
                  options={filterOptions?.research_topics || []}
                  groups={filterOptions?.classification_catalogs?.research_topic_groups || []}
                />
              </select>
            </div>}
            {Boolean(filterOptions?.neuroscience_subtopics?.length) && <div className="flex flex-col gap-1.5">
              <label className="text-xs text-muted-foreground">{filterOptions?.classification_catalogs?.dimension_labels?.neuroscience_subtopics || "补充分类"}</label>
              <select
                value={neuroscienceSubtopic}
                onChange={(e) => handleNeuroscienceSubtopicChange(e.target.value)}
                className="w-full text-xs border rounded-lg px-2 py-1.5 bg-background"
              >
                <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.neuroscience_subtopics || "补充分类"}</option>
                {(filterOptions?.neuroscience_subtopics || []).map((option) => (
                  <option key={option.value} value={option.value}>{option.value} ({option.count})</option>
                ))}
              </select>
            </div>}
            {/* 被试/疾病：确定性提取自 population/摘要 */}
            {(Boolean(filterOptions?.diseases?.length || filterOptions?.age_range?.min != null || filterOptions?.age_range?.max != null)) && <div className="flex flex-col gap-1.5">
              {(filterOptions?.diseases?.length ?? 0) > 0 && <label className="text-xs text-muted-foreground">{filterOptions?.classification_catalogs?.dimension_labels?.diseases || "疾病与研究对象"}</label>}
              {(filterOptions?.diseases?.length ?? 0) > 0 && <select
                value={subjectDisease}
                onChange={(e) => setSubjectDisease(e.target.value)}
                className="w-full text-xs border rounded-lg px-2 py-1.5 bg-background"
              >
                <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.diseases || "疾病与研究对象"}</option>
                <GroupedFilterOptionGroups
                  options={filterOptions?.diseases || []}
                  groups={filterOptions?.classification_catalogs?.disease_groups || []}
                />
              </select>}
              <div className="flex items-center gap-1">
                <Input
                  type="number"
                  min={0}
                  max={120}
                  step="0.1"
                  placeholder="最低年龄"
                  value={ageMin}
                  onChange={(e) => setAgeMin(e.target.value)}
                  className="w-full"
                />
                <span className="text-muted-foreground text-xs">—</span>
                <Input
                  type="number"
                  min={0}
                  max={120}
                  step="0.1"
                  placeholder="最高年龄"
                  value={ageMax}
                  onChange={(e) => setAgeMax(e.target.value)}
                  className="w-full"
                />
              </div>
            </div>}
            {/* 研究设计：只显示明确识别的设计与标准化范式（受限用户角色隐藏） */}
            {role !== "limited" && Boolean(filterOptions?.study_designs?.length || filterOptions?.paradigms?.length) && (
            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-muted-foreground">{filterOptions?.classification_catalogs?.dimension_labels?.study_design || "研究设计"}</label>
              <div className="grid grid-cols-2 gap-1">
                {Boolean(filterOptions?.study_designs?.length) && <select
                  value={studyDesign}
                  onChange={(e) => setStudyDesign(e.target.value)}
                  className="text-xs border rounded-lg px-1.5 py-1.5 bg-background"
                >
                  <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.study_design || "研究设计"}</option>
                  <GroupedFilterOptionGroups
                    options={filterOptions?.study_designs || []}
                    groups={filterOptions?.classification_catalogs?.study_design_groups || []}
                  />
                </select>}
                {Boolean(filterOptions?.paradigms?.length) && <select
                  value={paradigmFilter}
                  onChange={(e) => setParadigmFilter(e.target.value)}
                  className="text-xs border rounded-lg px-1.5 py-1.5 bg-background"
                >
                  <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.paradigms || "研究范式"}</option>
                  <GroupedFilterOptionGroups
                    options={filterOptions?.paradigms || []}
                    groups={filterOptions?.classification_catalogs?.paradigm_groups || []}
                  />
                </select>}
              </div>
            </div>
            )}
            {/* 多模态：复用已有 multimodal_techniques 结构化信息 */}
            {Boolean(filterOptions?.multimodal?.length || filterOptions?.multimodal_techniques?.length || filterOptions?.multimodal_timings?.length) && <div className="flex flex-col gap-1.5">
              <label className="text-xs text-muted-foreground">{filterOptions?.classification_catalogs?.dimension_labels?.multimodal || "多模态技术"}</label>
              <div className="grid grid-cols-2 gap-1">
                {Boolean(filterOptions?.multimodal?.length) && <select
                  value={hasMultimodal}
                  onChange={(e) => handleMultimodalChange(e.target.value)}
                  className="text-xs border rounded-lg px-1.5 py-1.5 bg-background"
                >
                  <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.multimodal_presence || "是否联用"}</option>
                  {(filterOptions?.multimodal || []).map((option) => (
                    <option key={option.value} value={option.value}>{option.label} ({option.count})</option>
                  ))}
                </select>}
                {Boolean(filterOptions?.multimodal_techniques?.length) && <select
                  value={multimodalTechnique}
                  onChange={(e) => handleMultimodalTechniqueChange(e.target.value)}
                  disabled={hasMultimodal === "false"}
                  className="text-xs border rounded-lg px-1.5 py-1.5 bg-background"
                >
                  <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.multimodal || "多模态技术"}</option>
                  <GroupedFilterOptionGroups
                    options={filterOptions?.multimodal_techniques || []}
                    groups={filterOptions?.classification_catalogs?.multimodal_method_groups || []}
                  />
                </select>}
                {Boolean(filterOptions?.multimodal_timings?.length) && <select
                  value={multimodalTiming}
                  onChange={(e) => handleMultimodalTimingChange(e.target.value)}
                  disabled={hasMultimodal === "false"}
                  className="col-span-2 text-xs border rounded-lg px-1.5 py-1.5 bg-background"
                >
                  <option value="">{filterOptions?.classification_catalogs?.dimension_labels?.multimodal_timing || "联用方式"}</option>
                  {(filterOptions?.multimodal_timings || []).map((option) => (
                    <option key={option.value} value={option.value}>{option.label} ({option.count})</option>
                  ))}
                </select>}
              </div>
            </div>}
            {allTagCategories.length > 0 && <details className="rounded-lg border p-2">
              <summary className="cursor-pointer text-xs text-muted-foreground">标签</summary>
              <div className="mt-2 flex flex-col gap-1.5">
              {allTagCategories.map((cat) => (
                <div key={cat.name} className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-muted-foreground/60">{cat.name}</span>
                  <div className="flex flex-wrap gap-1">
                    {cat.tags.map((t) => (
                      <button key={t.name} onClick={() => {
                        const next = tag === t.name ? "" : t.name
                        setTag(next)
                        setPage(1)
                        setSelectedIds(new Set())
                        setLastSelectedId(null)
                        syncURL({ tag: next || undefined, page: "1" })
                        setApplied((prev) => ({ ...prev, tag: next }))
                      }}
                      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium transition-colors ${tag === t.name ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:bg-muted/80"}`}>
                        {t.name}
                        <span className="ml-0.5 text-[9px] opacity-60">{t.count}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              {allTagCategories.length === 0 && <span className="text-[11px] text-muted-foreground">暂无标签</span>}
              </div>
            </details>}
          </div>
        </div>
        <div data-bottom-bar className="shrink-0 grid grid-cols-2 gap-2 border-t bg-sidebar px-3 py-2 pb-[calc(0.5rem+env(safe-area-inset-bottom,0px))]">
          <Button size="sm" className="w-full" onClick={onApply}>
            搜索
          </Button>
          <Button size="sm" variant="outline" className="w-full" onClick={onReset}>
            重置
          </Button>
        </div>
      </aside>
  )
}
