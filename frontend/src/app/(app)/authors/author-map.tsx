"use client"

import { useEffect, useRef, useState } from "react"
import { useTheme } from "next-themes"
import type * as Leaflet from "leaflet"
import type {} from "leaflet.markercluster"
import "leaflet/dist/leaflet.css"
import "leaflet.markercluster/dist/MarkerCluster.css"
import "leaflet.markercluster/dist/MarkerCluster.Default.css"

// ── Types ────────────────────────────────────────────────────────────

interface AuthorBrief {
  author_id: number
  name: string
  name_zh: string
  role: "first_author" | "corresponding" | "co_author"
}

interface LocationData {
  affiliation: string
  city: string
  province: string
  lat: number
  lng: number
  author_count: number
  paper_count: number
  authors: AuthorBrief[]
}

interface NetworkEdge {
  source: string
  target: string
  weight: number
  source_lat: number
  source_lng: number
  target_lat: number
  target_lng: number
}

// ── Tile configs ──────────────────────────────────────────────────────

const TILES = {
  light: {
    url: "https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
    attribution: '&copy; <a href="https://www.amap.com/">高德地图</a>',
    subdomains: ["1", "2", "3", "4"],
    maxZoom: 18,
  },
  dark: {
    url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    attribution: '&copy; <a href="https://carto.com/">CARTO</a>',
    subdomains: ["a", "b", "c", "d"],
    maxZoom: 18,
  },
}

// ── Helpers ───────────────────────────────────────────────────────────

function makeIcon(size: number, L: typeof import("leaflet")) {
  return L.divIcon({
    className: "custom-marker",
    html: `<div style="
      width:${size}px;height:${size}px;
      background:var(--primary);border:2px solid white;
      border-radius:50%;box-shadow:0 2px 6px rgba(0,0,0,.3);
      display:flex;align-items:center;justify-content:center;
      color:white;font-size:${size > 20 ? 10 : 8}px;font-weight:700;
    ">${size > 20 ? "●" : ""}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  })
}

function buildPopupHTML(loc: LocationData, collabCount: number): string {
  const authorList = loc.authors
    .filter((a) => a.role !== "co_author")
    .slice(0, 10)
    .map(
      (a) =>
        `<div style="font-size:12px;display:flex;align-items:center;gap:4px;margin-bottom:2px">` +
        `<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${
          a.role === "first_author" ? "var(--primary)" : a.role === "corresponding" ? "#22c55e" : "#d1d5db"
        }"></span>` +
        `${a.name_zh || a.name}` +
        (a.role === "first_author" ? '<span style="font-size:10px;color:var(--primary)"> 第一</span>' : "") +
        (a.role === "corresponding" ? '<span style="font-size:10px;color:#22c55e"> 通讯</span>' : "") +
        `</div>`
    )
    .join("")

  const overflow = loc.authors.filter((a) => a.role !== "co_author").length > 10
    ? `<div style="font-size:12px;color:#6b7280">...及其他 ${loc.author_count - 10} 位作者</div>`
    : ""

  const collab = collabCount > 0
    ? `<div style="font-size:11px;color:var(--primary);margin-top:4px">🔗 ${collabCount} 个合作机构</div>`
    : ""

  return (
    `<div style="font-family:system-ui,sans-serif;max-width:320px">` +
    `<div style="font-weight:600;margin-bottom:4px">${loc.affiliation}</div>` +
    `<div style="font-size:12px;color:#6b7280;margin-bottom:8px">${loc.city} · ${loc.paper_count} 篇 · ${loc.author_count} 人</div>` +
    `<div style="max-height:200px;overflow-y:auto">${authorList}${overflow}</div>${collab}` +
    `</div>`
  )
}

function makeArc(
  L: typeof import("leaflet"),
  p1: [number, number],
  p2: [number, number],
): Leaflet.Polyline {
  const midLat = (p1[0] + p2[0]) / 2
  const midLng = (p1[1] + p2[1]) / 2
  const dist = Math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)
  const curvature = Math.min(dist * 0.25, 8)
  const ctrlLat = midLat + curvature
  const ctrlLng = midLng

  const steps = 16
  const points: [number, number][] = []
  for (let i = 0; i <= steps; i++) {
    const t = i / steps
    const lat = (1 - t) ** 2 * p1[0] + 2 * (1 - t) * t * ctrlLat + t ** 2 * p2[0]
    const lng = (1 - t) ** 2 * p1[1] + 2 * (1 - t) * t * ctrlLng + t ** 2 * p2[1]
    points.push([lat, lng])
  }

  return L.polyline(points, {
    color: "#ff0000",
    weight: 3,
    opacity: 0.8,
    interactive: false,
    bubblingMouseEvents: false,
    smoothFactor: 1,
  })
}

// ── Map ──────────────────────────────────────────────────────────────

export default function AuthorMap({
  locations,
  edges,
  flyTo,
}: {
  locations: LocationData[]
  edges: NetworkEdge[]
  flyTo: [number, number] | null
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<Leaflet.Map | null>(null)
  const tileRef = useRef<Leaflet.TileLayer | null>(null)
  const clusterRef = useRef<Leaflet.MarkerClusterGroup | null>(null)
  const arcLayerRef = useRef<Leaflet.FeatureGroup | null>(null)
  const leafletRef = useRef<typeof import("leaflet") | null>(null)
  const [mapReady, setMapReady] = useState(false)
  const { resolvedTheme } = useTheme()

  // Init map once
  useEffect(() => {
    let cancelled = false
    let resizeObserver: ResizeObserver | null = null

    const initMap = async () => {
      const leafletModule = await import("leaflet")
      await import("leaflet.markercluster")
      if (cancelled) return

      const L = leafletModule.default ?? leafletModule
      const container = containerRef.current
      if (!container || mapRef.current) return
      if ((container as HTMLDivElement & { _leaflet_id?: number })._leaflet_id != null) return

      const map = L.map(container, {
        center: [35.86, 104.19],
        zoom: 5,
        scrollWheelZoom: true,
      })

      const cfg = resolvedTheme === "dark" ? TILES.dark : TILES.light
      const tile = L.tileLayer(cfg.url, {
        attribution: cfg.attribution,
        subdomains: cfg.subdomains,
        maxZoom: cfg.maxZoom,
      }).addTo(map)
      tileRef.current = tile

      const cluster = L.markerClusterGroup({
        chunkedLoading: true,
        maxClusterRadius: 60,
        spiderfyOnMaxZoom: true,
        showCoverageOnHover: false,
        zoomToBoundsOnClick: true,
        iconCreateFunction: (clst) => {
          const count = clst.getChildCount()
          let size = 36
          let color = "var(--primary)"
          if (count >= 100) { size = 48; color = "var(--primary)" }
          else if (count >= 20) { size = 42; color = "var(--primary)" }
          return L.divIcon({
            html: `<div style="
              width:${size}px;height:${size}px;
              background:${color};border:3px solid white;
              border-radius:50%;box-shadow:0 2px 8px rgba(0,0,0,.4);
              display:flex;align-items:center;justify-content:center;
              color:white;font-size:${size > 40 ? 13 : 11}px;font-weight:700;
            ">${count}</div>`,
            className: "cluster-marker",
            iconSize: [size, size],
          })
        },
      })

      map.addLayer(cluster)
      leafletRef.current = L
      mapRef.current = map
      clusterRef.current = cluster

      resizeObserver = new ResizeObserver(() => map.invalidateSize({ pan: false }))
      resizeObserver.observe(container)
      setMapReady(true)
    }

    void initMap()

    return () => {
      cancelled = true
      resizeObserver?.disconnect()
      mapRef.current?.remove()
      mapRef.current = null
      clusterRef.current = null
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Switch tiles on theme change
  useEffect(() => {
    const tile = tileRef.current
    const map = mapRef.current
    if (!tile || !map) return
    const cfg = resolvedTheme === "dark" ? TILES.dark : TILES.light
    tile.setUrl(cfg.url)
    if (tile.options) {
      tile.options.subdomains = cfg.subdomains
      tile.options.attribution = cfg.attribution
    }
  }, [resolvedTheme, mapReady])

  // Compute collaboration count per institution
  const collabCounts = useRef<Record<string, number>>({})
  useEffect(() => {
    const counts: Record<string, number> = {}
    for (const e of edges) {
      counts[e.source] = (counts[e.source] || 0) + 1
      counts[e.target] = (counts[e.target] || 0) + 1
    }
    collabCounts.current = counts
  }, [edges])

  // Update markers
  useEffect(() => {
    const cluster = clusterRef.current
    if (!cluster) return
    const L = leafletRef.current
    if (!L) return
    cluster.clearLayers()

    locations.forEach((loc) => {
      const size = Math.max(12, Math.min(32, 10 + loc.paper_count * 0.6))
      const marker = L.marker([loc.lat, loc.lng], { icon: makeIcon(size, L) })
      const collab = collabCounts.current[loc.affiliation] || 0
      marker.bindPopup(buildPopupHTML(loc, collab), { maxWidth: 320 })
      cluster.addLayer(marker)
    })
  }, [locations, mapReady])

  // Update collaboration arcs
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const L = leafletRef.current
    if (!L) return

    if (arcLayerRef.current) {
      map.removeLayer(arcLayerRef.current)
    }

    console.log('[AuthorMap] edges count:', edges.length)

    if (edges.length === 0) return

    const group = L.featureGroup()
    let drawn = 0
    for (const e of edges) {
      if (e.source_lat && e.target_lat) {
        const arc = makeArc(L, [e.source_lat, e.source_lng], [e.target_lat, e.target_lng])
        group.addLayer(arc)
        drawn++
      }
    }
    console.log('[AuthorMap] arcs drawn:', drawn)
    group.addTo(map)
    arcLayerRef.current = group

    return () => {
      if (arcLayerRef.current) {
        map.removeLayer(arcLayerRef.current)
      }
    }
  }, [edges, mapReady])

  // Fly to
  useEffect(() => {
    if (!flyTo || !mapRef.current || !Number.isFinite(flyTo[0]) || !Number.isFinite(flyTo[1])) return
    const frame = window.requestAnimationFrame(() => {
      mapRef.current?.invalidateSize({ pan: false })
      mapRef.current?.flyTo(flyTo, 14, { duration: 0.8 })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [flyTo, mapReady])

  return <div ref={containerRef} className="relative z-0 w-full h-full" />
}
