"use client"
import { createPortal } from "react-dom"
import { ReactNode, useSyncExternalStore } from "react"

const subscribeToTarget = () => () => {}

function getTarget() {
  return document.getElementById("mobile-header-actions")
}

export function MobileHeaderActions({ children }: { children: ReactNode }) {
  const target = useSyncExternalStore(subscribeToTarget, getTarget, () => null)

  // 桌面端不渲染
  if (typeof window !== "undefined" && window.innerWidth >= 768) {
    return null
  }

  if (!target) return null

  return createPortal(children, target)
}
