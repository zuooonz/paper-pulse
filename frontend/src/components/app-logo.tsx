import { cn } from "@/lib/utils"

type AppLogoProps = {
  className?: string
  iconClassName?: string
  showWordmark?: boolean
}

export function AppLogo({
  className,
  iconClassName,
  showWordmark = true,
}: AppLogoProps) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <span
        className={cn(
          "inline-flex shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground",
          iconClassName,
        )}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 32 32"
          className="h-[72%] w-[72%]"
          fill="none"
        >
          <path
            d="M16 9.5c-3.1-2.3-7.3-2.6-11.5-2v16c4.2-.6 8.4-.3 11.5 2 3.1-2.3 7.3-2.6 11.5-2v-16c-4.2-.6-8.4-.3-11.5 2Z"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinejoin="round"
          />
          <path d="M16 9.5v16" stroke="currentColor" strokeWidth="2" />
          <path d="M8 12h4M20 12h4M8 16h4M20 16h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </span>
      {showWordmark && <span className="font-semibold">Paper Pulse</span>}
    </span>
  )
}
