"use client"

import { RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface QueryErrorNoticeProps {
  message: string
  onRetry: () => void
  retrying?: boolean
  className?: string
}

export function QueryErrorNotice({ message, onRetry, retrying = false, className }: QueryErrorNoticeProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-lg border border-destructive/20 bg-destructive/5 p-6 text-center",
        className,
      )}
    >
      <p className="text-sm text-destructive">{message}</p>
      <Button type="button" size="sm" variant="outline" onClick={onRetry} disabled={retrying}>
        <RefreshCw className={cn("mr-1.5 size-3.5", retrying && "animate-spin")} />
        {retrying ? "重试中…" : "重试"}
      </Button>
    </div>
  )
}
