export type UserRole = "admin" | "reviewer" | "viewer" | "limited"
export type PaperReviewOutcome = "unreviewed" | "approved" | "excluded"

export interface Paper {
  id: number
  internal_id: number | null
  doi: string | null
  apa_citation?: string | null
  title: string
  title_zh: string | null
  journal_name: string | null
  year: number | null
  volume?: string | null
  issue?: string | null
  pages?: string | null
  impact_factor: number | null
  jcr_quartile: string | null
  cas_quartile: string | null
  cites_target: boolean | null
  entity_label: string | null
  is_in_domain: boolean | null
  article_status: string | null
  is_processing: boolean
  can_review: boolean
  review_outcome: PaperReviewOutcome
  reviewed_by?: string | null
  reviewed_at?: string | null
  review_note?: string | null
  has_pdf: boolean
  metadata_complete: boolean
  can_generate: boolean
  abstract?: string | null
  abstract_zh?: string | null
  subject_disease?: string | null
  subject_diseases?: string[] | null
  corresponding_region?: string | null
  research_fields?: string[] | null
  paradigm?: string[] | null
  tags?: string[] | null
  research_topics?: string[] | null
  neuroscience_subtopics?: string[] | null
  study_design?: string | null
  paradigms?: string[] | null
  uses_multimodal?: boolean | null
  multimodal_methods?: string[] | null
  multimodal_relations?: { method: string; timing: string }[] | null
  classification_sources?: Record<string, { source?: string; updated_by?: string; updated_at?: string }> | null
  conclusions_zh?: string | null
  highlights_zh?: string[] | null
  pdf_storage_path: string | null
  first_authors?: { name: string; name_zh?: string; affiliation?: string; affiliation_en?: string; affiliation_zh?: string }[] | null
  corresponding_authors?: { name: string; name_zh?: string; affiliation?: string; affiliation_en?: string; affiliation_zh?: string }[] | null
  authors?: { name: string; name_zh?: string; affiliation?: string; affiliation_en?: string; affiliation_zh?: string; is_first?: boolean; is_corresponding?: boolean }[] | null
  draft_ids?: number[]
  status?: string
  corresponding_country?: string | null
  corresponding_province?: string | null
  corresponding_city?: string | null
  corresponding_institution_type?: string | null
  corresponding_department?: string | null
  subject_age_mean?: number | null
  emerging_quartile?: string | null
  co_first_authors?: { name: string; name_zh?: string; affiliation?: string; affiliation_en?: string; affiliation_zh?: string }[] | null
  full_authors?: { name: string; affiliation?: string }[] | null
  full_authors_source?: "crossref" | "original_data" | "paper_authors" | null
  keywords?: string[] | null
  keywords_zh?: string[] | null
  population?: unknown
  equipment_brand?: string[] | string | null
  channel_count?: number | null
  sample_size?: number | null
  uses_target_software?: boolean | null
  device_citation_text?: string | null
  citation_error_type?: string | null
  notes?: string | null
  pmid?: string | null
  pdf_text_extracted?: boolean | null
  extracted_images?: { path: string; width?: number; height?: number }[] | null
  created_at?: string | null
  updated_at?: string | null
}

export interface User {
  username: string
  name: string
  role: UserRole
}

export interface Draft {
  id: number
  draft_type: string
  title: string
  title_zh?: string | null
  content?: string
  status: string
  reviewer?: string | null
  reviewed_at?: string | null
  created_at: string
  updated_at?: string | null
  paper_title: string | null
  paper_id: number | null
  paper_internal_id: number | null
  paper_ids: { id: number; internal_id: number | null; title: string }[]
  review_notes: string | null
  published_at: string | null
  meta?: Record<string, unknown> | null
  paper_abstract?: string | null
  paper_conclusions_zh?: string | null
  paper_images?: { path: string; caption: string; size_kb: number }[]
  paper_selected_images?: string[]
}

export interface Account {
  id: number
  channel: string
  account_name: string
  credential_type: string
  expires_at: string | null
  valid: boolean | null
  is_active?: boolean
  note?: string | null
  bound_at: string | null
  created_at?: string | null
  last_validated_at: string | null
  validation_status: string
  health: AccountHealth
}

export interface AccountHealth {
  overall: string
  ttl_based: string
  validation_status: string
  last_validated_at: string | null
  message: string
}

export interface AccountValidationResponse {
  ok: boolean
  account_id: number
  channel: string
  status: string
  message: string
  last_validated_at: string | null
  validation_status: string
  health: AccountHealth | null
}

export interface PublishRecord {
  id: number
  draft_id: number
  draft_title?: string
  channel: string
  account_name: string
  status: string
  url: string | null
  error: string | null
  scheduled_at?: string | null
  published_at: string | null
  created_at?: string | null
}

export interface OutboxAccount {
  channel: string
  account_name: string
  record_id: number
  status: string
  error: string | null
  url: string | null
  published_at: string | null
}

export interface OutboxItem {
  draft_id: number
  draft_title: string | null
  scheduled_at: string | null
  accounts: OutboxAccount[]
  record_ids: number[]
}
