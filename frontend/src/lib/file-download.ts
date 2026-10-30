export type FileDownloadResult =
  | "link-shared"
  | "link-copied"
  | "dingtalk-preview"
  | "browser"
  | "cancelled"

export interface FileDownloadOptions {
  url: string
  filename: string
  fileType?: string
}

interface DingTalkDownloadApi {
  showActionSheet(params: {
    items: string[]
    title: string
    cancelButtonText: string
  }): Promise<{ index: number }>
  biz?: {
    util?: {
      openLink?(params: {
        url: string
        enableShare?: boolean
      }): Promise<unknown>
      systemShare(params: {
        type: number
        title?: string
        url?: string
        images: string[]
      }): Promise<unknown>
    }
  }
  setClipboard?(params: { text: string }): Promise<unknown>
  downloadFile(params: { url: string; header?: Record<string, string> }): Promise<{ filePath?: string }>
  openDocument(params: { filePath: string; fileType?: string }): Promise<unknown>
}

export function isDingTalkMobile(): boolean {
  if (typeof navigator === "undefined") return false
  const userAgent = navigator.userAgent
  return /DingTalk/i.test(userAgent) && /Android|iPhone|iPad|iPod|HarmonyOS|Mobile/i.test(userAgent)
}

export function isDingTalkDesktop(): boolean {
  if (typeof navigator === "undefined") return false
  return /DingTalk/i.test(navigator.userAgent) && !isDingTalkMobile()
}

function absoluteUrl(url: string): string {
  return new URL(url, window.location.origin).toString()
}

/**
 * Ask the DingTalk desktop client to open a URL without relying on a browser
 * popup. Returns false when the bridge or API is unavailable so callers can
 * use same-window navigation as a reliable fallback.
 */
export async function openLinkInDingTalkDesktop(url: string): Promise<boolean> {
  if (typeof window === "undefined" || !isDingTalkDesktop()) return false

  try {
    const imported = await import("dingtalk-jsapi")
    const dd = (imported.default || imported) as unknown as DingTalkDownloadApi
    const openLink = dd.biz?.util?.openLink
    if (!openLink) return false
    await openLink({ url: absoluteUrl(url), enableShare: false })
    return true
  } catch (error) {
    console.warn("钉钉桌面端打开链接失败，将在当前窗口打开", error)
    return false
  }
}

function inferFileType(filename: string, explicitType?: string): string | undefined {
  if (explicitType) return explicitType.replace(/^\./, "").toLowerCase()
  const extension = filename.split(".").pop()
  return extension && extension !== filename ? extension.toLowerCase() : undefined
}

function browserDownload(url: string, filename: string): FileDownloadResult {
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  anchor.rel = "noopener"
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  return "browser"
}

function waitForNativeSheetToClose(delayMs = 400): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, delayMs))
}

async function copyDownloadLink(url: string, dd?: DingTalkDownloadApi): Promise<void> {
  if (dd?.setClipboard) {
    try {
      await dd.setClipboard({ text: url })
      return
    } catch {
      // Continue with browser clipboard fallbacks.
    }
  }

  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(url)
      return
    } catch {
      // Some DingTalk WebViews expose the API but deny clipboard permission.
    }
  }

  const textarea = document.createElement("textarea")
  textarea.value = url
  textarea.style.position = "fixed"
  textarea.style.opacity = "0"
  document.body.appendChild(textarea)
  textarea.select()
  const copied = document.execCommand("copy")
  textarea.remove()
  if (!copied) throw new Error("复制下载链接失败")
}

function downloadShareText(url: string, filename: string): string {
  return `文件：${filename}\n下载链接（7 天内有效）：${url}`
}

async function shareTextLinkToDingTalk(
  dd: DingTalkDownloadApi,
  url: string,
  filename: string,
): Promise<FileDownloadResult> {
  const text = downloadShareText(url, filename)

  try {
    const systemShare = dd.biz?.util?.systemShare
    if (!systemShare) throw new Error("当前钉钉版本不支持系统分享")
    await systemShare({
      // This is the only share form verified to reach DingTalk reliably in the
      // mobile client. The complete URL is embedded in the text payload.
      type: 4,
      title: text,
      url,
      images: [],
    })
    return "link-shared"
  } catch {
    await copyDownloadLink(url, dd)
    window.alert("钉钉分享面板不可用，完整下载链接已复制（7 天内有效）")
    return "link-copied"
  }
}

async function dingTalkDownload(
  url: string,
  filename: string,
  fileType?: string,
): Promise<FileDownloadResult> {
  const imported = await import("dingtalk-jsapi")
  const dd = (imported.default || imported) as unknown as DingTalkDownloadApi
  const normalizedType = inferFileType(filename, fileType)
  const canPreview = !!normalizedType && [
    "pdf", "doc", "docx", "xls", "xlsx", "txt",
  ].includes(normalizedType)
  let choice: { index: number }
  try {
    choice = await dd.showActionSheet({
      title: filename,
      items: [
        canPreview ? "下载并打开" : "使用浏览器下载",
        "分享链接",
        "复制链接",
      ],
      cancelButtonText: "取消",
    })
  } catch {
    // Older clients may not support showActionSheet; retain the two main actions.
    choice = {
      index: window.confirm(`是否直接下载 ${filename}？\n选择“取消”将尝试分享链接。`) ? 0 : 1,
    }
  }

  if (choice.index < 0) return "cancelled"

  if (choice.index === 1) {
    // showActionSheet is native UI. Opening another native panel during its
    // dismissal animation makes the new panel immediately disappear on some
    // DingTalk Android versions.
    await waitForNativeSheetToClose()
    return shareTextLinkToDingTalk(dd, url, filename)
  }

  if (choice.index === 2) {
    await copyDownloadLink(url, dd)
    window.alert("完整下载链接已复制（7 天内有效）")
    return "link-copied"
  }

  if (!canPreview) {
    return browserDownload(url, filename)
  }

  const downloaded = await dd.downloadFile({ url })
  if (!downloaded.filePath) {
    throw new Error("钉钉未返回本地文件路径")
  }
  await dd.openDocument({
    filePath: downloaded.filePath,
    fileType: normalizedType,
  })
  return "dingtalk-preview"
}

/**
 * Download a server-hosted file using the native DingTalk flow when available.
 * The URL must be reachable over HTTPS by the DingTalk client/server; signed
 * temporary URLs are preferred over exposing a user's login token.
 */
export async function downloadFileForUser(
  options: FileDownloadOptions,
): Promise<FileDownloadResult> {
  if (typeof window === "undefined") return "cancelled"

  const url = absoluteUrl(options.url)
  if (isDingTalkMobile()) {
    try {
      return await dingTalkDownload(url, options.filename, options.fileType)
    } catch (error) {
      console.warn("钉钉原生文件下载失败，回退到浏览器下载", error)
      window.alert("钉钉原生下载不可用，将尝试使用浏览器下载")
    }
  }

  return browserDownload(url, options.filename)
}
