/**
 * Copy text to clipboard with fallback for non-secure contexts (HTTP).
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // fall through to fallback
    }
  }
  try {
    const ta = document.createElement("textarea")
    ta.value = text
    ta.style.position = "fixed"
    ta.style.left = "-9999px"
    ta.style.top = "-9999px"
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    document.execCommand("copy")
    document.body.removeChild(ta)
    return true
  } catch {
    return false
  }
}

/** Check if paper has enough metadata for a valid APA citation. */
export function canFormatAPA(paper: { title?: string | null; journal_name?: string | null; year?: number | null; apa_citation?: string | null }): boolean {
  return !!(paper.apa_citation || (paper.title && paper.journal_name && paper.year))
}
