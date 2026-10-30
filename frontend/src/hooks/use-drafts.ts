import { useQuery, useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiClient } from "@/lib/api"
import type { Draft, OutboxItem } from "@/lib/types"

export interface PaperImage {
  path: string
  caption: string
  size_kb: number
  data_uri?: string | null
}

export function useInfiniteDrafts(status: string, q: string) {
  return useInfiniteQuery({
    queryKey: ["drafts", "infinite", status, q],
    queryFn: async ({ pageParam = 1 }) => {
      const res = await apiClient.get("/api/drafts", {
        params: { status, q, page: pageParam, page_size: 20 },
      })
      return res.data as { items: Draft[]; total: number; page: number; page_size: number }
    },
    initialPageParam: 1,
    getNextPageParam: (lastPage) => {
      const { page, page_size, total } = lastPage
      return page * page_size < total ? page + 1 : undefined
    },
    // 不用 refetchInterval：useInfiniteQuery 重取会重置所有已加载页面
    // 草稿状态更新通过 outbox 轮询和手动刷新感知
    staleTime: 30_000,
  })
}

export function useDraft(id: number | null) {
  return useQuery({
    queryKey: ["draft", id],
    queryFn: async () => {
      const res = await apiClient.get(`/api/drafts/${id}`)
      return res.data as Draft & {
        paper_images: PaperImage[]
        paper_abstract: string
        meta: Record<string, unknown> | null
        paper_selected_images: string[]
      }
    },
    enabled: id !== null,
  })
}

export function usePatchDraft() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: Record<string, unknown> }) =>
      apiClient.patch(`/api/drafts/${id}`, data),
    onSuccess: (_, { id }) => {
      qc.invalidateQueries({ queryKey: ["draft", id] })
      qc.invalidateQueries({ queryKey: ["drafts"] })
    },
  })
}

export function usePublishDraft() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, account_ids }: { id: number; account_ids: number[] }) =>
      apiClient.post(
        `/api/drafts/${id}/publish`,
        { account_ids },
        { headers: { "Idempotency-Key": crypto.randomUUID() } },
      ),
    onSuccess: (_, { id }) => {
      qc.invalidateQueries({ queryKey: ["drafts"] })
      qc.invalidateQueries({ queryKey: ["draft", id] })
    },
  })
}

export function useBatchPublish() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ draft_ids, account_ids }: { draft_ids: number[]; account_ids: number[] }) =>
      apiClient.post(
        "/api/drafts/batch-publish",
        { draft_ids, account_ids },
        { headers: { "Idempotency-Key": crypto.randomUUID() } },
      ),
    onSuccess: (_, { draft_ids }) => {
      qc.invalidateQueries({ queryKey: ["drafts"] })
      draft_ids.forEach((id) => qc.invalidateQueries({ queryKey: ["draft", id] }))
    },
  })
}

export function useDeleteDraft() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => apiClient.delete(`/api/drafts/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["drafts"] })
      qc.invalidateQueries({ queryKey: ["papers"] })
    },
  })
}

// ── Scheduled publish hooks ──

export function useScheduleDraft() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, account_ids, scheduled_at }: { id: number; account_ids: number[]; scheduled_at: string }) =>
      apiClient.post(`/api/drafts/${id}/schedule`, { account_ids, scheduled_at }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["drafts"] })
      qc.invalidateQueries({ queryKey: ["scheduled"] })
      qc.invalidateQueries({ queryKey: ["draft"] })
    },
  })
}

export function useBatchSchedule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: {
      draft_ids: number[]
      account_ids: number[]
      start_at: string
      interval_hours: number
      jitter_hours: number
    }) => apiClient.post("/api/drafts/batch-schedule", data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["drafts"] })
      qc.invalidateQueries({ queryKey: ["scheduled"] })
      qc.invalidateQueries({ queryKey: ["draft"] })
    },
  })
}

// ── Outbox (unified publish task list) ──

export function useOutbox() {
  return useQuery({
    queryKey: ["outbox"],
    queryFn: async () => {
      const res = await apiClient.get("/api/drafts/outbox")
      return res.data.items as OutboxItem[]
    },
    refetchInterval: 3000,
    placeholderData: (previousData) => previousData,
  })
}

export function useCancelOutboxRecord() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (record_id: number) =>
      apiClient.delete(`/api/drafts/outbox/${record_id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["outbox"] })
      qc.invalidateQueries({ queryKey: ["scheduled"] })
      qc.invalidateQueries({ queryKey: ["drafts"] })
      qc.invalidateQueries({ queryKey: ["draft"] })
    },
  })
}

export function useClearOutbox() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiClient.delete("/api/drafts/outbox/clear"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["outbox"] })
      qc.invalidateQueries({ queryKey: ["drafts"] })
      qc.invalidateQueries({ queryKey: ["draft"] })
    },
  })
}

// ── Word upload ──

export function useUploadWord() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData()
      formData.append("file", file)
      const res = await apiClient.post("/api/drafts/upload-word", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      return res.data as { ok: boolean; draft_id: number; title: string; image_count: number }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["drafts"] })
    },
  })
}

