export type DefaultHomePath = "/workspace" | "/papers"

/**
 * Resolve the landing page from the authenticated application role.
 * Restricted users and viewers open the paper library by default.
 */
export function defaultHomeForRole(role?: string | null): DefaultHomePath {
  return role === "limited" || role === "viewer" ? "/papers" : "/workspace"
}
