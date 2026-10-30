type RecordValue = Record<string, unknown>

interface ApiErrorLike {
  code?: string
  message?: string
  response?: {
    status?: number
    data?: unknown
  }
}

function asRecord(value: unknown): RecordValue | null {
  return typeof value === "object" && value !== null
    ? value as RecordValue
    : null
}

export function getApiError(error: unknown): ApiErrorLike | null {
  const value = asRecord(error)
  if (!value) return null

  const response = asRecord(value.response)
  return {
    ...(typeof value.code === "string" ? { code: value.code } : {}),
    ...(typeof value.message === "string" ? { message: value.message } : {}),
    ...(response
      ? {
          response: {
            ...(typeof response.status === "number" ? { status: response.status } : {}),
            data: response.data,
          },
        }
      : {}),
  }
}

export function getApiErrorDetail(error: unknown): unknown {
  return asRecord(getApiError(error)?.response?.data)?.detail
}

export function getApiErrorMessage(error: unknown, fallback: string): string {
  const detail = getApiErrorDetail(error)
  if (typeof detail === "string") return detail

  const detailMessage = asRecord(detail)?.message
  if (typeof detailMessage === "string") return detailMessage

  const responseData = getApiError(error)?.response?.data
  if (typeof responseData === "string" && responseData.trim()) return responseData

  return getApiError(error)?.message || fallback
}

export function isRecord(value: unknown): value is RecordValue {
  return asRecord(value) !== null
}
