import NextAuth from "next-auth"
import Credentials from "next-auth/providers/credentials"

// Treat an empty value (e.g. an exported but unset variable) the same as unset.
const backendApiUrl = process.env.BACKEND_API_URL || "http://localhost:8000"

export const { handlers, auth, signIn, signOut } = NextAuth({
  trustHost: true,
  providers: [
    Credentials({
      name: "credentials",
      credentials: {
        username: { label: "用户名", type: "text" },
        password: { label: "密码", type: "password" },
      },
      async authorize(credentials) {
        try {
          const res = await fetch(`${backendApiUrl}/api/auth/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              username: credentials.username,
              password: credentials.password,
            }),
          })
          if (!res.ok) return null
          const data = await res.json()
          return {
            id: data.actor_id,
            name: data.name,
            role: data.role,
            accessToken: data.access_token,
            actorId: data.actor_id,
            loginSource: data.login_source,
          }
        } catch {
          return null
        }
      },
    }),
    Credentials({
      name: "dingtalk-sso",
      id: "dingtalk-sso",
      credentials: {
        token: { label: "Token", type: "text" },
        username: { label: "Username", type: "text" },
        name: { label: "Name", type: "text" },
        role: { label: "Role", type: "text" },
      },
      async authorize(credentials) {
        try {
          // 验证 token 是否有效
          const res = await fetch(`${backendApiUrl}/api/auth/verify`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token: credentials.token }),
          })
          if (!res.ok) return null
          const data = await res.json()
          if (!data.valid) return null
          return {
            id: data.actor_id,
            name: data.name,
            role: data.role,
            accessToken: credentials.token as string,
            actorId: data.actor_id,
            loginSource: data.login_source,
          }
        } catch {
          return null
        }
      },
    }),
  ],
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.role = user.role
        token.accessToken = user.accessToken
        token.actorId = user.actorId
        token.loginSource = user.loginSource
      }
      return token
    },
    async session({ session, token }) {
      session.user.role = token.role as string
      session.user.accessToken = token.accessToken as string
      session.user.actorId = token.actorId as string
      session.user.loginSource = token.loginSource as string
      return session
    },
  },
  pages: { signIn: "/login" },
  session: { strategy: "jwt" },
})
