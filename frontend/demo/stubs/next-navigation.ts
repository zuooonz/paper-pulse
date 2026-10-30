export function useRouter() {
  return {
    push: (_href: string) => {},
    replace: (_href: string) => {},
    refresh: () => {},
    back: () => {},
    prefetch: async (_href: string) => {},
  }
}
export function usePathname() { return typeof window === "undefined" ? "/" : window.location.pathname }
export function useSearchParams() { return new URLSearchParams(typeof window === "undefined" ? "" : window.location.search) }
export function useParams() { return {} }
export function redirect(_path: string) { throw new Error("redirect is not available in the static demo") }
export function notFound() { throw new Error("not found") }
