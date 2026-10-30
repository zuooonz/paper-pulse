"use client"
import { useSyncExternalStore } from "react"

type DeviceSnapshot = "mobile" | "tablet" | "desktop" | "server"

function subscribeToResize(onStoreChange: () => void) {
  window.addEventListener("resize", onStoreChange)
  return () => window.removeEventListener("resize", onStoreChange)
}

function getDeviceSnapshot(): DeviceSnapshot {
  if (window.innerWidth < 768) return "mobile"
  if (window.innerWidth < 1024) return "tablet"
  return "desktop"
}

function getServerDeviceSnapshot(): DeviceSnapshot {
  return "server"
}

/**
 * 设备检测 hook
 * 用于判断当前是否为移动端视图
 *
 * @returns { isMobile, isTablet, isDesktop }
 * - isMobile: 屏幕宽度 < 768px (Tailwind md 断点)
 * - isTablet: 768px <= 屏幕宽度 < 1024px (Tailwind lg 断点)
 * - isDesktop: 屏幕宽度 >= 1024px
 */
export function useDevice() {
  const snapshot = useSyncExternalStore(
    subscribeToResize,
    getDeviceSnapshot,
    getServerDeviceSnapshot,
  )

  return {
    isMobile: snapshot === "mobile",
    isTablet: snapshot === "tablet",
    isDesktop: snapshot === "desktop" || snapshot === "server",
    isReady: snapshot !== "server",
  }
}

/**
 * 检测是否在钉钉环境
 * 用于决定是否触发自动 SSO 登录
 */
export function useIsDingTalk() {
  return useSyncExternalStore(
    () => () => {},
    () => typeof navigator !== "undefined" && /DingTalk/i.test(navigator.userAgent),
    () => false,
  )
}
