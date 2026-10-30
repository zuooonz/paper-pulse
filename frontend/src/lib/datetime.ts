/**
 * 统一处理后端返回的 UTC ISO 日期字符串，以及应用时区中的日期输入。
 * 后端无时区日期按 UTC 解析；datetime-local 输入按应用时区解释。
 */

const appTimezone = process.env.NEXT_PUBLIC_APP_TIMEZONE || "Asia/Shanghai"
const formatterCache = new Map<string, Intl.DateTimeFormat>()

type LocalParts = { year: number; month: number; day: number; hour: number; minute: number; second: number }

function partsAt(timestamp: number, timeZone: string): LocalParts {
  let formatter = formatterCache.get(timeZone)
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    })
    formatterCache.set(timeZone, formatter)
  }
  const parts = formatter.formatToParts(new Date(timestamp))
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return {
    year: Number(values.year),
    month: Number(values.month),
    day: Number(values.day),
    hour: Number(values.hour),
    minute: Number(values.minute),
    second: Number(values.second),
  }
}

function localPartsToString(parts: LocalParts): string {
  const pad = (value: number) => String(value).padStart(2, "0")
  return `${parts.year}-${pad(parts.month)}-${pad(parts.day)}T${pad(parts.hour)}:${pad(parts.minute)}`
}

function offsetAt(timestamp: number, timeZone: string): number {
  const parts = partsAt(timestamp, timeZone)
  const localAsUtc = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second)
  return localAsUtc - Math.floor(timestamp / 1000) * 1000
}

function localWallClockToEpoch(parts: LocalParts, timeZone: string): number {
  const wallClock = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second)
  const offsets = new Set<number>()
  for (let hours = -48; hours <= 48; hours += 6) {
    offsets.add(offsetAt(wallClock + hours * 60 * 60 * 1000, timeZone))
  }

  const candidates = [...offsets].map((offset) => wallClock - offset)
  const matches = candidates.filter((candidate) => {
    const actual = partsAt(candidate, timeZone)
    return actual.year === parts.year && actual.month === parts.month && actual.day === parts.day &&
      actual.hour === parts.hour && actual.minute === parts.minute && actual.second === parts.second
  })
  if (matches.length > 0) return Math.min(...matches)

  // 夏令时跳过的本地时间会向前归一化到最近的有效时间。
  const afterGap = candidates
    .map((timestamp) => ({ timestamp, parts: partsAt(timestamp, timeZone) }))
    .filter(({ parts: actual }) => Date.UTC(actual.year, actual.month - 1, actual.day, actual.hour, actual.minute, actual.second) > wallClock)
    .sort((a, b) => {
      const aWall = Date.UTC(a.parts.year, a.parts.month - 1, a.parts.day, a.parts.hour, a.parts.minute, a.parts.second)
      const bWall = Date.UTC(b.parts.year, b.parts.month - 1, b.parts.day, b.parts.hour, b.parts.minute, b.parts.second)
      return aWall - bWall || a.timestamp - b.timestamp
    })
  if (afterGap[0]) return afterGap[0].timestamp
  throw new RangeError(`无法将本地时间转换为时区 ${timeZone} 的时间`)
}

/** 将 datetime-local 字符串按应用时区解释，返回 UTC ISO 字符串。 */
export function appLocalInputToIso(localValue: string, timeZone = appTimezone): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(localValue)
  if (!match) throw new RangeError("日期时间格式无效")
  const [, year, month, day, hour, minute, second = "0"] = match
  const parts = { year: Number(year), month: Number(month), day: Number(day), hour: Number(hour), minute: Number(minute), second: Number(second) }
  const expected = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second)
  const normalized = new Date(expected)
  if (normalized.getUTCFullYear() !== parts.year || normalized.getUTCMonth() + 1 !== parts.month ||
    normalized.getUTCDate() !== parts.day || normalized.getUTCHours() !== parts.hour ||
    normalized.getUTCMinutes() !== parts.minute || normalized.getUTCSeconds() !== parts.second) {
    throw new RangeError("日期时间无效")
  }
  const timestamp = localWallClockToEpoch(parts, timeZone)
  return new Date(timestamp).toISOString()
}

/** 返回应用时区下的下一个有效整点，供 datetime-local 输入使用。 */
export function nextHourInAppTimezone(now = new Date(), timeZone = appTimezone): string {
  let timestamp = Math.floor(now.getTime() / 60_000) * 60_000 + 60_000
  for (let minute = 0; minute < 181; minute += 1, timestamp += 60_000) {
    const parts = partsAt(timestamp, timeZone)
    if (parts.minute === 0 && parts.second === 0) return localPartsToString(parts)
  }
  throw new RangeError(`无法找到时区 ${timeZone} 的下一个整点`)
}

/** 返回应用时区日期往前偏移指定天数后的 YYYY-MM-DD。 */
export function appDateOffset(days: number, now = new Date()): string {
  const local = partsAt(now.getTime(), appTimezone)
  const date = new Date(Date.UTC(local.year, local.month - 1, local.day - days))
  const pad = (value: number) => String(value).padStart(2, "0")
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`
}

/** 解析后端返回的 ISO 日期字符串。无时区值按 UTC 解析。 */
export function parseApiDate(iso: string | null | undefined): Date | null {
  if (!iso) return null
  if (/[Zz]|[+-]\d{2}:?\d{2}$/.test(iso)) return new Date(iso)
  return new Date(iso + "Z")
}

/** 格式化后端日期为应用时区的短日期和时间。 */
export function fmtApiDate(
  iso: string | null | undefined,
  opts: Intl.DateTimeFormatOptions = { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
): string {
  const date = parseApiDate(iso)
  if (!date) return "—"
  return date.toLocaleString("zh-CN", { ...opts, timeZone: appTimezone })
}

/** 格式化后端日期为应用时区的日期部分。 */
export function fmtApiDateOnly(
  iso: string | null | undefined,
  opts: Intl.DateTimeFormatOptions = { year: "numeric", month: "2-digit", day: "2-digit" }
): string {
  const date = parseApiDate(iso)
  if (!date) return "—"
  return date.toLocaleDateString("zh-CN", { ...opts, timeZone: appTimezone })
}
