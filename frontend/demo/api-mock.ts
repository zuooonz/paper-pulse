import papersFixture from "./fixtures/papers.json"
import filterOptions from "./fixtures/filter-options.json"
import configOptions from "./fixtures/config-options.json"
import quota from "./fixtures/quota.json"
import paperEntities from "./fixtures/paper-entities.json"
import literatureEntities from "./fixtures/literature-entities.json"
import drafts from "./fixtures/drafts.json"
import outbox from "./fixtures/drafts-outbox.json"
import accounts from "./fixtures/accounts.json"
import details from "./fixtures/paper-details.json"
import citations from "./fixtures/citations.json"

type Params = Record<string, unknown>
type ApiConfig = { params?: Params; timeout?: number; responseType?: string }
const allPapers = (papersFixture as { items: Record<string, unknown>[] }).items
const detailMap = details as Record<string, Record<string, unknown>>
const citationMap = citations as Record<string, { citation?: string }>

function error(message: string): Promise<never> {
  const err = new Error(message) as Error & { response?: { status: number; data: { detail: string } } }
  err.response = { status: 405, data: { detail: message } }
  return Promise.reject(err)
}

function matches(paper: Record<string, unknown>, params: Params): boolean {
  const review = String(params.review_outcome || "").split(",").filter(Boolean)
  if (review.length && !review.includes(String(paper.review_outcome))) return false
  const entity = String(params.entity_label || "")
  if (entity && String(paper.entity_label || "") !== entity) return false
  const q = String(params.q || "").trim().toLowerCase()
  if (q) {
    const haystack = [paper.title, paper.title_zh, paper.journal_name, paper.doi, paper.entity_label, ...(Array.isArray(paper.authors) ? paper.authors : [])]
      .join(" ").toLowerCase()
    if (!haystack.includes(q)) return false
  }
  return true
}

async function get<T = unknown>(url: string, config: ApiConfig = {}): Promise<{ data: T }> {
  const params = config.params || {}
  if (url === "/api/papers") {
    const filtered = allPapers.filter((paper) => matches(paper, params))
    const page = Math.max(1, Number(params.page || 1))
    const pageSize = Math.max(1, Number(params.page_size || 20))
    return {
      data: {
        items: filtered.slice((page - 1) * pageSize, page * pageSize),
        total: filtered.length,
        page,
        page_size: pageSize,
      } as T,
    }
  }
  if (url === "/api/papers/filter-options") return { data: filterOptions as T }
  if (url === "/api/config/options") return { data: configOptions as T }
  if (url === "/api/governance/quota/me") return { data: quota as T }
  if (url === "/api/accounts") return { data: accounts as T }
  if (url === "/api/papers/entities") return { data: paperEntities as T }
  if (url === "/api/literature-search/entities") return { data: literatureEntities as T }
  if (url === "/api/literature-search/tasks/latest") return { data: null as T }
  if (url === "/api/drafts") return { data: drafts as T }
  if (url === "/api/drafts/outbox") return { data: outbox as T }
  const paperMatch = url.match(/^\/api\/papers\/(\d+)$/)
  if (paperMatch) {
    const detail = detailMap[paperMatch[1]]
    if (detail) return { data: detail as T }
    return error("演示数据中不存在该论文")
  }
  const citationMatch = url.match(/^\/api\/papers\/(\d+)\/citation$/)
  if (citationMatch) return { data: (citationMap[citationMatch[1]] || { citation: "" }) as T }
  return error(`静态演示未提供接口：${url}`)
}

export const apiClient = {
  get,
  post: async () => error("这是只读静态演示，修改不会保存。"),
  patch: async () => error("这是只读静态演示，修改不会保存。"),
  put: async () => error("这是只读静态演示，修改不会保存。"),
  delete: async () => error("这是只读静态演示，修改不会保存。"),
}
