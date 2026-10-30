import { auth } from "@/auth"
import { redirect } from "next/navigation"
import { CollapsibleSidebar } from "@/components/collapsible-sidebar"
import { RoleAwareContent } from "@/components/role-aware-content"

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const session = await auth()
  if (!session) redirect("/login")

  return (
    <div className="app-viewport flex min-h-0 bg-background overflow-hidden">
      {/* 折叠侧边栏（桌面+移动端统一） */}
      <CollapsibleSidebar role={session.user.role} userName={session.user.name ?? ""} />

      {/* 主内容区 */}
      <main className="min-h-0 min-w-0 flex-1 overflow-hidden pt-12 md:pt-0">
        <RoleAwareContent role={session.user.role}>{children}</RoleAwareContent>
      </main>
    </div>
  )
}
