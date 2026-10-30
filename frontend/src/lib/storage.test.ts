import assert from "node:assert/strict"
import { test } from "node:test"

import { STORAGE_KEYS } from "./storage.ts"

test("browser storage keys use the current application namespace", () => {
  assert.deepEqual(Object.values(STORAGE_KEYS), [
    "paper_pulse_papers_columns_v2",
    "paper_pulse_word_summary_job",
  ])
})
