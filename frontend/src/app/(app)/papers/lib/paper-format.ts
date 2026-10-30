import type { PaperDetail } from "./paper-types"

export function truncate(s: string | null | undefined, n: number) {
  if (!s) return "—"
  return s.length > n ? s.slice(0, n) + "…" : s
}

export interface PaperAuthorEntry {
  name?: string
  name_zh?: string
  affiliation?: string
  affiliation_en?: string
  affiliation_zh?: string
}

export function paperAuthorEntries(list: unknown): PaperAuthorEntry[] {
  if (Array.isArray(list)) return list as PaperAuthorEntry[]
  if (typeof list === "string") {
    try { const p = JSON.parse(list); return Array.isArray(p) ? p : [] } catch { return [] }
  }
  return []
}

export function authorNamesText(list: PaperAuthorEntry[]): string {
  return list.length ? list.map((a) => a.name_zh || a.name || "—").join("、") : "—"
}

export function authorAffiliationsText(list: PaperAuthorEntry[]): string {
  return list.length ? list.map((a) => a.affiliation_zh || a.affiliation_en || a.affiliation || "—").join("、") : "—"
}

export function authorPairsTitle(list: PaperAuthorEntry[]): string {
  return list.map((a) => `${a.name_zh || a.name || "—"}（${a.affiliation_zh || a.affiliation_en || a.affiliation || "—"}）`).join("\n")
}

// ─── Editable form sub-component ───
export function fieldVal(p: PaperDetail | null, key: string, fallback = "") {
  if (!p) return fallback
  const v = (p as unknown as Record<string, unknown>)[key]
  return v != null ? String(v) : fallback
}

// ─── Utility: safe parse JSON fields (handle stringified JSON from DB) ───
export function safeArrayStr(v: unknown): string {
  if (Array.isArray(v)) return v.join(" / ")
  if (typeof v === "string") { try { const p = JSON.parse(v); return Array.isArray(p) ? p.join(" / ") : v } catch { return v } }
  return String(v ?? "")
}
export function safeAuth(v: unknown): { name: string; name_zh?: string; affiliation: string; affiliation_en?: string; affiliation_zh?: string; author_id?: number }[] {
  if (Array.isArray(v)) return v as { name: string; name_zh?: string; affiliation: string; affiliation_en?: string; affiliation_zh?: string; author_id?: number }[]
  if (typeof v === "string") { try { const p = JSON.parse(v); return Array.isArray(p) ? p : [] } catch { return [] } }
  return []
}
