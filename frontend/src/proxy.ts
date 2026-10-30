import { auth } from "@/auth"
import { NextResponse } from "next/server"
import { defaultHomeForRole } from "@/lib/default-home"

export default auth((req) => {
  const isLoggedIn = !!req.auth
  const isLoginPage = req.nextUrl.pathname.startsWith("/login")
  if (!isLoggedIn && !isLoginPage) {
    return NextResponse.redirect(new URL("/login", req.url))
  }
  if (isLoggedIn && isLoginPage) {
    return NextResponse.redirect(new URL(defaultHomeForRole(req.auth?.user?.role), req.url))
  }
})

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
}
