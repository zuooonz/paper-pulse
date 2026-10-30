import { forwardRef } from "react"
import type { AnchorHTMLAttributes, ReactNode } from "react"

type LinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & { href: string; children: ReactNode; prefetch?: boolean }
const Link = forwardRef<HTMLAnchorElement, LinkProps>(function Link({ href, children, prefetch: _prefetch, onClick, ...props }, ref) {
  return (
    <a
      ref={ref}
      href={href}
      onClick={(event) => {
        onClick?.(event)
        if (event.defaultPrevented) return
        event.preventDefault()
        window.dispatchEvent(new CustomEvent("demo-notice", {
          detail: "这是只读论文库演示；其他页面和完整应用请查看 GitHub 仓库。",
        }))
      }}
      {...props}
    >
      {children}
    </a>
  )
})
export default Link
