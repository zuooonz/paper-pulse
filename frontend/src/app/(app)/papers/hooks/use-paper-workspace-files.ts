import { useRef, useState, type Dispatch, type SetStateAction } from "react"
import { apiClient } from "@/lib/api"
import type { Paper } from "@/lib/types"
import { downloadFileForUser, isDingTalkDesktop, isDingTalkMobile, openLinkInDingTalkDesktop } from "@/lib/file-download"
import { getApiError, getApiErrorMessage } from "@/lib/api-errors"
import type { DoiResultItem, PaperDetail, PdfResultItem } from "../lib/paper-types"

interface UsePaperWorkspaceFilesOptions {
  selectedIds: Set<number>
  selectAllFiltered: boolean
  getAllFilteredPapers: () => Promise<Paper[]>
  detailPaper: PaperDetail | null
  setDetailPaper: Dispatch<SetStateAction<PaperDetail | null>>
  handleQuotaError: (error: unknown, action: string, paperIds: number[]) => boolean
  refetch: () => void
  setIsDownloading: Dispatch<SetStateAction<boolean>>
}

export function usePaperWorkspaceFiles({
  selectedIds,
  selectAllFiltered,
  getAllFilteredPapers,
  detailPaper,
  setDetailPaper,
  handleQuotaError,
  refetch,
  setIsDownloading,
}: UsePaperWorkspaceFilesOptions) {
  // Upload dialog (DOI + PDF)
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false)
  const [doiText, setDoiText] = useState("")
  const [doiResults, setDoiResults] = useState<DoiResultItem[] | null>(null)
  const [isUploadingDois, setIsUploadingDois] = useState(false)

  // PDF upload states
  const [pdfFiles, setPdfFiles] = useState<File[]>([])
  const [pdfResults, setPdfResults] = useState<PdfResultItem[] | null>(null)
  const [, setIsUploadingPdfs] = useState(false)

  // Single PDF upload (inside detail dialog)
  const [isUploadingSinglePdf, setIsUploadingSinglePdf] = useState(false)
  const singlePdfInputRef = useRef<HTMLInputElement>(null)

  // Row-level PDF upload
  const [rowPdfUploadId, setRowPdfUploadId] = useState<number | null>(null)

  // Add-PDF dialog (auto-download + manual upload)
  const [addPdfDialogOpen, setAddPdfDialogOpen] = useState(false)
  const [addPdfPaper, setAddPdfPaper] = useState<Paper | null>(null)
  const [pdfDownloadLoading, setPdfDownloadLoading] = useState(false)
  const [pdfDownloadResult, setPdfDownloadResult] = useState<{
    success: boolean
    message: string
    method?: string
  } | null>(null)
  const [pdfDropHover, setPdfDropHover] = useState(false)

  function openUploadDialog() {
    setUploadDialogOpen(true)
    setDoiText("")
    setDoiResults(null)
    setPdfFiles([])
    setPdfResults(null)
  }

  async function handleBatchDownloadPdf() {
    let paperIds: number[]
    if (selectAllFiltered) {
      const allPapers = await getAllFilteredPapers()
      paperIds = allPapers.map((paper) => paper.id)
    } else {
      paperIds = Array.from(selectedIds)
    }

    if (paperIds.length === 0) {
      alert("请选择需要下载的论文")
      return
    }
    if (paperIds.length > 100) {
      alert(`PDF 批量下载单次最多 100 篇，当前选中 ${paperIds.length} 篇，请减少后重试`)
      return
    }

    setIsDownloading(true)
    try {
      const res = await apiClient.post(
        "/api/papers/batch-download-pdf",
        { paper_ids: paperIds },
        { timeout: 600000 },
      )
      await downloadFileForUser({
        url: res.data.download_url,
        filename: res.data.filename || `papers_${paperIds.length}.zip`,
        fileType: "zip",
      })
    } catch (e: unknown) {
      if (handleQuotaError(e, "paper.batch_pdf", paperIds)) return
      const apiError = getApiError(e)
      const errorMsg = getApiErrorMessage(e, "未知错误")
      let finalErrorMessage = errorMsg
      if (apiError?.code === "ECONNABORTED" || apiError?.message?.includes("timeout")) {
        finalErrorMessage = "打包超时，请减少论文数量后重试"
      } else if (apiError?.response?.status === 404) {
        finalErrorMessage = "所选论文均无 PDF 文件"
      } else if (apiError?.response?.status === 500) {
        finalErrorMessage = "服务器打包失败，请联系管理员"
      }
      alert("下载失败: " + finalErrorMessage)
    } finally {
      setIsDownloading(false)
    }
  }

  async function handleDoiUpload() {
    const lines = doiText
      .split(/\n/)
      .map((s) => s.trim())
      .filter(Boolean)
    if (!lines.length) return
    setIsUploadingDois(true)
    setDoiResults(null)
    try {
      const res = await apiClient.post("/api/papers/batch-doi", { dois: lines })
      setDoiResults(res.data.results)
      refetch()
    } catch (e: unknown) {
      alert(getApiErrorMessage(e, "DOI 导入失败"))
    } finally {
      setIsUploadingDois(false)
    }
  }

  async function handleSinglePdfUpload(file: File) {
    if (!detailPaper) return
    setIsUploadingSinglePdf(true)
    try {
      const formData = new FormData()
      formData.append("file", file)
      const uploadRes = await apiClient.post(`/api/papers/${detailPaper.id}/upload-pdf`, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      alert(uploadRes.data?.message || "PDF 上传成功")
      if (singlePdfInputRef.current) {
        singlePdfInputRef.current.value = ""
      }
      // Refresh detail
      const res = await apiClient.get(`/api/papers/${detailPaper.id}`)
      setDetailPaper(res.data)
      refetch()
    } catch (e: unknown) {
      alert(getApiErrorMessage(e, "PDF 上传失败"))
    } finally {
      setIsUploadingSinglePdf(false)
    }
  }

  async function handleRowPdfUpload(paperId: number, file: File) {
    setRowPdfUploadId(paperId)
    try {
      const formData = new FormData()
      formData.append("files", file)
      const res = await apiClient.post("/api/papers/batch-pdf", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      const result = res.data.results?.[0]
      if (!result) {
        alert("PDF 处理无结果")
        return
      }

      switch (result.status) {
        case "mismatch":
          alert(
            `DOI 不匹配：PDF 中的论文标题与 CrossRef 记录差异过大（相似度 ${result.similarity}）。\n\n请确认上传的 PDF 是否正确。`
          )
          break
        case "no_doi":
          alert("未从 PDF 中提取到 DOI，无法自动匹配论文。请确认 PDF 是否包含 DOI 信息。")
          break
        case "error":
          alert(`PDF 处理失败：${result.message}`)
          break
        case "created":
          alert(
            `PDF 中的 DOI 在库中不存在，已创建新论文（#${result.internal_id}）。\n如需关联到当前论文，请检查 PDF 是否正确。`
          )
          refetch()
          break
        case "existing_with_pdf":
          alert(`该论文（#${result.internal_id}）已有 PDF，如需覆盖请使用批量导入的替换功能。`)
          refetch()
          break
        case "linked":
        case "replaced": {
          if (result.paper_id !== paperId) {
            alert(
              `PDF 中的 DOI 匹配到了其他论文（#${result.internal_id}），而非当前论文。\n如需关联到当前论文，请检查 PDF 是否正确。`
            )
          } else {
            alert(`PDF 上传成功：${result.message}`)
          }
          refetch()
          break
        }
        default:
          alert(`PDF 处理结果：${result.message}`)
          refetch()
      }
    } catch (e: unknown) {
      alert(getApiErrorMessage(e, "PDF 上传失败"))
    } finally {
      setRowPdfUploadId(null)
    }
  }

  async function handleAutoDownloadPdf(paperId: number) {
    setPdfDownloadLoading(true)
    setPdfDownloadResult(null)
    try {
      const res = await apiClient.post(`/api/papers/${paperId}/download-pdf`)
      setPdfDownloadResult({
        success: true,
        message: res.data.message || "下载成功",
        method: res.data.method,
      })
      refetch()
    } catch (e: unknown) {
      setPdfDownloadResult({
        success: false,
        message: getApiErrorMessage(e, "PDF 下载失败，请尝试手动上传"),
      })
    } finally {
      setPdfDownloadLoading(false)
    }
  }

  async function handlePdfDropUpload(paperId: number, files: FileList | null) {
    if (!files || files.length === 0) return
    const file = Array.from(files).find((f) => f.name.toLowerCase().endsWith(".pdf"))
    if (!file) {
      alert("请上传 PDF 文件")
      return
    }
    await handleRowPdfUpload(paperId, file)
  }

  async function handleOpenPdf(paper: Paper) {
    if (!paper.pdf_storage_path) return

    const dingTalkMobile = isDingTalkMobile()
    const dingTalkDesktop = isDingTalkDesktop()
    const desktopPreview = typeof window !== "undefined" && window.innerWidth >= 768 && !dingTalkMobile
    // DingTalk desktop blocks browser popups in its embedded WebView. Its
    // native openLink bridge is invoked after the signed URL is prepared.
    const previewWindow = desktopPreview && !dingTalkDesktop
      ? window.open("about:blank", "_blank")
      : null
    if (desktopPreview && !dingTalkDesktop && !previewWindow) {
      alert("浏览器阻止了 PDF 预览窗口，请允许本站打开新窗口后重试")
      return
    }
    if (previewWindow) {
      previewWindow.opener = null
      previewWindow.document.title = "正在加载 PDF"
      previewWindow.document.body.style.cssText = "margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center;font:14px system-ui;color:#64748b"
      previewWindow.document.body.textContent = "正在加载 PDF…"
    }

    try {
      const res = await apiClient.post(`/api/papers/${paper.id}/get-pdf`)
      const previewUrl = new URL(res.data.download_url, window.location.origin)
      previewUrl.searchParams.set("preview", "1")
      if (dingTalkDesktop) {
        const opened = await openLinkInDingTalkDesktop(previewUrl.toString())
        if (!opened) window.location.assign(previewUrl.toString())
        return
      }
      if (previewWindow) {
        previewWindow.location.replace(previewUrl.toString())
        return
      }
      await downloadFileForUser({
        url: res.data.download_url,
        filename: res.data.filename || `${paper.internal_id ?? paper.id}.pdf`,
        fileType: "pdf",
      })
    } catch (e: unknown) {
      previewWindow?.close()
      if (handleQuotaError(e, "paper.get_pdf", [paper.id])) return
      alert(getApiErrorMessage(e, "PDF 打开失败"))
    }
  }

  return {
    uploadDialogOpen, setUploadDialogOpen, doiText, setDoiText, doiResults, setDoiResults,
    isUploadingDois, setIsUploadingDois, pdfFiles, setPdfFiles, pdfResults, setPdfResults,
    setIsUploadingPdfs, isUploadingSinglePdf, singlePdfInputRef, rowPdfUploadId,
    addPdfDialogOpen, setAddPdfDialogOpen, addPdfPaper, setAddPdfPaper,
    pdfDownloadLoading, pdfDownloadResult, setPdfDownloadResult, pdfDropHover, setPdfDropHover,
    openUploadDialog, handleBatchDownloadPdf, handleDoiUpload,
    handleSinglePdfUpload, handleRowPdfUpload, handleAutoDownloadPdf, handlePdfDropUpload,
    handleOpenPdf,
  }
}
