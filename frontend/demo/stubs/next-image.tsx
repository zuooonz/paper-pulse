import type { ImgHTMLAttributes } from "react"

type ImageProps = ImgHTMLAttributes<HTMLImageElement> & {
  src: string
  width?: number
  height?: number
  unoptimized?: boolean
  priority?: boolean
}
export default function Image({ src, width, height, unoptimized: _unoptimized, priority: _priority, alt, ...props }: ImageProps) {
  return <img src={src} width={width} height={height} alt={alt || ""} {...props} />
}
