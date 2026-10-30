"use client"

import { useEffect } from "react"
import { usePathname, useRouter } from "next/navigation"

const LIMITED_ALLOWED_PREFIXES = ["/papers", "/settings"]

export function RoleAwareContent({ role, children }: { role: string; children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const allowed = role !== "limited"
    || LIMITED_ALLOWED_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`))

  useEffect(() => {
    if (!allowed) router.replace("/papers")
  }, [allowed, router])

  if (!allowed) {
    return <div className="flex h-full items-center justify-center text-sm text-muted-foreground">正在返回论文库…</div>
  }
  return <>{children}</>
}
