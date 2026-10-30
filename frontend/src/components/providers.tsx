"use client"
import { ThemeProvider } from "next-themes"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useState, useEffect } from "react"
import { SessionProvider, useSession } from "next-auth/react"
import { syncApiToken } from "@/lib/api"

function TokenSync() {
  const { data: session, status } = useSession()
  useEffect(() => {
    if (status === "loading") return
    syncApiToken(session?.user?.accessToken || null)
  }, [session, status])
  return null
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: 1 } }
  }))
  return (
    <SessionProvider>
      <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
        <QueryClientProvider client={queryClient}>
          <TokenSync />
          {children}
        </QueryClientProvider>
      </ThemeProvider>
    </SessionProvider>
  )
}
