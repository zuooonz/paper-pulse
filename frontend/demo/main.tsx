import { StrictMode, useEffect, useState } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ThemeProvider } from "next-themes"
import PapersWorkspace from "@/app/(app)/papers/components/papers-workspace"
import { CollapsibleSidebar } from "@/components/collapsible-sidebar"
import "./demo.css"

if (typeof window !== "undefined" && !window.location.search) {
  window.history.replaceState(null, "", "?entity_label=&review_outcome=all")
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 60_000, retry: 0, refetchOnWindowFocus: false } },
})

function DemoBanner() {
  return (
    <div className="fixed inset-x-0 top-0 z-[100] flex items-center justify-center gap-2 border-b border-sky-200 bg-sky-50 px-3 py-1 text-[11px] text-sky-900">
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500" />
      GitHub Pages 静态演示 · 使用真实前端组件与 [demo] 数据 · 只读，不连接后端
    </div>
  )
}

function DemoNotice() {
  const [message, setMessage] = useState("")
  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<string>).detail
      setMessage(detail)
      window.setTimeout(() => setMessage(""), 3200)
    }
    window.addEventListener("demo-notice", handler)
    return () => window.removeEventListener("demo-notice", handler)
  }, [])
  if (!message) return null
  return <div className="fixed bottom-5 right-5 z-[110] max-w-sm rounded-lg bg-slate-900 px-4 py-3 text-xs text-white shadow-xl">{message}</div>
}

function Demo() {
  useEffect(() => {
    const handler = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null
      const button = target?.closest("button")
      if (!button) return
      const label = (button.textContent || "").trim()
      const inDialog = Boolean(button.closest('[role="dialog"]'))
      const topLevelWrite = /导入论文|批量生成|生成综述|导出 Excel|下载 PDF|总结论文|更新 DOI|上传 PDF|删除/.test(label)
      if (!inDialog && !topLevelWrite) return
      if (inDialog && /关闭|取消/.test(label)) return
      event.preventDefault()
      event.stopPropagation()
      window.dispatchEvent(new CustomEvent("demo-notice", {
        detail: "这是只读演示，导入、审核、发布等写操作不会保存。",
      }))
    }
    document.addEventListener("click", handler, true)
    return () => document.removeEventListener("click", handler, true)
  }, [])
  return (
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <QueryClientProvider client={queryClient}>
        <DemoBanner />
        <DemoNotice />
        <div className="app-viewport flex min-h-0 bg-background overflow-hidden pt-6">
          <CollapsibleSidebar role="admin" userName="演示管理员" />
          <main className="min-h-0 min-w-0 flex-1 overflow-hidden pt-12 md:pt-0">
            <PapersWorkspace />
          </main>
        </div>
      </QueryClientProvider>
    </ThemeProvider>
  )
}

createRoot(document.getElementById("root")!).render(<StrictMode><Demo /></StrictMode>)
