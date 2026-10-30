import "next-auth"
declare module "next-auth" {
  interface User { role: string; accessToken: string; actorId: string; loginSource: string }
  interface Session {
    user: { role: string; accessToken: string; actorId: string; loginSource: string; name: string; email: string }
  }
}
import "next-auth/jwt"
declare module "next-auth/jwt" {
  interface JWT { role: string; accessToken: string; actorId: string; loginSource: string }
}
