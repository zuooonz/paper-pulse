import type { ReactNode } from "react"
import { RefreshCw } from "lucide-react"

import { Button } from "@/components/ui/button"

type SettingsSectionHeaderProps = {
  title: string
  description?: string
  onRefresh?: () => void
  refreshing?: boolean
  actions?: ReactNode
}

export function SettingsSectionHeader({
  title,
  description,
  onRefresh,
  refreshing = false,
  actions,
}: SettingsSectionHeaderProps) {
  return (
    <div className="flex min-h-[3.25rem] min-w-0 flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
          {onRefresh && (
            <Button
              size="icon-sm"
              variant="ghost"
              onClick={onRefresh}
              disabled={refreshing}
              aria-label={`刷新${title}`}
              title="刷新"
              className="text-muted-foreground"
            >
              <RefreshCw className={`size-3.5 ${refreshing ? "animate-spin" : ""}`} />
            </Button>
          )}
        </div>
        {description && (
          <p className="mt-1 max-w-3xl text-sm leading-5 text-muted-foreground">{description}</p>
        )}
      </div>
      {actions && <div className="flex min-h-8 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}
