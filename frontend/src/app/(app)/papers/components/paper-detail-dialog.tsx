"use client"

import { useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import type { ClassificationOptionGroup, FilterOption, PaperDetail } from "../lib/paper-types"
import { fieldVal, safeArrayStr } from "../lib/paper-format"
import { Check, ChevronRight, Plus, Search, X } from "lucide-react"

const TARGET_LABEL = process.env.NEXT_PUBLIC_TARGET_LABEL || "qMRLab"
type MultimodalTiming = string
type MultimodalRelation = { method: string; timing: MultimodalTiming }
const multimodalTimingLabel = (value: string | undefined, options: { value: string; label: string }[]) =>
  options.find((option) => option.value === value)?.label || value || "未判断"

export function ClassificationPickerField({
  label,
  values,
  options,
  onChange,
  multiple = true,
  disabled = false,
  emptyHint = "请选择",
  allowCustom = true,
  optionGroups = [],
}: {
  label: string
  values: string[]
  options: string[]
  onChange: (values: string[]) => void
  multiple?: boolean
  disabled?: boolean
  emptyHint?: string
  allowCustom?: boolean
  optionGroups?: ClassificationOptionGroup[]
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const allOptions = useMemo(
    () => {
      const grouped = optionGroups.flatMap((group) => group.options)
      const combined = Array.from(new Set([...grouped, ...options, ...values]))
      return optionGroups.length ? combined : combined.sort((a, b) => a.localeCompare(b, "zh-CN"))
    },
    [optionGroups, options, values],
  )
  const filteredOptions = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase()
    return keyword ? allOptions.filter((item) => item.toLocaleLowerCase().includes(keyword)) : allOptions
  }, [allOptions, query])
  const canAddCustom = allowCustom && Boolean(query.trim()) && !allOptions.some(
    (item) => item.toLocaleLowerCase() === query.trim().toLocaleLowerCase(),
  )
  const visibleGroups = useMemo(() => {
    if (!optionGroups.length) return []
    const included = new Set<string>()
    const groups = optionGroups.map((group) => {
      const groupOptions = filteredOptions.filter((option) => group.options.includes(option))
      groupOptions.forEach((option) => included.add(option))
      return { ...group, options: groupOptions }
    }).filter((group) => group.options.length)
    const ungrouped = filteredOptions.filter((option) => !included.has(option))
    if (ungrouped.length) groups.push({ label: "当前自定义", options: ungrouped })
    return groups
  }, [filteredOptions, optionGroups])

  const toggle = (value: string) => {
    if (multiple) {
      onChange(values.includes(value) ? values.filter((item) => item !== value) : [...values, value])
    } else {
      onChange(values.includes(value) ? [] : [value])
      setOpen(false)
    }
  }
  const addCustom = () => {
    const value = query.trim()
    if (!value) return
    onChange(multiple ? Array.from(new Set([...values, value])) : [value])
    setQuery("")
    if (!multiple) setOpen(false)
  }

  return (
    <Dialog open={open} onOpenChange={(next) => {
      setOpen(next)
      if (!next) setQuery("")
    }}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        className="group min-h-12 w-full rounded-lg border bg-background px-3 py-2 text-left transition-colors hover:border-primary/40 hover:bg-muted/30 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
          <span>{label}</span>
          <span className="inline-flex items-center gap-0.5">{values.length ? `${values.length} 项` : ""}<ChevronRight className="h-3.5 w-3.5" /></span>
        </span>
        <span className="mt-1 flex min-w-0 flex-wrap gap-1">
          {values.length ? values.slice(0, 3).map((value) => (
            <span key={value} className="max-w-full truncate rounded-md bg-primary/10 px-1.5 py-0.5 text-[11px] text-primary">{value}</span>
          )) : <span className="text-xs text-muted-foreground/60">{disabled ? emptyHint : "点击选择或新增"}</span>}
          {values.length > 3 && <span className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">+{values.length - 3}</span>}
        </span>
      </button>

      <DialogContent
        showCloseButton={false}
        className="top-auto bottom-0 max-h-[86dvh] w-full max-w-none translate-y-0 grid-rows-[auto_auto_minmax(0,1fr)_auto] gap-3 overflow-hidden overscroll-contain rounded-b-none p-0 sm:top-1/2 sm:bottom-auto sm:max-h-[72vh] sm:max-w-lg sm:-translate-y-1/2 sm:rounded-xl"
      >
        <DialogHeader className="border-b px-4 pb-3 pt-4">
          <DialogTitle className="flex items-center justify-between pr-0">
            <span>{label}</span>
            <span className="text-xs font-normal text-muted-foreground">{multiple ? `已选 ${values.length} 项` : "单选"}</span>
          </DialogTitle>
          <DialogDescription className="text-xs">{allowCustom ? "搜索已有分类，或在下方新增自定义分类。" : "从规范分类中选择。"}</DialogDescription>
        </DialogHeader>

        <div className="px-4">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => {
              if (event.key === "Enter" && canAddCustom) { event.preventDefault(); addCustom() }
            }} placeholder={allowCustom ? `搜索${label}，也可输入新分类` : `搜索${label}`} className="h-10 pl-9" />
          </div>
        </div>

        <div className="min-h-0 overflow-y-auto overscroll-contain px-4 scrollbar-none">
          {(visibleGroups.length ? visibleGroups : [{ label: "", options: filteredOptions }]).map((group) => (
            <div key={group.label || "all"} className="mb-3 last:mb-0">
              {group.label && <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">{group.label}</p>}
              <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                {group.options.map((option) => {
              const selected = values.includes(option)
              return (
                <button
                  key={option}
                  type="button"
                  onClick={() => toggle(option)}
                  className={`flex min-h-11 items-center justify-between gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-colors ${selected ? "border-primary bg-primary/10 text-primary" : "bg-background hover:bg-muted"}`}
                >
                  <span className="min-w-0 break-words">{option}</span>
                  <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border ${selected ? "border-primary bg-primary text-primary-foreground" : "border-muted-foreground/30"}`}>
                    {selected && <Check className="h-3.5 w-3.5" />}
                  </span>
                </button>
              )
                })}
              </div>
            </div>
          ))}
          {filteredOptions.length === 0 && <p className="py-8 text-center text-xs text-muted-foreground">没有匹配的已有分类，可在下方新增</p>}
        </div>

        <div data-bottom-bar className="grid grid-cols-2 gap-2 border-t bg-muted/20 px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3">
          <Button
            type="button"
            variant="outline"
            className="h-10 min-w-0"
            onClick={() => canAddCustom ? addCustom() : onChange([])}
            disabled={!canAddCustom && values.length === 0}
          >
            {canAddCustom ? <Plus className="h-4 w-4" /> : <X className="h-4 w-4" />}
            <span className="truncate">{canAddCustom ? `新增“${query.trim()}”` : "清空已选"}</span>
          </Button>
          <Button type="button" className="h-10" onClick={() => setOpen(false)}>完成</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export interface ClassificationEditorOptions {
  labels: Record<string, string>
  researchTopics: string[]
  researchTopicGroups: ClassificationOptionGroup[]
  neuroscienceSubtopics: string[]
  diseases: string[]
  diseaseGroups: ClassificationOptionGroup[]
  studyDesigns: string[]
  studyDesignGroups: ClassificationOptionGroup[]
  paradigms: string[]
  paradigmGroups: ClassificationOptionGroup[]
  multimodalMethods: string[]
  multimodalMethodGroups: ClassificationOptionGroup[]
  multimodalStates: FilterOption[]
  multimodalTimings: { value: string; label: string }[]
}

export function StructuredClassificationEditor({
  paper,
  classificationOptions,
  editable,
  saving,
  onSave,
  embeddedMobile = false,
}: {
  paper: PaperDetail
  classificationOptions: ClassificationEditorOptions
  editable: boolean
  saving: boolean
  onSave: (data: Record<string, unknown>) => void
  embeddedMobile?: boolean
}) {
  const parseValues = (value: unknown): string[] => {
    if (Array.isArray(value)) return value.map(String)
    if (typeof value === "string") {
      try { const parsed = JSON.parse(value); return Array.isArray(parsed) ? parsed.map(String) : [value] }
      catch { return value ? [value] : [] }
    }
    return value != null ? [String(value)] : []
  }
  const [subjectDiseases, setSubjectDiseases] = useState<string[]>(
    parseValues(paper.subject_diseases).length ? parseValues(paper.subject_diseases) : parseValues(paper.subject_disease),
  )
  const [researchTopics, setResearchTopics] = useState<string[]>(parseValues(paper.research_topics))
  const [neuroscienceSubtopics, setNeuroscienceSubtopics] = useState<string[]>(parseValues(paper.neuroscience_subtopics))
  const [paradigms, setParadigms] = useState<string[]>(
    parseValues(paper.paradigms).length ? parseValues(paper.paradigms) : parseValues(paper.paradigm),
  )
  const [multimodalMethods, setMultimodalMethods] = useState<string[]>(parseValues(paper.multimodal_methods))
  const [multimodalRelations, setMultimodalRelations] = useState<MultimodalRelation[]>(
    Array.isArray(paper.multimodal_relations)
      ? paper.multimodal_relations.filter((relation): relation is MultimodalRelation =>
          Boolean(relation?.method && relation?.timing),
        )
      : [],
  )
  const [studyDesign, setStudyDesign] = useState(paper.study_design || "")
  const [usesMultimodal, setUsesMultimodal] = useState(
    paper.uses_multimodal === true ? "true" : paper.uses_multimodal === false ? "false" : "",
  )

  const save = () => onSave({
    subject_diseases: subjectDiseases,
    research_topics: researchTopics,
    neuroscience_subtopics: neuroscienceSubtopics,
    study_design: studyDesign || null,
    paradigms,
    uses_multimodal: usesMultimodal === "true" ? true : usesMultimodal === "false" ? false : null,
    multimodal_methods: usesMultimodal === "false" ? [] : multimodalMethods,
    multimodal_relations: usesMultimodal === "false" ? [] : multimodalRelations,
  })

  const setMethodTiming = (method: string, timing: string) => {
    setMultimodalRelations((current) => {
      const withoutMethod = current.filter((relation) => relation.method !== method)
      return timing
        ? [...withoutMethod, { method, timing: timing as MultimodalTiming }]
        : withoutMethod
    })
  }

  const readOnlyItems = [
    [classificationOptions.labels.research_topics || "研究主题", researchTopics],
    [classificationOptions.labels.neuroscience_subtopics || "补充分类", neuroscienceSubtopics],
    [classificationOptions.labels.diseases || "疾病与研究对象", subjectDiseases],
    [classificationOptions.labels.study_design || "研究设计", studyDesign ? [studyDesign] : []],
    [classificationOptions.labels.paradigms || "研究范式", paradigms],
    [classificationOptions.labels.multimodal || "多模态技术", multimodalMethods.map((method) => {
      const relation = multimodalRelations.find((item) => item.method === method)
      return `${method} · ${multimodalTimingLabel(relation?.timing, classificationOptions.multimodalTimings)}`
    })],
  ] as const

  return (
    <section className={embeddedMobile ? "space-y-3" : "space-y-3 rounded-xl border bg-card p-3 sm:p-4"}>
      {!embeddedMobile && (
        <div className="flex items-start justify-between gap-3">
          <p className="text-xs font-semibold text-muted-foreground">结构化分类</p>
          {editable && <Button type="button" size="sm" className="hidden lg:inline-flex" onClick={save} disabled={saving}>{saving ? "保存中…" : "保存分类"}</Button>}
        </div>
      )}

      {editable ? (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {(classificationOptions.researchTopics.length > 0 || researchTopics.length > 0) && (
            <ClassificationPickerField label={classificationOptions.labels.research_topics || "研究主题"} values={researchTopics} options={classificationOptions.researchTopics} optionGroups={classificationOptions.researchTopicGroups} onChange={setResearchTopics} />
          )}
          {(classificationOptions.neuroscienceSubtopics.length > 0 || neuroscienceSubtopics.length > 0) && (
            <ClassificationPickerField
              label={classificationOptions.labels.neuroscience_subtopics || "补充分类"}
              values={neuroscienceSubtopics}
              options={classificationOptions.neuroscienceSubtopics}
              onChange={setNeuroscienceSubtopics}
            />
          )}
          {(classificationOptions.diseases.length > 0 || subjectDiseases.length > 0) && (
            <ClassificationPickerField label={classificationOptions.labels.diseases || "疾病与研究对象"} values={subjectDiseases} options={classificationOptions.diseases} optionGroups={classificationOptions.diseaseGroups} onChange={setSubjectDiseases} />
          )}
          {(classificationOptions.studyDesigns.length > 0 || studyDesign) && (
            <ClassificationPickerField label={classificationOptions.labels.study_design || "研究设计"} values={studyDesign ? [studyDesign] : []} options={classificationOptions.studyDesigns} optionGroups={classificationOptions.studyDesignGroups} onChange={(values) => setStudyDesign(values[0] || "")} multiple={false} />
          )}
          {(classificationOptions.paradigms.length > 0 || paradigms.length > 0) && (
            <ClassificationPickerField
              label={classificationOptions.labels.paradigms || "研究范式"}
              values={paradigms}
              options={classificationOptions.paradigms}
              optionGroups={classificationOptions.paradigmGroups}
              onChange={setParadigms}
            />
          )}
          {(classificationOptions.multimodalMethods.length > 0 || multimodalMethods.length > 0 || usesMultimodal !== "") && <div className="min-h-12 rounded-lg border bg-background px-3 py-2">
            <span className="text-[11px] text-muted-foreground">{classificationOptions.labels.multimodal_presence || "是否联用"}</span>
            <div className="mt-1 flex gap-1 rounded-lg bg-muted p-1">
              {classificationOptions.multimodalStates.map((option) => (
                <button key={option.value} type="button" onClick={() => {
                  setUsesMultimodal(option.value)
                  if (option.value === "false") {
                    setMultimodalMethods([])
                    setMultimodalRelations([])
                  }
                }} className={`min-h-8 flex-1 rounded-md px-2 text-xs transition-colors ${usesMultimodal === option.value ? "bg-background font-medium text-primary shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
                  {option.label}
                </button>
              ))}
              <button type="button" onClick={() => setUsesMultimodal("")} className="min-h-8 rounded-md px-2 text-xs text-muted-foreground hover:text-foreground">清除</button>
            </div>
          </div>}
          {(classificationOptions.multimodalMethods.length > 0 || multimodalMethods.length > 0) && (
            <ClassificationPickerField label={classificationOptions.labels.multimodal || "多模态技术"} values={multimodalMethods} options={classificationOptions.multimodalMethods} optionGroups={classificationOptions.multimodalMethodGroups}
              disabled={usesMultimodal === "false"} emptyHint="已选择不联用" onChange={(values) => {
                setMultimodalMethods(values)
                setMultimodalRelations((current) => current.filter((relation) => values.includes(relation.method)))
                if (values.length) setUsesMultimodal("true")
              }} />
          )}
          {usesMultimodal !== "false" && multimodalMethods.length > 0 && (
            <div className="space-y-2 rounded-lg border bg-background px-3 py-2 sm:col-span-2">
              <span className="text-[11px] text-muted-foreground">联用方式</span>
              <div className="grid gap-2 sm:grid-cols-2">
                {multimodalMethods.map((method) => {
                  const timing = multimodalRelations.find((relation) => relation.method === method)?.timing || ""
                  const timingOptions = timing && !classificationOptions.multimodalTimings.some((option) => option.value === timing)
                    ? [{ value: timing, label: timing }, ...classificationOptions.multimodalTimings]
                    : classificationOptions.multimodalTimings
                  return (
                    <label key={method} className="grid grid-cols-[minmax(5rem,auto)_1fr] items-center gap-2 text-xs">
                      <span className="truncate font-medium" title={method}>{method}</span>
                      <select
                        value={timing}
                        onChange={(event) => setMethodTiming(method, event.target.value)}
                        className="h-9 min-w-0 rounded-lg border bg-background px-2 text-xs"
                      >
                        <option value="">未判断</option>
                        {timingOptions.map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    </label>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-2 text-xs">
          {readOnlyItems.map(([label, values]) => values.length ? (
            <div key={label} className="flex gap-2"><span className="w-20 shrink-0 text-muted-foreground">{label}</span><span>{values.join(" / ")}</span></div>
          ) : null)}
          {(classificationOptions.multimodalMethods.length > 0 || multimodalMethods.length > 0 || usesMultimodal !== "") && (
            <div className="flex gap-2"><span className="w-20 shrink-0 text-muted-foreground">{classificationOptions.labels.multimodal_presence || "是否联用"}</span><span>{classificationOptions.multimodalStates.find((option) => option.value === usesMultimodal)?.label || "未判断"}</span></div>
          )}
        </div>
      )}
      {editable && (
        <div className="flex justify-end border-t pt-3 lg:hidden">
          <Button type="button" size="sm" onClick={save} disabled={saving}>{saving ? "保存中…" : "保存分类"}</Button>
        </div>
      )}
    </section>
  )
}

// ─── Simple inline editable fields (key prop handles paper switching) ───
export function EditableDetailFormV2({
  paper,
  onSave,
  saving,
  onDelete,
  embeddedMobile = false,
}: {
  paper: PaperDetail
  onSave: (data: Record<string, unknown>) => void
  saving: boolean
  onDelete?: () => void
  embeddedMobile?: boolean
}) {
  const safeParse = (v: unknown): string[] => {
    if (Array.isArray(v)) return v.map(String)
    if (typeof v === "string") { try { const p = JSON.parse(v); return Array.isArray(p) ? p.map(String) : [v] } catch { return [v] } }
    return v != null ? [String(v)] : []
  }


  // Derive form state from paper (rebuilds on paper change via key prop)
  const buildForm = (): Record<string, string> => {
    const f: Record<string, string> = {}
    for (const k of ["title_zh", "journal_name", "entity_label", "corresponding_region", "device_citation_text", "citation_error_type", "notes"])
      f[k] = fieldVal(paper, k)
    f["research_fields"] = safeParse(paper.research_fields).join(" / ")
    f["paradigm"] = safeParse(paper.paradigm).join(" / ")
    f["equipment_brand"] = safeParse(paper.equipment_brand).join(", ")
    f["channel_count"] = paper.channel_count != null ? String(paper.channel_count) : ""
    f["uses_target_software"] = paper.uses_target_software === true ? "是" : paper.uses_target_software === false ? "否" : ""
    f["sample_size"] = paper.sample_size != null ? String(paper.sample_size) : ""
    f["tags"] = safeParse(paper.tags).join(", ")
    return f
  }
  const [form, setForm] = useState<Record<string, string>>(buildForm)
  type EditableAuthor = {
    author_id?: number
    author_order?: number
    name_en: string
    name_zh?: string
    affiliation_en?: string
    affiliation_zh?: string
    email?: string
    orcid?: string
    is_first: boolean
    is_corresponding: boolean
  }
  const [roleAuthors, setRoleAuthors] = useState<EditableAuthor[]>(() =>
    (paper.authors || []).filter((author) => author.is_first || author.is_corresponding).map((author) => ({
      ...author,
      name_en: author.name_en || author.name || "",
      name_zh: author.name_zh || "",
      affiliation_en: author.affiliation_en || "",
      affiliation_zh: author.affiliation_zh || "",
      is_first: Boolean(author.is_first),
      is_corresponding: Boolean(author.is_corresponding),
    })),
  )
  const [authorsDirty, setAuthorsDirty] = useState(false)
  const updateAuthor = (index: number, changes: Partial<EditableAuthor>) => {
    setAuthorsDirty(true)
    setRoleAuthors((current) => current.map((author, i) => i === index ? { ...author, ...changes } : author))
  }
  const hasUnresolvedRoleAuthor = roleAuthors.some((author) => !author.name_en.trim())
  const setF = (k: string, v: string) => setForm((prev) => ({ ...prev, [k]: v }))
  const row = (label: string, key: string, ph = "", wide = false) => (
    <div className={wide ? "sm:col-span-2" : ""}>
      <label className="mb-1 block text-[11px] text-muted-foreground">{label}</label>
      <input className="h-9 w-full min-w-0 rounded-lg border bg-background px-2 text-xs"
        value={form[key] ?? ""} onChange={(e) => setF(key, e.target.value)} placeholder={ph} />
    </div>
  )
  const targetSoftwareChoice = (
    <div>
      <label className="mb-1 block text-[11px] text-muted-foreground">是否使用目标软件</label>
      <div className="grid h-9 grid-cols-2 overflow-hidden rounded-lg border bg-background" role="group" aria-label="是否使用目标软件">
        {(["是", "否"] as const).map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={form["uses_target_software"] === option}
            className={`text-xs transition-colors first:border-r ${form["uses_target_software"] === option ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}
            onClick={() => setF("uses_target_software", option)}
          >
            {option}
          </button>
        ))}
      </div>
    </div>
  )

  function save() {
    const d: Record<string, unknown> = {}
    for (const k of ["title_zh", "journal_name", "entity_label", "corresponding_region", "device_citation_text", "citation_error_type", "notes"]) {
      d[k] = form[k]?.trim() || null
    }
    const eq = form["equipment_brand"]?.trim(); d["equipment_brand"] = eq ? eq.split(",").map((s) => s.trim()).filter(Boolean) : null
    const chRaw = form["channel_count"]?.trim(); d["channel_count"] = chRaw !== "" ? (isNaN(parseInt(chRaw)) ? null : parseInt(chRaw)) : null
    const ssRaw = form["sample_size"]?.trim(); d["sample_size"] = ssRaw !== "" ? (isNaN(parseInt(ssRaw)) ? null : parseInt(ssRaw)) : null
    if (form["uses_target_software"] === "是") d["uses_target_software"] = true; else if (form["uses_target_software"] === "否") d["uses_target_software"] = false; else d["uses_target_software"] = null
    const serializedAuthors = roleAuthors
      .filter((author) => author.name_en.trim())
      .map((author) => ({
        ...author,
        name: author.name_en.trim(),
        name_en: author.name_en.trim(),
        name_zh: author.name_zh?.trim() || undefined,
        affiliation_en: author.affiliation_en?.trim() || undefined,
        affiliation_zh: author.affiliation_zh?.trim() || undefined,
        email: author.email?.trim() || undefined,
        orcid: author.orcid?.trim() || undefined,
      }))
    // Do not clear unresolved legacy role links when saving unrelated paper
    // fields. Once the operator supplies/removes every missing English name,
    // the complete role projection can be synchronized safely.
    if (authorsDirty && !hasUnresolvedRoleAuthor) {
      d["first_authors"] = serializedAuthors.filter((author) => author.is_first)
      d["corresponding_authors"] = serializedAuthors.filter((author) => author.is_corresponding)
    }
    onSave(d)
  }

  const actionButtons = (
    <div data-bottom-bar={!embeddedMobile ? "" : undefined} className={embeddedMobile ? "flex flex-wrap gap-2" : "sticky bottom-0 z-10 flex flex-wrap gap-2 bg-card/95 py-1 backdrop-blur supports-backdrop-filter:bg-card/85"}>
      <Button size="sm" onClick={save} disabled={saving}>{saving ? "保存中..." : "保存修改"}</Button>
      {onDelete && <Button size="sm" variant="outline" onClick={onDelete}>删除</Button>}
    </div>
  )

  return (
    <div className={embeddedMobile
      ? "min-w-0 space-y-3 pt-3"
      : "min-w-0 space-y-3 overflow-hidden rounded-xl border bg-card p-3 sm:p-4"}>
      {!embeddedMobile && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs font-semibold text-muted-foreground">编辑论文信息</p>
          {actionButtons}
        </div>
      )}
      {row("中文标题", "title_zh", "", true)}

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <p className="text-[10px] font-semibold text-muted-foreground">第一/共一、通讯/共通讯作者（按论文署名顺序）</p>
          <button type="button" className="flex h-8 items-center gap-1 rounded-lg border px-2 text-xs hover:bg-muted"
            onClick={() => {
              setAuthorsDirty(true)
              setRoleAuthors((current) => [...current, { name_en: "", name_zh: "", affiliation_en: "", affiliation_zh: "", is_first: false, is_corresponding: false }])
            }}>
            <Plus className="h-3.5 w-3.5" />添加角色作者
          </button>
        </div>
        {hasUnresolvedRoleAuthor && (
          <p className="rounded-md bg-amber-50 px-2 py-1.5 text-[10px] text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
            存在缺少可信英文署名的历史角色作者；补全或移除后才会保存作者变更，其他论文字段仍可正常保存。
          </p>
        )}
        {roleAuthors.map((author, index) => (
          <div key={author.author_id ? `author-${author.author_id}` : `new-author-${index}`} className="space-y-1 rounded-lg border p-2">
            <div className="flex items-center gap-1">
              <span className="w-6 text-center text-[10px] text-muted-foreground">{author.author_order != null ? author.author_order + 1 : "+"}</span>
              <input className="h-8 min-w-0 flex-1 rounded-md border px-2 text-xs" placeholder="英文署名"
                value={author.name_en} onChange={(e) => updateAuthor(index, { name_en: e.target.value })} />
              <input className="h-8 min-w-0 flex-1 rounded-md border px-2 text-xs" placeholder="中文名（可选）"
                value={author.name_zh || ""} onChange={(e) => updateAuthor(index, { name_zh: e.target.value })} />
              <button type="button" className="h-8 w-8 rounded-md border text-destructive"
                onClick={() => {
                  setAuthorsDirty(true)
                  setRoleAuthors((current) => current.filter((_, i) => i !== index))
                }} aria-label="移除角色作者"><X className="mx-auto h-3.5 w-3.5" /></button>
            </div>
            <div className="flex min-w-0 items-center gap-2 pl-7">
              <input className="h-8 min-w-0 flex-1 rounded-md border px-2 text-xs" placeholder="英文单位（可选）"
                value={author.affiliation_en || ""} onChange={(e) => updateAuthor(index, { affiliation_en: e.target.value })} />
              <input className="h-8 min-w-0 flex-1 rounded-md border px-2 text-xs" placeholder="中文单位（可选）"
                value={author.affiliation_zh || ""} onChange={(e) => updateAuthor(index, { affiliation_zh: e.target.value })} />
              <label className="flex shrink-0 items-center gap-1 text-[10px]"><input type="checkbox" checked={author.is_first}
                onChange={(e) => updateAuthor(index, { is_first: e.target.checked })} />第一/共一</label>
              <label className="flex shrink-0 items-center gap-1 text-[10px]"><input type="checkbox" checked={author.is_corresponding}
                onChange={(e) => updateAuthor(index, { is_corresponding: e.target.checked })} />通讯</label>
            </div>
          </div>
        ))}
      </div>

      <div className="grid min-w-0 grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2">
        {row("课题", "entity_label", TARGET_LABEL)}
        {row("使用设备", "equipment_brand", "厂商或产品名称")}
        {row("通道数", "channel_count")}
        {row("通讯作者单位地区", "corresponding_region")}
        {row("样本量", "sample_size")}
        {targetSoftwareChoice}
        {row("引用错误类型", "citation_error_type")}
        {row("设备引用信息", "device_citation_text")}
        {row("备注", "notes", "", true)}
      </div>
      {(paper.research_fields?.length || paper.tags?.length) ? (
        <details className="rounded border px-2 py-1 text-[11px] text-muted-foreground">
          <summary className="cursor-pointer font-medium">历史分类信息（只读）</summary>
          {paper.research_fields?.length ? <p>原归属类别：{safeArrayStr(paper.research_fields)}</p> : null}
          {paper.tags?.length ? <p>原标签：{safeArrayStr(paper.tags)}</p> : null}
        </details>
      ) : null}
      {embeddedMobile && (
        <div data-section-bar className="sticky bottom-0 z-10 flex justify-end border-t bg-card/95 pt-3 pb-2 backdrop-blur supports-backdrop-filter:bg-card/85">
          {actionButtons}
        </div>
      )}
    </div>
  )
}



