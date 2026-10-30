import type { Paper } from "@/lib/types"

export interface AppliedPaperFilters {
  q: string
  entity_label: string
  journal: string
  yearFrom: string
  yearTo: string
  country: string
  province: string
  city: string
  institutionType: string
  affiliation: string
  researchField: string
  researchTopic: string
  neuroscienceSubtopic: string
  tag: string
  subjectDisease: string
  ageMin: string
  ageMax: string
  studyDesign: string
  paradigmFilter: string
  hasMultimodal: string
  multimodalTechnique: string
  multimodalTiming: string
  reviewOutcome: string
}

export interface LiteratureSearchTask {
  task_id: string
  job_id?: string
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled"
  stage: string
  request: {
    mode: "recent" | "range" | "years"
    days?: number
    date_from?: string | null
    date_to?: string | null
    entity_key?: string | null
    year_from?: number | null
    year_to?: number | null
    dry_run: boolean
    translate_zh: boolean
  }
  logs: string[]
  report: Record<string, number>
  error?: string | null
  created_at?: string
  started_at?: string | null
  finished_at?: string | null
}

export interface PapersResponse {
  items: Paper[]
  total: number
  page: number
  page_size: number
}

export interface FilterOption {
  value: string
  count: number
  label?: string
}

export interface ClassificationOptionGroup {
  label: string
  options: string[]
}

export interface PaperFilterOptions {
  review_outcomes: FilterOption[]
  countries: FilterOption[]
  provinces: FilterOption[]
  cities: FilterOption[]
  institution_types: FilterOption[]
  diseases: FilterOption[]
  research_topics: FilterOption[]
  neuroscience_subtopics: FilterOption[]
  age_range: { min: number | null; max: number | null }
  study_designs: FilterOption[]
  paradigms: FilterOption[]
  multimodal: (FilterOption & { label: string })[]
  multimodal_techniques: FilterOption[]
  multimodal_timings: (FilterOption & { label: string })[]
  departments: FilterOption[]
  tag_categories: { name: string; tags: { name: string; count: number }[] }[]
  classification_catalogs: {
    research_topics: string[]
    research_topic_groups: ClassificationOptionGroup[]
    neuroscience_subtopics: string[]
    neuroscience_subtopic_groups: ClassificationOptionGroup[]
    diseases: string[]
    disease_groups: ClassificationOptionGroup[]
    study_designs: string[]
    study_design_groups: ClassificationOptionGroup[]
    paradigms: string[]
    paradigm_groups: ClassificationOptionGroup[]
    multimodal_methods: string[]
    multimodal_method_groups: ClassificationOptionGroup[]
    multimodal_presence: string[]
    tags: string[]
    tag_groups: ClassificationOptionGroup[]
    dimension_labels: Record<string, string>
  }
}

export interface ConfigTaxonomyItem {
  key: string
  value?: string
  label: string
  group?: string
  aliases?: string[]
  patterns?: string[]
}

export interface ConfigOptions {
  themes: { value: string; label: string }[]
  models: { value: string; label: string }[]
  multimodal_timings: { value: string; label: string }[]
  taxonomy: {
    dimensions?: Record<string, { label?: string; items?: ConfigTaxonomyItem[] }>
  }
  taxonomy_labels?: Record<string, string>
  generation?: { require_target_entity: boolean }
}

export interface DoiResultItem {
  doi: string
  status: string
  paper_id?: number
  title?: string
  message?: string
}

export interface PdfResultItem {
  filename: string
  status: string
  doi: string | null
  paper_id: number | null
  internal_id: number | null
  title: string | null
  similarity: number
  message: string
}

export type PaperAuthor = {
  author_id?: number
  author_order?: number
  name: string
  name_en?: string
  name_zh?: string
  affiliation?: string
  affiliation_en?: string
  affiliation_zh?: string
  email?: string
  orcid?: string
  is_first?: boolean
  is_corresponding?: boolean
}

export type MultimodalRelation = { method: string; timing: string }

export interface PaperDetail extends Paper {
  volume?: string | null
  issue?: string | null
  pages?: string | null
  extracted_images?: { path: string; width?: number; height?: number }[] | null
  corresponding_authors?: PaperAuthor[] | null
  co_first_authors?: PaperAuthor[] | null
  first_authors?: PaperAuthor[] | null
  authors?: PaperAuthor[] | null
  full_authors?: { name: string; affiliation?: string }[] | null
  full_authors_source?: "crossref" | "original_data" | "paper_authors" | null
  equipment_brand?: string[] | string | null
  sample_size?: number | null
  tags?: string[] | null
  emerging_quartile?: string | null
  research_fields?: string[] | null
  paradigm?: string[] | null
  research_topics?: string[] | null
  neuroscience_subtopics?: string[] | null
  study_design?: string | null
  paradigms?: string[] | null
  uses_multimodal?: boolean | null
  multimodal_methods?: string[] | null
  multimodal_relations?: MultimodalRelation[] | null
  classification_sources?: Record<string, { source?: string; updated_by?: string; updated_at?: string }> | null
  channel_count?: number | null
  device_citation_text?: string | null
  citation_error_type?: string | null
  uses_target_software?: boolean | null
  accepted_date?: string | null
  notes?: string | null
  corresponding_region?: string | null
}
