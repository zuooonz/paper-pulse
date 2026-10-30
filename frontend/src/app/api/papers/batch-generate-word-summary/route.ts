import { NextRequest } from "next/server"

export const dynamic = "force-dynamic"
export const maxDuration = 600

const BACKEND_URL = process.env.BACKEND_API_URL || "http://127.0.0.1:8000"

export async function POST(request: NextRequest) {
  const authorization = request.headers.get("authorization")
  const body = await request.text()

  try {
    const upstream = await fetch(
      `${BACKEND_URL}/api/papers/batch-generate-word-summary`,
      {
        method: "POST",
        headers: {
          "content-type": request.headers.get("content-type") || "application/json",
          ...(authorization ? { authorization } : {}),
        },
        body,
        cache: "no-store",
      },
    )

    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") || "application/json",
        "cache-control": "no-store",
      },
    })
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    return Response.json(
      { detail: `论文总结服务连接失败: ${message}` },
      { status: 502 },
    )
  }
}
