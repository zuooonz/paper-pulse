import { useCallback, useMemo, useState } from "react"
import { useDraft, usePatchDraft, type PaperImage } from "@/hooks/use-drafts"

const EMPTY_IMAGE_PATHS: string[] = []

function defaultSelectedImages(images: PaperImage[] | null | undefined, saved: string[] | null | undefined): string[] {
  if (saved?.length) return saved
  const visible = (images ?? []).filter((image) => image.size_kb >= 50 || image.size_kb === 0)
  return visible.length > 0 ? [visible[0].path] : []
}

export function useDraftEditor(selectedId: number | null) {
  const { data: draft } = useDraft(selectedId)
  const patchDraft = usePatchDraft()
  const [snapshot, setSnapshot] = useState<{
    draftId: number
    title: string
    content: string
    selectedImagePaths: string[]
  } | null>(null)

  const defaultImagePaths = useMemo(
    () => draft && draft.id === selectedId
      ? defaultSelectedImages(draft.paper_images, draft.paper_selected_images)
      : EMPTY_IMAGE_PATHS,
    [draft, selectedId],
  )
  const current = draft && draft.id === selectedId
    ? snapshot?.draftId === draft.id
      ? snapshot
      : { draftId: draft.id, title: draft.title, content: draft.content ?? "", selectedImagePaths: defaultImagePaths }
    : null

  const update = useCallback((changes: Partial<NonNullable<typeof current>>) => {
    if (!draft || draft.id !== selectedId) return
    setSnapshot((previous) => {
      const base = previous?.draftId === draft.id
        ? previous
        : { draftId: draft.id, title: draft.title, content: draft.content ?? "", selectedImagePaths: defaultImagePaths }
      return { ...base, ...changes }
    })
  }, [draft, selectedId, defaultImagePaths])

  const setTitle = useCallback((title: string) => update({ title }), [update])
  const setContent = useCallback((content: string) => update({ content }), [update])
  const setSelectedImagePaths = useCallback((next: string[] | ((previous: string[]) => string[])) => {
    const previous = current?.selectedImagePaths ?? EMPTY_IMAGE_PATHS
    update({ selectedImagePaths: typeof next === "function" ? next(previous) : next })
  }, [current?.selectedImagePaths, update])

  const title = current?.title ?? ""
  const content = current?.content ?? ""
  const selectedImagePaths = current?.selectedImagePaths ?? EMPTY_IMAGE_PATHS
  const isDirty = Boolean(draft && current && (
    title !== draft.title || content !== (draft.content ?? "") ||
    selectedImagePaths.length !== defaultImagePaths.length ||
    selectedImagePaths.some((path, index) => path !== defaultImagePaths[index])
  ))

  const saveDraft = useCallback(async (status?: string, includeImages = true) => {
    if (!selectedId) return
    const data: Record<string, unknown> = { title, content }
    if (status) data.status = status
    if (includeImages) data.selected_images = selectedImagePaths
    return patchDraft.mutateAsync({ id: selectedId, data })
  }, [selectedId, title, content, selectedImagePaths, patchDraft])

  const resetSnapshot = useCallback(() => setSnapshot(null), [])

  return {
    draft,
    title,
    content,
    selectedImagePaths,
    setTitle,
    setContent,
    setSelectedImagePaths,
    isDirty,
    saveDraft,
    resetSnapshot,
    patchDraft,
  }
}
