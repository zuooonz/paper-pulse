import type { Metadata, Viewport } from "next"
import "./globals.css"
import { Providers } from "@/components/providers"

export const metadata: Metadata = {
  title: {
    default: "Paper Pulse",
    template: "%s · Paper Pulse",
  },
  description: "面向科研团队的论文管理、作者分析与内容协作工作台。",
}

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: "cover",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh" suppressHydrationWarning>
      <body className="overflow-hidden font-sans antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
