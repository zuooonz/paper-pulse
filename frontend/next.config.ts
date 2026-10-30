import type { NextConfig } from "next";

const backendApiUrl = process.env.BACKEND_API_URL || "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // Set LOW_MEMORY_BUILD=1 to skip the redundant TypeScript check in constrained builds.
  typescript: {
    ignoreBuildErrors: process.env.LOW_MEMORY_BUILD === "1",
  },
  experimental: {
    proxyClientMaxBodySize: "500mb",
    // Keep the build worker count configurable for constrained environments.
    cpus: 1,
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              "base-uri 'self'",
              "object-src 'none'",
              "frame-ancestors 'none'",
              "form-action 'self'",
              "img-src 'self' data: blob: https:",
              "media-src 'self' blob: https:",
              "font-src 'self' data:",
              "style-src 'self' 'unsafe-inline'",
              "script-src 'self' 'unsafe-inline'",
              "connect-src 'self' http: https: ws: wss:",
            ].join("; "),
          },
        ],
      },
    ]
  },
  async rewrites() {
    return [
      {
        source: "/api/drafts/:path*",
        destination: `${backendApiUrl}/api/drafts/:path*`,
      },
      {
        source: "/api/accounts/:path*",
        destination: `${backendApiUrl}/api/accounts/:path*`,
      },
      {
        source: "/api/governance/:path*",
        destination: `${backendApiUrl}/api/governance/:path*`,
      },
      {
        source: "/api/admin/governance/:path*",
        destination: `${backendApiUrl}/api/admin/governance/:path*`,
      },
      {
        source: "/api/papers/:path*",
        destination: `${backendApiUrl}/api/papers/:path*`,
      },
      {
        source: "/api/literature-search/:path*",
        destination: `${backendApiUrl}/api/literature-search/:path*`,
      },
      {
        source: "/api/authors/:path*",
        destination: `${backendApiUrl}/api/authors/:path*`,
      },
      {
        source: "/api/stats/:path*",
        destination: `${backendApiUrl}/api/stats/:path*`,
      },
      {
        source: "/api/videos/:path*",
        destination: `${backendApiUrl}/api/videos/:path*`,
      },
      {
        source: "/api/auth/dingtalk/:path*",
        destination: `${backendApiUrl}/api/auth/dingtalk/:path*`,
      },
      {
        source: "/api/auth/verify",
        destination: `${backendApiUrl}/api/auth/verify`,
      },
      {
        // Exact rewrite avoids NextAuth's /api/auth/[...nextauth] catch-all.
        source: "/api/auth/logout",
        destination: `${backendApiUrl}/api/auth/logout`,
      },
      {
        source: "/api/config/:path*",
        destination: `${backendApiUrl}/api/config/:path*`,
      },
      {
        source: "/api/temp-download/:path*",
        destination: `${backendApiUrl}/api/temp-download/:path*`,
      },
    ];
  },
};

export default nextConfig;
