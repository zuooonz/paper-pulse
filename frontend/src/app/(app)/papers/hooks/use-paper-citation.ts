import { useEffect, useState } from "react"
import { apiClient } from "@/lib/api"
import { copyToClipboard } from "@/lib/citation"
import type { Paper } from "@/lib/types"

export interface CitationCopyState {
  paperId: number
  source: "list" | "detail"
  status: "success" | "error"
  message: string
}

export function usePaperCitation(getRequestErrorMessage: (error: unknown, fallback: string) => string) {
  const [citationCopyState, setCitationCopyState] = useState<CitationCopyState | null>(null)
  async function handleCopyCitation(paper: Paper, source: "list" | "detail") {
    try {
      // Always use the live formatter so stale imported citations are not copied.
      const res = await apiClient.get(`/api/papers/${paper.id}/citation`)
      const text = String(res.data?.citation || "").trim()
      if (!text) throw new Error("未生成可用引用")
      const ok = await copyToClipboard(text)
      if (!ok) throw new Error("浏览器未允许写入剪贴板")
      setCitationCopyState({ paperId: paper.id, source, status: "success", message: "APA 引用已复制" })
    } catch (error: unknown) {
      const reason = getRequestErrorMessage(error, "请稍后重试")
      setCitationCopyState({ paperId: paper.id, source, status: "error", message: `复制失败：${reason}` })
    }
  }

  useEffect(() => {
    if (!citationCopyState) return
    const timer = window.setTimeout(() => setCitationCopyState(null), 2400)
    return () => window.clearTimeout(timer)
  }, [citationCopyState])

  return { citationCopyState, handleCopyCitation }
}
