"use client"

import { useState, useMemo, useCallback, useEffect, useRef } from "react"
import dynamic from "next/dynamic"
import { Loader2, MapPin, Download, Filter, List, Map, X } from "lucide-react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { apiClient } from "@/lib/api"
import { useDevice } from "@/hooks/use-device"
import { getApiErrorMessage } from "@/lib/api-errors"
import { QueryErrorNotice } from "@/components/query-error-notice"

const AuthorMap = dynamic(() => import("./author-map"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center h-full bg-muted/20">
      <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
    </div>
  ),
})

// ── Types ────────────────────────────────────────────────────────────

interface AuthorBrief {
  author_id: number
  name: string
  name_zh: string
  role: "first_author" | "corresponding" | "co_author"
}
interface LocationData {
  affiliation: string; country: string; city: string; province: string; lat: number; lng: number
  author_count: number; paper_count: number; authors: AuthorBrief[]
}
interface NetworkEdge { source: string; target: string; weight: number; source_lat: number; source_lng: number; target_lat: number; target_lng: number; cross_city: boolean }
interface NetworkNode { id: string; city: string; lat: number | null; lng: number | null; paper_count: number; partner_count: number }
interface NetworkResponse { nodes: NetworkNode[]; edges: NetworkEdge[]; total_nodes: number; total_edges: number; cross_city_edges: number; same_city_edges: number; papers_with_collab: number; papers_solo: number; max_weight: number }
interface GeoResponse { locations: LocationData[]; total_locations: number }
interface StatsResponse {
  total_authors: number; total_cities: number; total_institutions: number; total_papers_mapped: number
  geo_review_pending: number
}
interface AuthorPaperItem {
  paper_id: number; internal_id?: number | null; title: string; title_zh?: string | null; year?: number | null
  is_first: boolean; is_corresponding: boolean
}
interface AuthorProfileResponse {
  author: { id: number; name: string; name_zh?: string | null; affiliation?: string | null; affiliation_zh?: string | null; canonical_affiliation?: string | null; city?: string | null; province?: string | null; email?: string | null; orcid?: string | null }
  papers: AuthorPaperItem[]
}
interface GeoReviewCandidate { provider: string; result_name?: string | null; province?: string | null; city?: string | null; district?: string | null; provider_confidence?: number | null; conflict_reason?: string | null }
interface GeoReviewItem { affiliation_location_id: number; affiliation: string; current_province?: string | null; current_city?: string | null; candidates: GeoReviewCandidate[] }
interface GeoReviewResponse { items: GeoReviewItem[]; total: number }

// ── Page ─────────────────────────────────────────────────────────────

const COUNTRY_OPTIONS = [
  "中国", "美国", "日本", "韩国", "德国", "英国",
  "加拿大", "澳大利亚", "法国", "意大利", "荷兰", "瑞士", "瑞典", "新加坡", "印度", "巴西",
]
const TARGET_LABEL = process.env.NEXT_PUBLIC_TARGET_LABEL || "qMRLab"

export default function AuthorsPage() {
  const queryClient = useQueryClient()
  const { isMobile } = useDevice()
  const [selectedCity, setSelectedCity] = useState<string | null>(null)
  const [flyTo, setFlyTo] = useState<[number, number] | null>(null)
  const [showNetwork, setShowNetwork] = useState(false)
  const [country, setCountry] = useState("中国")
  const [province, setProvince] = useState("")
  const [city, setCity] = useState("")
  const [search, setSearch] = useState("")
  const [debouncedSearch, setDebouncedSearch] = useState("")
  const [showTarget, setShowTarget] = useState(true)
  const [showNonTarget] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const [showMobileFilter, setShowMobileFilter] = useState(false)
  const [mobileView, setMobileView] = useState<"list" | "map">("list")
  const [selectedAuthorId, setSelectedAuthorId] = useState<number | null>(null)
  const [showGeoReview, setShowGeoReview] = useState(false)
  const scope =
    showTarget && showNonTarget ? "all" :
    showTarget ? "target" :
    showNonTarget ? "non_target" : "none"

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [search])

  const mobileCityFilterKey = `${country}|${province}|${city}|${scope}|${debouncedSearch}`
  const [mobileCityWindow, setMobileCityWindow] = useState({ key: mobileCityFilterKey, count: 20 })
  const visibleMobileCities = mobileCityWindow.key === mobileCityFilterKey ? mobileCityWindow.count : 20

  const filterParams = useMemo(
    () => ({ ...(country ? { country } : {}), province: province || undefined, city: city || undefined, search: debouncedSearch || undefined, scope }),
    [country, province, city, debouncedSearch, scope],
  )

  // ── Provinces (dynamic from selected country) ──
  const { data: provincesData } = useQuery<{ provinces: string[] }>({
    queryKey: ["authors-provinces", country],
    queryFn: () => apiClient.get("/api/authors/provinces", { params: { country } }).then(r => r.data),
    staleTime: 5 * 60 * 1000,
    enabled: !!country,
  })

  // ── Cities (dynamic from selected country + province) ──
  const { data: citiesData } = useQuery<{ cities: string[] }>({
    queryKey: ["authors-cities", country, province],
    queryFn: () => apiClient.get("/api/authors/cities", {
      params: { ...(country ? { country } : {}), ...(province ? { province } : {}) }
    }).then(r => r.data),
    staleTime: 5 * 60 * 1000,
  })

  // ── Main data ──
  const {
    data: geo,
    isLoading,
    isError: isGeoError,
    isRefetchError: isGeoRefetchError,
    error: geoError,
    refetch: refetchGeo,
    isFetching: isGeoFetching,
  } = useQuery<GeoResponse>({
    queryKey: ["authors-geo", filterParams],
    queryFn: () => apiClient.get("/api/authors/geo", { params: filterParams }).then(r => r.data),
    staleTime: 5 * 60 * 1000,
  })

  const {
    data: stats,
    isError: isStatsError,
    isRefetchError: isStatsRefetchError,
    error: statsError,
    refetch: refetchStats,
    isFetching: isStatsFetching,
  } = useQuery<StatsResponse>({
    queryKey: ["authors-stats", filterParams],
    queryFn: () => apiClient.get("/api/authors/stats", { params: filterParams }).then(r => r.data),
    staleTime: 5 * 60 * 1000,
  })

  const {
    data: network,
    isError: isNetworkError,
    isRefetchError: isNetworkRefetchError,
    error: networkError,
    refetch: refetchNetwork,
    isFetching: isNetworkFetching,
  } = useQuery<NetworkResponse>({
    queryKey: ["authors-network", filterParams],
    queryFn: () => apiClient.get("/api/authors/network", {
      params: { min_papers: 2, top_n: 300, ...filterParams }
    }).then(r => r.data),
    staleTime: 5 * 60 * 1000,
  })

  const {
    data: authorProfile,
    isLoading: isAuthorProfileLoading,
    isError: isAuthorProfileError,
    error: authorProfileError,
    refetch: refetchAuthorProfile,
    isFetching: isAuthorProfileFetching,
  } = useQuery<AuthorProfileResponse>({
    queryKey: ["author-profile", selectedAuthorId],
    queryFn: () => apiClient.get(`/api/authors/profile/${selectedAuthorId}`).then(r => r.data),
    enabled: selectedAuthorId != null,
    staleTime: 5 * 60 * 1000,
  })
  const {
    data: geoReview,
    isLoading: isGeoReviewLoading,
    isError: isGeoReviewError,
    error: geoReviewError,
    refetch: refetchGeoReview,
    isFetching: isGeoReviewFetching,
  } = useQuery<GeoReviewResponse>({
    queryKey: ["author-geo-review"],
    queryFn: () => apiClient.get("/api/authors/review/geography", { params: { limit: 100 } }).then(r => r.data),
    enabled: showGeoReview,
  })
  const acceptGeoCandidate = useMutation({
    mutationFn: ({ locationId, provider }: { locationId: number; provider: string }) => apiClient.post(`/api/authors/review/geography/${locationId}`, { provider }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["author-geo-review"] })
      queryClient.invalidateQueries({ queryKey: ["authors-geo"] })
      queryClient.invalidateQueries({ queryKey: ["authors-stats"] })
    },
    onError: (error: unknown) => alert(getApiErrorMessage(error, "地理信息更新失败")),
  })

  const cityGroups = useMemo(() => {
    if (!geo?.locations) return []
    const groups: Record<string, { locations: LocationData[]; totalPapers: number; totalAuthors: number }> = {}
    for (const loc of geo.locations) {
      const key = loc.city || loc.affiliation
      const entry = groups[key] || (groups[key] = { locations: [], totalPapers: 0, totalAuthors: 0 })
      entry.locations.push(loc)
      entry.totalPapers += loc.paper_count
      entry.totalAuthors += loc.author_count
    }
    return Object.entries(groups).map(([city, data]) => ({ city, ...data })).sort((a, b) => b.totalPapers - a.totalPapers)
  }, [geo])

  const mobileCityGroups = cityGroups.slice(0, visibleMobileCities)
  const mobileCityListRef = useRef<HTMLDivElement>(null)
  const mobileCityLoadRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const target = mobileCityLoadRef.current
    if (!target || visibleMobileCities >= cityGroups.length) return
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setMobileCityWindow((window) => ({
          key: mobileCityFilterKey,
          count: Math.min((window.key === mobileCityFilterKey ? window.count : 20) + 20, cityGroups.length),
        }))
      }
    }, { root: mobileCityListRef.current, threshold: 0.1 })
    observer.observe(target)
    return () => observer.disconnect()
  }, [visibleMobileCities, cityGroups.length, mobileCityFilterKey])

  // ── Export ──
  const handleExport = useCallback(async () => {
    setIsExporting(true)
    try {
      const res = await apiClient.get("/api/authors/export", {
        params: filterParams, responseType: "blob",
      })
      const blob = new Blob([res.data], { type: "text/csv;charset=utf-8" })
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url; a.download = `authors_${new Date().toISOString().slice(0, 10)}.csv`
      document.body.appendChild(a); a.click(); a.remove()
      window.URL.revokeObjectURL(url)
    } catch (e: unknown) {
      alert("导出失败: " + getApiErrorMessage(e, "未知错误"))
    } finally { setIsExporting(false) }
  }, [filterParams])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      {(isGeoError || isGeoRefetchError || isStatsError || isStatsRefetchError || (showNetwork && (isNetworkError || isNetworkRefetchError))) && (
        <div className="max-h-[35%] shrink-0 space-y-2 overflow-y-auto p-3 md:px-4">
          {(isGeoError || isGeoRefetchError) && (
            <QueryErrorNotice
              className="p-3"
              message={`作者地理数据加载失败：${getApiErrorMessage(geoError, "请稍后重试")}`}
              onRetry={() => { void refetchGeo() }}
              retrying={isGeoFetching}
            />
          )}
          {(isStatsError || isStatsRefetchError) && (
            <QueryErrorNotice
              className="p-3"
              message={`作者统计加载失败：${getApiErrorMessage(statsError, "请稍后重试")}`}
              onRetry={() => { void refetchStats() }}
              retrying={isStatsFetching}
            />
          )}
          {showNetwork && (isNetworkError || isNetworkRefetchError) && (
            <QueryErrorNotice
              className="p-3"
              message={`合作网络加载失败：${getApiErrorMessage(networkError, "请稍后重试")}`}
              onRetry={() => { void refetchNetwork() }}
              retrying={isNetworkFetching}
            />
          )}
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden md:flex-row">

      {/* Sidebar - desktop only */}
      <div className="hidden md:block w-72 shrink-0 border-r border-border bg-card overflow-y-auto scrollbar-none">
        <div className="p-4 border-b border-border space-y-3">
          <h1 className="text-lg font-bold">作者库</h1>

          {/* ── Unified filters ── */}
          <div className="space-y-2">
            <p className="text-[10px] text-muted-foreground uppercase tracking-wide">筛选条件</p>

            <input
              type="search"
              aria-label="搜索作者与单位"
              placeholder="搜索作者或单位（中英文）"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-9 w-full rounded-lg border bg-background px-2.5 text-xs"
            />

            {/* Country / Province / City — one row */}
            <div className="grid grid-cols-3 gap-1">
              <select value={country} onChange={(e) => { setCountry(e.target.value); setProvince(""); setCity(""); setSelectedCity(null) }}
                className="text-xs border rounded px-1.5 py-1 bg-background">
                {COUNTRY_OPTIONS.map(c => <option key={c} value={c}>{c}</option>)}
                <option value="">全部国家</option>
              </select>
              <select value={province} onChange={(e) => { setProvince(e.target.value); setCity(""); setSelectedCity(null) }}
                className="text-xs border rounded px-1.5 py-1 bg-background">
                <option value="">全部省份</option>
                {(provincesData?.provinces || []).map(p => <option key={p} value={p}>{p}</option>)}
              </select>
              <select value={city} onChange={(e) => { setCity(e.target.value); setSelectedCity(null) }}
                className="text-xs border rounded px-1.5 py-1 bg-background">
                <option value="">全部城市</option>
                {(citiesData?.cities || []).map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>

            {/* Target and other entities */}
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="checkbox" checked={showTarget} onChange={(e) => setShowTarget(e.target.checked)}
                  className="w-3.5 h-3.5 rounded border-border" style={{ accentColor: "var(--primary)" }} />
                {TARGET_LABEL}
              </label>
              <label className="flex items-center gap-1.5 text-xs cursor-not-allowed opacity-50" title="非目标课题论文作者信息尚未校准，暂不可用">
                <input type="checkbox" checked={showNonTarget} disabled
                  className="w-3.5 h-3.5 rounded border-border" style={{ accentColor: "#6b7280" }} />
                非目标课题
              </label>
            </div>
          </div>

          {/* Stats */}
          {stats && (
            <div className="grid grid-cols-2 gap-2">
              <div className="bg-blue-50 dark:bg-blue-950 rounded-lg p-2.5">
                <div className="text-xs text-muted-foreground">作者</div>
                <div className="text-lg font-bold text-primary">{stats.total_authors}</div>
              </div>
              <div className="bg-green-50 dark:bg-green-950 rounded-lg p-2.5">
                <div className="text-xs text-muted-foreground">论文</div>
                <div className="text-lg font-bold text-green-600">{stats.total_papers_mapped}</div>
              </div>
              <div className="bg-purple-50 dark:bg-purple-950 rounded-lg p-2.5">
                <div className="text-xs text-muted-foreground">城市</div>
                <div className="text-lg font-bold text-purple-600">{stats.total_cities}</div>
              </div>
              <div className="bg-orange-50 dark:bg-orange-950 rounded-lg p-2.5">
                <div className="text-xs text-muted-foreground">机构</div>
                <div className="text-lg font-bold text-orange-600">{stats.total_institutions}</div>
              </div>
            </div>
          )}
          {stats && stats.geo_review_pending > 0 && <button type="button" onClick={() => setShowGeoReview(true)} className="w-full rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-left text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200"><span className="font-medium">地理信息待审核</span><span className="float-right">{stats.geo_review_pending} 个机构</span></button>}
          {/* Network toggle + stats */}
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input type="checkbox" checked={showNetwork} onChange={(e) => setShowNetwork(e.target.checked)}
                className="w-3.5 h-3.5 rounded border-border" style={{ accentColor: "var(--primary)" }} />
              <span className="text-muted-foreground font-medium">合作网络</span>
              {network && (
                <span className="text-[10px] text-muted-foreground">
                  {network.total_nodes}机构·{network.total_edges}连线
                </span>
              )}
            </label>
            <button onClick={handleExport} disabled={isExporting}
              className="text-xs text-muted-foreground hover:text-primary transition-colors disabled:opacity-50">
              <Download className="w-3.5 h-3.5 inline" />
              {isExporting ? "…" : "导出"}
            </button>
          </div>
        </div>

        {/* City list */}
        <div className="divide-y divide-border">
          {cityGroups.map(({ city: cityName, totalPapers, totalAuthors, locations }) => {
            const expanded = selectedCity === cityName || !!(province || city)
            return (
            <div key={cityName}>
              <button
                className={`w-full text-left px-4 py-3 hover:bg-accent/50 transition-colors ${selectedCity === cityName ? "bg-accent" : ""}`}
                onClick={() => { setSelectedCity(selectedCity === cityName ? null : cityName); if (!isMobile && locations[0]) setFlyTo([locations[0].lat, locations[0].lng]) }}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium text-sm flex items-center gap-1.5">
                    <MapPin className="w-3.5 h-3.5 text-primary" />{cityName}
                  </span>
                  <span className="text-xs text-muted-foreground">{totalPapers} 篇</span>
                </div>
                <div className="text-xs text-muted-foreground mt-0.5">{locations.length} 个机构 · {totalAuthors} 位作者</div>
              </button>
              {expanded && (
                <div className="bg-muted/30 px-4 py-2 space-y-2">
                  {locations.map((loc) => (
                    <div key={loc.affiliation} className="text-xs cursor-pointer hover:text-primary transition-colors" onClick={() => setFlyTo([loc.lat, loc.lng])}>
                      <div className="flex items-center justify-between">
                        <span className="font-medium truncate max-w-[200px]">{loc.affiliation}</span>
                        <span className="text-muted-foreground shrink-0">{loc.paper_count} 篇</span>
                      </div>
                      <div className="text-muted-foreground mt-0.5 flex flex-wrap gap-x-2 gap-y-1">
                        {loc.authors.filter((a) => a.role !== "co_author").slice(0, 3).map((a) => (
                          <button key={a.author_id} type="button" className="hover:text-primary hover:underline" onClick={(event) => { event.stopPropagation(); setSelectedAuthorId(a.author_id) }}>
                            {a.name_zh || a.name}
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )})}
          {cityGroups.length === 0 && <div className="p-4 text-xs text-muted-foreground">没有匹配的作者或单位</div>}
        </div>
      </div>

      {/* Map */}
      <div className={`${mobileView === "map" ? "flex" : "hidden"} md:flex flex-1 min-h-0 relative z-0 isolate overflow-hidden`}>
        {geo && (
          <AuthorMap key="map" locations={geo.locations} edges={showNetwork ? (network?.edges ?? []) : []} flyTo={flyTo} />
        )}
      </div>

      {/* Mobile city list: progressive rendering keeps the map responsive */}
      <div className={`${mobileView === "list" ? "flex" : "hidden"} md:hidden flex-1 min-h-0 bg-card flex-col`}>
        <div ref={mobileCityListRef} className="flex-1 min-h-0 overflow-y-auto scrollbar-none divide-y divide-border">
          {mobileCityGroups.map(({ city: cityName, totalPapers, totalAuthors, locations }) => {
            const expanded = selectedCity === cityName
            return (
              <div key={cityName}>
                <button
                  className={`w-full text-left px-3 py-2 transition-colors ${expanded ? "bg-accent/50" : "hover:bg-accent/50"}`}
                  onClick={() => {
                    setSelectedCity(expanded ? null : cityName)
                  }}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-sm truncate flex items-center gap-1.5"><MapPin className="w-3.5 h-3.5 text-primary shrink-0" />{cityName}</span>
                    <span className="text-xs text-muted-foreground shrink-0">{totalPapers} 篇</span>
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-0.5">{locations.length} 个机构 · {totalAuthors} 位作者</div>
                </button>
                {expanded && (
                  <div className="bg-muted/30 px-3 py-2 space-y-1.5">
                    {locations.map((loc) => (
                      <div key={loc.affiliation} className="w-full text-left text-xs py-1.5">
                        <span className="flex items-center justify-between gap-2"><span className="truncate">{loc.affiliation}</span><span className="text-muted-foreground shrink-0">{loc.paper_count} 篇</span></span>
                        <div className="mt-1 flex flex-wrap gap-2">
                          {loc.authors.slice(0, 3).map((author) => <button key={author.author_id} type="button" className="text-muted-foreground hover:text-primary" onClick={() => setSelectedAuthorId(author.author_id)}>{author.name_zh || author.name}</button>)}
                          <button type="button" className="text-primary" onClick={() => { setMobileView("map"); setFlyTo([loc.lat, loc.lng]) }}>地图定位</button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
          <div ref={mobileCityLoadRef} className="py-2 text-center text-[11px] text-muted-foreground">
            {visibleMobileCities < cityGroups.length ? "继续滚动加载" : cityGroups.length ? null : "没有匹配的作者或单位"}
          </div>
        </div>
      </div>

      {/* Mobile status bar: view, summary and filters stay reachable without covering content. */}
      <div data-bottom-bar className="md:hidden shrink-0 border-t bg-background px-2 pt-1.5 pb-[max(0.5rem,env(safe-area-inset-bottom))] space-y-1.5">
        <div className="flex items-center justify-between px-1 text-[11px] text-muted-foreground">
          <span>{country || "国家/地区"}{province ? ` · ${province}` : ""}{city ? ` · ${city}` : ""}</span>
          <span>{stats ? `${stats.total_authors} 位作者 · ${stats.total_papers_mapped} 篇论文` : ""}</span>
        </div>
        <div className="grid grid-cols-[1fr_1fr_auto] gap-1.5">
          <button type="button" onClick={() => setMobileView("list")} className={`h-9 rounded-lg border text-xs inline-flex items-center justify-center gap-1.5 ${mobileView === "list" ? "bg-primary text-primary-foreground border-primary" : "bg-background"}`}><List className="w-3.5 h-3.5" />机构列表</button>
          <button type="button" onClick={() => setMobileView("map")} className={`h-9 rounded-lg border text-xs inline-flex items-center justify-center gap-1.5 ${mobileView === "map" ? "bg-primary text-primary-foreground border-primary" : "bg-background"}`}><Map className="w-3.5 h-3.5" />地图</button>
          <button type="button" onClick={() => setShowMobileFilter(true)} className="h-9 w-10 rounded-lg border inline-flex items-center justify-center" aria-label="筛选"><Filter className="w-4 h-4" /></button>
        </div>
      </div>

      {showMobileFilter && (
        <>
          <button type="button" className="md:hidden fixed inset-0 z-30 bg-black/30" onClick={() => setShowMobileFilter(false)} aria-label="关闭筛选" />
          <div className="md:hidden fixed inset-x-3 bottom-[max(0.75rem,env(safe-area-inset-bottom,0px))] z-40 max-h-[calc(100dvh-4rem)] overflow-y-auto overscroll-contain rounded-xl border bg-card shadow-xl p-3 space-y-3">
            <div className="flex items-center justify-between"><span className="text-sm font-semibold">作者筛选</span><button type="button" className="h-8 w-8 rounded-lg inline-flex items-center justify-center hover:bg-accent" onClick={() => setShowMobileFilter(false)} aria-label="关闭"><X className="w-4 h-4" /></button></div>
            <input
              type="search"
              aria-label="搜索作者与单位"
              placeholder="搜索作者或单位（中英文）"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-9 w-full rounded-lg border bg-background px-2.5 text-xs"
            />
            <div className="grid grid-cols-3 gap-1.5">
              <select value={country} onChange={(e) => { setCountry(e.target.value); setProvince(""); setCity(""); setSelectedCity(null) }} className="h-9 min-w-0 text-xs border rounded-lg px-2 bg-background">
                {COUNTRY_OPTIONS.map((item) => <option key={item} value={item}>{item}</option>)}
                <option value="">国家/地区</option>
              </select>
              <select value={province} onChange={(e) => { setProvince(e.target.value); setCity(""); setSelectedCity(null) }} className="h-9 min-w-0 text-xs border rounded-lg px-2 bg-background">
                <option value="">省份</option>
                {(provincesData?.provinces || []).map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
              <select value={city} onChange={(e) => { setCity(e.target.value); setSelectedCity(null) }} className="h-9 min-w-0 text-xs border rounded-lg px-2 bg-background">
                <option value="">城市</option>
                {(citiesData?.cities || []).map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </div>
            <div className="flex items-center justify-between rounded-lg bg-muted/40 px-3 py-2">
              <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={showNetwork} onChange={(e) => setShowNetwork(e.target.checked)} className="accent-primary" />合作网络</label>
              <button type="button" onClick={handleExport} disabled={isExporting} className="text-xs text-primary disabled:opacity-50"><Download className="w-3.5 h-3.5 inline mr-1" />{isExporting ? "导出中…" : "导出"}</button>
            </div>
            {stats && <div className="grid grid-cols-4 gap-1 text-center text-[10px] text-muted-foreground"><span>作者<br /><b className="text-foreground text-sm">{stats.total_authors}</b></span><span>论文<br /><b className="text-foreground text-sm">{stats.total_papers_mapped}</b></span><span>城市<br /><b className="text-foreground text-sm">{stats.total_cities}</b></span><span>机构<br /><b className="text-foreground text-sm">{stats.total_institutions}</b></span></div>}
            <button type="button" data-bottom-bar onClick={() => setShowMobileFilter(false)} className="sticky bottom-0 z-10 -mx-3 -mb-3 w-[calc(100%+1.5rem)] h-12 border-t bg-card px-3 pb-3 pt-2 text-xs text-primary">完成</button>
          </div>
        </>
      )}

      {selectedAuthorId != null && (
        <>
          <button type="button" className="fixed inset-0 z-40 bg-black/30" onClick={() => setSelectedAuthorId(null)} aria-label="关闭作者详情" />
          <aside className="fixed inset-y-0 right-0 z-50 w-full max-w-lg overflow-y-auto border-l bg-background shadow-2xl">
            <div className="sticky top-0 z-10 flex items-center justify-between border-b bg-background/95 px-4 py-3 backdrop-blur">
              <h2 className="font-semibold">作者与论文</h2>
              <button type="button" className="h-9 w-9 rounded-lg hover:bg-accent inline-flex items-center justify-center" onClick={() => setSelectedAuthorId(null)} aria-label="关闭"><X className="w-4 h-4" /></button>
            </div>
            {isAuthorProfileError ? (
              <QueryErrorNotice
                className="m-4 p-4"
                message={`作者详情加载失败：${getApiErrorMessage(authorProfileError, "请稍后重试")}`}
                onRetry={() => { void refetchAuthorProfile() }}
                retrying={isAuthorProfileFetching}
              />
            ) : isAuthorProfileLoading || !authorProfile ? <div className="p-8 flex justify-center"><Loader2 className="w-6 h-6 animate-spin" /></div> : (
              <div className="p-4 space-y-5">
                <section>
                  <h3 className="text-lg font-semibold">{authorProfile.author.name_zh || authorProfile.author.name}</h3>
                  {authorProfile.author.name_zh && <p className="text-sm text-muted-foreground">{authorProfile.author.name}</p>}
                  <p className="mt-2 text-sm">{authorProfile.author.affiliation_zh || authorProfile.author.canonical_affiliation || authorProfile.author.affiliation || "单位待补充"}</p>
                  <p className="text-xs text-muted-foreground">{[authorProfile.author.province, authorProfile.author.city].filter(Boolean).join(" · ")}</p>
                </section>
                <section className="space-y-3">
                  <h4 className="text-sm font-semibold">论文列表</h4>
                  {authorProfile.papers.map((paper) => (
                    <article key={paper.paper_id} className="rounded-xl border p-3 space-y-2">
                      <div className="flex items-start justify-between gap-2">
                        <a href={`/papers?q=${encodeURIComponent(String(paper.internal_id ?? paper.title))}&entity_label=&is=`} className="text-sm font-medium hover:text-primary hover:underline">{paper.title_zh || paper.title}</a>
                        {paper.internal_id != null && <span className="shrink-0 text-xs text-muted-foreground">#{paper.internal_id}</span>}
                      </div>
                      <div className="flex gap-1.5 text-[11px] text-muted-foreground">{paper.year && <span>{paper.year}</span>}{paper.is_first && <span>第一作者</span>}{paper.is_corresponding && <span>通讯作者</span>}</div>
                    </article>
                  ))}
                </section>
              </div>
            )}
          </aside>
        </>
      )}
      {showGeoReview && (
        <>
          <button type="button" className="fixed inset-0 z-40 bg-black/30" onClick={() => setShowGeoReview(false)} aria-label="关闭地理审核" />
          <aside className="fixed inset-y-0 right-0 z-50 w-full max-w-2xl overflow-y-auto border-l bg-background shadow-2xl">
            <div className="sticky top-0 z-10 flex items-center justify-between border-b bg-background/95 px-4 py-3 backdrop-blur"><div><h2 className="font-semibold">机构地理信息审核</h2><p className="text-xs text-muted-foreground">当前值与地图供应商冲突时，选择可信候选；不会自动采用未确认结果</p></div><button type="button" className="h-9 w-9 rounded-lg hover:bg-accent inline-flex items-center justify-center" onClick={() => setShowGeoReview(false)}><X className="w-4 h-4" /></button></div>
            {isGeoReviewError ? (
              <QueryErrorNotice
                className="m-4 p-4"
                message={`机构地理审核数据加载失败：${getApiErrorMessage(geoReviewError, "请稍后重试")}`}
                onRetry={() => { void refetchGeoReview() }}
                retrying={isGeoReviewFetching}
              />
            ) : isGeoReviewLoading ? <div className="p-8 flex justify-center"><Loader2 className="w-6 h-6 animate-spin" /></div> : <div className="p-4 space-y-3">
              <p className="text-xs text-muted-foreground">共 {geoReview?.total || 0} 个待审核机构</p>
              {(geoReview?.items || []).map((item) => <article key={item.affiliation_location_id} className="rounded-xl border p-3 space-y-2"><div><h3 className="text-sm font-medium">{item.affiliation}</h3><p className="text-xs text-muted-foreground">当前：{[item.current_province, item.current_city].filter(Boolean).join(" · ") || "未知"}</p></div><div className="space-y-2">{item.candidates.map((candidate) => <div key={candidate.provider} className="flex items-start justify-between gap-3 rounded-lg bg-muted/40 p-2.5"><div className="min-w-0"><p className="text-xs font-medium uppercase">{candidate.provider} <span className="font-normal text-muted-foreground">置信度 {candidate.provider_confidence != null ? `${Math.round(candidate.provider_confidence * 100)}%` : "—"}</span></p><p className="text-sm truncate">{candidate.result_name || "未返回机构名"}</p><p className="text-xs text-muted-foreground">{[candidate.province, candidate.city, candidate.district].filter(Boolean).join(" · ") || "位置未知"}</p>{candidate.conflict_reason && <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">{candidate.conflict_reason}</p>}</div><button type="button" disabled={acceptGeoCandidate.isPending || !candidate.city} onClick={() => acceptGeoCandidate.mutate({ locationId: item.affiliation_location_id, provider: candidate.provider })} className="shrink-0 rounded-lg border bg-background px-2.5 py-1.5 text-xs hover:bg-accent disabled:opacity-40">采用</button></div>)}</div></article>)}
            </div>}
          </aside>
        </>
      )}
      </div>
    </div>
  )
}
