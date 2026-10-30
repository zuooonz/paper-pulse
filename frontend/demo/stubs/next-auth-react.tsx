import type { ReactNode } from "react"

const demoSession = {
  data: {
    user: {
      name: "演示管理员",
      role: "admin",
      actorId: "demo:admin",
      loginSource: "local",
      accessToken: "demo-token",
    },
  },
  status: "authenticated" as const,
}

export function useSession() { return demoSession }
export async function signIn() {}
export async function signOut() {}
export function SessionProvider({ children }: { children: ReactNode }) { return <>{children}</> }
