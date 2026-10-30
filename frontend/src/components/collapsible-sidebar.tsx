"use client"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { signOut } from "next-auth/react"
import { ThemeToggle } from "./theme-toggle"
import { AppLogo } from "./app-logo"
import { cn } from "@/lib/utils"
import {
  LayoutDashboard, Settings, BookOpen, LogOut, Map, Video, Menu, X, ChevronLeft, ChevronRight
} from "lucide-react"
import { Button } from "./ui/button"
import { useState } from "react"
import { apiClient } from "@/lib/api"

const NAV_ITEMS = [
  { href: "/workspace", icon: LayoutDashboard, label: "推文库" },
  { href: "/videos",    icon: Video,            label: "视频库" },
  { href: "/papers",    icon: BookOpen,         label: "论文库" },
  { href: "/authors",   icon: Map,              label: "作者库" },
  { href: "/settings",  icon: Settings,         label: "设置" },
]

export function CollapsibleSidebar({ role, userName }: { role: string; userName: string }) {
  const pathname = usePathname()
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)

  // 受限用户只能访问论文库
  const visibleItems = role === "limited"
    ? NAV_ITEMS.filter((item) => item.href === "/papers")
    : NAV_ITEMS

  const handleNavClick = () => {
    setMobileOpen(false)
  }

  const handleLogout = async () => {
    try { await apiClient.post("/api/auth/logout") } catch { /* local sign-out must still proceed */ }
    await signOut({ callbackUrl: "/login" })
  }

  return (
    <>
      {/* 移动端遮罩 */}
      {mobileOpen && (
        <div
          className="md:hidden fixed inset-0 bg-black/50 z-40"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* 移动端顶栏：菜单按钮 + 页面操作按钮 */}
      <div className="md:hidden fixed top-0 left-0 right-0 z-30 bg-card border-b border-border">
        <div className="flex items-center px-3 py-2 min-h-[48px]">
          <button
            onClick={() => setMobileOpen(true)}
            className="p-2 -ml-2 text-muted-foreground hover:text-foreground shrink-0"
          >
            <Menu className="w-5 h-5" />
          </button>
          {/* 页面操作按钮会通过 portal 渲染到这里 */}
          <div id="mobile-header-actions" className="flex-1 flex items-center justify-end gap-1 overflow-hidden" />
        </div>
      </div>

      {/* 侧边栏 */}
      <aside className={cn(
        "relative hidden md:flex flex-col border-r border-border bg-card shrink-0 transition-all duration-200",
        collapsed ? "w-16" : "w-[220px]"
      )}>
        {/* Logo */}
        <div className={cn(
          "flex items-center border-b border-border px-3 py-4",
          collapsed ? "justify-center" : "gap-2.5"
        )}>
          <AppLogo showWordmark={false} iconClassName="h-8 w-8" />
          {!collapsed && (
            <div className="min-w-0 leading-tight">
              <div className="truncate text-sm font-semibold text-foreground">Paper Pulse</div>
              <div className="truncate text-xs text-muted-foreground">论文内容工作台</div>
            </div>
          )}
        </div>

        {/* 折叠按钮：贴合侧栏边缘，避免与导航内容争夺空间 */}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="absolute top-[4.25rem] -right-3 z-20 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-card text-muted-foreground shadow-sm transition-colors hover:bg-accent hover:text-foreground"
          aria-label={collapsed ? "展开菜单栏" : "收起菜单栏"}
          title={collapsed ? "展开菜单栏" : "收起菜单栏"}
        >
          {collapsed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronLeft className="w-3.5 h-3.5" />}
        </button>

        {/* Nav items */}
        <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
          {visibleItems.map(({ href, icon: Icon, label }) => {
            const active = pathname.startsWith(href)
            return (
              <Link key={href} href={href}
                className={cn(
                  "flex items-center gap-2.5 rounded-lg text-sm transition-colors",
                  collapsed ? "justify-center px-2 py-2" : "px-3 py-2",
                  active
                    ? "bg-accent text-accent-foreground font-medium"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
                )}>
                <Icon className="w-4 h-4 shrink-0" />
                {!collapsed && label}
              </Link>
            )
          })}
        </nav>

        {/* Bottom: user + theme toggle, then logout */}
        <div className={cn(
          "border-t border-border px-3",
          collapsed ? "py-2" : "py-3 space-y-1"
        )}>
          {!collapsed && (
            <div className="flex items-center justify-between px-1">
              <span className="text-xs text-muted-foreground truncate">{userName}</span>
              <ThemeToggle />
            </div>
          )}
          <Button
            variant="ghost"
            size="sm"
            className={cn(
              "text-muted-foreground hover:text-destructive",
              collapsed ? "w-full justify-center px-0" : "w-full justify-start gap-2"
            )}
            onClick={handleLogout}
          >
            <LogOut className="w-3.5 h-3.5" />
            {!collapsed && "退出登录"}
          </Button>
        </div>
      </aside>

      {/* 移动端抽屉 */}
      <aside className={cn(
        "md:hidden fixed inset-y-0 left-0 z-50 flex flex-col w-64 bg-card border-r border-border transform transition-transform duration-200",
        mobileOpen ? "translate-x-0" : "-translate-x-full"
      )}>
        {/* Logo */}
        <div className="flex items-center gap-2.5 px-4 py-4 border-b border-border">
          <AppLogo showWordmark={false} iconClassName="h-8 w-8" />
          <div className="leading-tight">
            <div className="text-sm font-semibold text-foreground">Paper Pulse</div>
            <div className="text-xs text-muted-foreground">论文内容工作台</div>
          </div>
          <button
            onClick={() => setMobileOpen(false)}
            className="ml-auto p-1 text-muted-foreground hover:text-foreground"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Nav items */}
        <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
          {visibleItems.map(({ href, icon: Icon, label }) => {
            const active = pathname.startsWith(href)
            return (
              <Link key={href} href={href} onClick={handleNavClick}
                className={cn(
                  "flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors",
                  active
                    ? "bg-accent text-accent-foreground font-medium"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
                )}>
                <Icon className="w-4 h-4 shrink-0" />
                {label}
              </Link>
            )
          })}
        </nav>

        {/* Bottom: user + theme toggle, then logout */}
        <div className="px-3 py-3 border-t border-border space-y-1">
          <div className="flex items-center justify-between px-1">
            <span className="text-xs text-muted-foreground truncate">{userName}</span>
            <ThemeToggle />
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start gap-2 text-muted-foreground hover:text-destructive"
            onClick={handleLogout}
          >
            <LogOut className="w-3.5 h-3.5" />
            退出登录
          </Button>
        </div>
      </aside>
    </>
  )
}
