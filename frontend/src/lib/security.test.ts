import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"

const draftEditor = readFileSync(
  new URL("../app/(app)/workspace/components/draft-editor.tsx", import.meta.url),
  "utf8",
)

test("draft preview never injects raw HTML", () => {
  assert.doesNotMatch(draftEditor, /dangerouslySetInnerHTML/)
})

test("draft images never put the API token in a URL", () => {
  assert.doesNotMatch(draftEditor, /access_token=/)
  assert.doesNotMatch(draftEditor, /localStorage\.getItem\(STORAGE_KEYS/)
})
