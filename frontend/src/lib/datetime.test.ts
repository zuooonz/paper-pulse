import assert from "node:assert/strict"
import { test } from "node:test"

import { appLocalInputToIso, nextHourInAppTimezone } from "./datetime.ts"

test("application-local input is converted with the Asia/Shanghai offset", () => {
  assert.equal(appLocalInputToIso("2026-10-22T09:30", "Asia/Shanghai"), "2026-10-22T01:30:00.000Z")
  assert.equal(nextHourInAppTimezone(new Date("2026-10-22T00:17:00.000Z"), "Asia/Shanghai"), "2026-10-22T09:00")
})

test("UTC input does not inherit the browser timezone", () => {
  assert.equal(appLocalInputToIso("2026-10-22T09:30", "UTC"), "2026-10-22T09:30:00.000Z")
  assert.equal(nextHourInAppTimezone(new Date("2026-10-22T09:17:00.000Z"), "UTC"), "2026-10-22T10:00")
})

test("America/New_York input uses the offset for the selected date", () => {
  assert.equal(appLocalInputToIso("2026-01-15T12:30", "America/New_York"), "2026-01-15T17:30:00.000Z")
  assert.equal(appLocalInputToIso("2026-07-15T12:30", "America/New_York"), "2026-07-15T16:30:00.000Z")
  assert.equal(nextHourInAppTimezone(new Date("2024-03-10T06:45:00.000Z"), "America/New_York"), "2024-03-10T03:00")
})

test("ambiguous and skipped daylight-saving times resolve consistently", () => {
  assert.equal(appLocalInputToIso("2024-11-03T01:30", "America/New_York"), "2024-11-03T05:30:00.000Z")
  assert.equal(appLocalInputToIso("2024-03-10T02:30", "America/New_York"), "2024-03-10T07:30:00.000Z")
})
