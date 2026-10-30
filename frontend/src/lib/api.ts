import axios from "axios"
import { getSession, signOut } from "next-auth/react"

let isSigningOut = false
let sessionToken: string | null | undefined
let sessionTokenRequest: Promise<string | null> | null = null

export function syncApiToken(token: string | null) {
  // Keep the backend token in memory only. The httpOnly NextAuth cookie is the
  // persistent credential; writing the API token to localStorage made any XSS
  // permanently stealable.
  sessionToken = token
}

async function resolveApiToken(): Promise<string | null> {
  if (sessionToken !== undefined) return sessionToken
  if (!sessionTokenRequest) {
    sessionTokenRequest = getSession()
      .then((session) => {
        const token = session?.user?.accessToken || null
        syncApiToken(token)
        return token
      })
      .catch(() => null)
      .finally(() => {
        sessionTokenRequest = null
      })
  }
  return sessionTokenRequest
}

export const apiClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "",
  timeout: 120_000,
})

apiClient.interceptors.request.use(async (config) => {
  if (typeof window !== "undefined") {
    // Wait for the authoritative NextAuth session on the first request. This
    // prevents a freshly logged-in user from being signed out by an early 401
    // before TokenSync has populated the in-memory token.
    const token = await resolveApiToken()
    if (token) config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

apiClient.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401 && typeof window !== "undefined") {
      syncApiToken(null)
      // The backend JWT and the NextAuth session have separate lifecycles. If
      // only the local backend token is cleared, the auth middleware still
      // sees an active NextAuth cookie and redirects /login back to the app,
      // causing a refresh loop. End both sessions together.
      if (!isSigningOut) {
        isSigningOut = true
        void signOut({ callbackUrl: "/login" }).catch(() => {
          isSigningOut = false
          window.location.replace("/login")
        })
      }
    }
    return Promise.reject(err)
  }
)
