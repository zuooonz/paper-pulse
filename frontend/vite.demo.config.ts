import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

const root = path.resolve(__dirname, "demo")
const src = path.resolve(root, "../src")

export default defineConfig({
  root,
  base: process.env.DEMO_BASE || "/",
  plugins: [react()],
  resolve: {
    alias: [
      { find: /^@\/lib\/api$/, replacement: path.resolve(root, "api-mock.ts") },
      { find: /^next\/navigation$/, replacement: path.resolve(root, "stubs/next-navigation.ts") },
      { find: /^next-auth\/react$/, replacement: path.resolve(root, "stubs/next-auth-react.tsx") },
      { find: /^next\/link$/, replacement: path.resolve(root, "stubs/next-link.tsx") },
      { find: /^next\/image$/, replacement: path.resolve(root, "stubs/next-image.tsx") },
      { find: /^next\/dynamic$/, replacement: path.resolve(root, "stubs/next-dynamic.tsx") },
      { find: /^@\//, replacement: `${src}/` },
    ],
  },
  define: {
    "process.env.NEXT_PUBLIC_TARGET_LABEL": JSON.stringify(""),
    "process.env.NEXT_PUBLIC_APP_TIMEZONE": JSON.stringify("Asia/Shanghai"),
    "process.env.NEXT_PUBLIC_API_URL": JSON.stringify(""),
  },
  css: { postcss: path.resolve(root, "../postcss.config.mjs") },
  build: { outDir: path.resolve(root, "../demo-dist"), emptyOutDir: true },
})
