import type { ComponentType } from "react"

export default function dynamic<T = object>(
  _loader: () => Promise<{ default: ComponentType<T> }>,
  _options?: Record<string, unknown>,
) {
  return function DynamicPlaceholder() { return null }
}
