import { useCallback, useState } from "react"
import { usePathname, useSearchParams } from "next/navigation"
import { AppliedPaperFilters } from "../lib/paper-types"

const DEFAULT_TARGET_LABEL = process.env.NEXT_PUBLIC_TARGET_LABEL || "qMRLab"
const SORTABLE_FIELDS = new Set([
  "id", "internal_id", "title", "journal_name", "year", "impact_factor",
  "cas_quartile", "jcr_quartile", "cites_target", "updated_at", "entity_label",
  "review_outcome", "metadata_complete",
])

export function usePaperFilters() {
  const searchParams = useSearchParams()
  const pathname = usePathname()

  function readParam(key: string, fallback: string): string {
    return searchParams?.get(key) ?? fallback
  }

  // "all" is a UI-only sentinel so a missing/empty URL value can keep the
  // default approved view while an explicit "全部" selection stays empty.
  function readReviewOutcome(): string {
    const value = readParam("review_outcome", "approved")
    return value === "all" ? "" : value
  }

  const syncURL = useCallback(
    (updates: Record<string, string | undefined>) => {
      const sp = new URLSearchParams(searchParams?.toString() ?? "")
      sp.delete("is")
      for (const [k, v] of Object.entries(updates)) {
        if (v === undefined || v === "") sp.delete(k)
        else sp.set(k, v)
      }
      const qs = sp.toString()
      window.history.replaceState(null, "", qs ? `${pathname}?${qs}` : pathname)
    },
    [searchParams, pathname],
  )

  // Initialize from URL (or defaults)
  const [q, setQ] = useState(() => readParam("q", ""))
  const [entityLabel, setEntityLabel] = useState(() => readParam("entity_label", DEFAULT_TARGET_LABEL))
  const [journal, setJournal] = useState(() => readParam("journal", ""))
  const [yearFrom, setYearFrom] = useState(() => readParam("year_from", ""))
  const [yearTo, setYearTo] = useState(() => readParam("year_to", ""))
  const [country, setCountry] = useState(() => readParam("country", ""))
  const [province, setProvince] = useState(() => readParam("province", ""))
  const [city, setCity] = useState(() => readParam("city", ""))
  const [institutionType, setInstitutionType] = useState(() => readParam("institution_type", ""))
  const [affiliation, setAffiliation] = useState(() => readParam("affiliation", ""))
  const [researchField, setResearchField] = useState(() => readParam("rf", ""))
  const [researchTopic, setResearchTopic] = useState(() => readParam("topic", ""))
  const [neuroscienceSubtopic, setNeuroscienceSubtopic] = useState(() => readParam("neuro", ""))
  const [tag, setTag] = useState(() => readParam("tag", ""))
  const [subjectDisease, setSubjectDisease] = useState(() => readParam("disease", ""))
  const [ageMin, setAgeMin] = useState(() => readParam("age_min", ""))
  const [ageMax, setAgeMax] = useState(() => readParam("age_max", ""))
  const [studyDesign, setStudyDesign] = useState(() => readParam("design", ""))
  const [paradigmFilter, setParadigmFilter] = useState(() => readParam("paradigm", ""))
  const [hasMultimodal, setHasMultimodal] = useState(() => readParam("multimodal", ""))
  const [multimodalTechnique, setMultimodalTechnique] = useState(() => readParam("technique", ""))
  const [multimodalTiming, setMultimodalTiming] = useState(() => readParam("timing", ""))
  const [reviewOutcome, setReviewOutcome] = useState(readReviewOutcome)
  const [page, setPage] = useState(() => parseInt(readParam("page", "1"), 10))
  const [sortBy, setSortBy] = useState(() => {
    const value = readParam("sort", "internal_id")
    return SORTABLE_FIELDS.has(value) ? value : "internal_id"
  })
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">(
    () => (readParam("order", "desc") as "asc" | "desc") || "desc",
  )

  // Applied (submitted) filter values — init from URL too
  const [applied, setApplied] = useState<AppliedPaperFilters>(() => ({
    q: readParam("q", ""),
    entity_label: readParam("entity_label", DEFAULT_TARGET_LABEL),
    journal: readParam("journal", ""),
    yearFrom: readParam("year_from", ""),
    yearTo: readParam("year_to", ""),
    country: readParam("country", ""),
    province: readParam("province", ""),
    city: readParam("city", ""),
    institutionType: readParam("institution_type", ""),
    affiliation: readParam("affiliation", ""),
    researchField: readParam("rf", ""),
    researchTopic: readParam("topic", ""),
    neuroscienceSubtopic: readParam("neuro", ""),
    tag: readParam("tag", ""),
    subjectDisease: readParam("disease", ""),
    ageMin: readParam("age_min", ""),
    ageMax: readParam("age_max", ""),
    studyDesign: readParam("design", ""),
    paradigmFilter: readParam("paradigm", ""),
    hasMultimodal: readParam("multimodal", ""),
    multimodalTechnique: readParam("technique", ""),
    multimodalTiming: readParam("timing", ""),
    reviewOutcome: readReviewOutcome(),
  }))

  return {
    readParam,
    syncURL,
    searchParams,
    pathname,
    q,
    setQ,
    entityLabel,
    setEntityLabel,
    journal,
    setJournal,
    yearFrom,
    setYearFrom,
    yearTo,
    setYearTo,
    country,
    setCountry,
    province,
    setProvince,
    city,
    setCity,
    institutionType,
    setInstitutionType,
    affiliation,
    setAffiliation,
    researchField,
    setResearchField,
    researchTopic,
    setResearchTopic,
    neuroscienceSubtopic,
    setNeuroscienceSubtopic,
    tag,
    setTag,
    subjectDisease,
    setSubjectDisease,
    ageMin,
    setAgeMin,
    ageMax,
    setAgeMax,
    studyDesign,
    setStudyDesign,
    paradigmFilter,
    setParadigmFilter,
    hasMultimodal,
    setHasMultimodal,
    multimodalTechnique,
    setMultimodalTechnique,
    multimodalTiming,
    setMultimodalTiming,
    reviewOutcome,
    setReviewOutcome,
    page,
    setPage,
    sortBy,
    setSortBy,
    sortOrder,
    setSortOrder,
    applied,
    setApplied,
  }
}
