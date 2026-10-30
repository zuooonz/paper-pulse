const cookieCredential = {
  credentialType: "cookie",
  credentialLabel: "Cookie",
  credentialDescription: "从浏览器 F12 → Application → Cookies 复制全部",
} as const

export const ARTICLE_CHANNELS = {
  csdn: {
    name: "CSDN",
    ...cookieCredential,
  },
  zhihu: {
    name: "知乎",
    ...cookieCredential,
  },
  baijiahao: {
    name: "百家号",
    ...cookieCredential,
  },
  xiaohongshu: {
    name: "小红书",
    ...cookieCredential,
  },
  sohu: {
    name: "搜狐号",
    ...cookieCredential,
  },
  bilibili: {
    name: "哔哩哔哩",
    ...cookieCredential,
  },
} as const

export const CHANNEL_NAMES: Record<string, string> = {
  ...Object.fromEntries(Object.entries(ARTICLE_CHANNELS).map(([key, metadata]) => [key, metadata.name])),
}

export const VIDEO_CHANNEL_NAMES: Record<string, string> = {
  douyin: "抖音",
  kuaishou: "快手",
}

export function channelName(channel: string): string {
  return CHANNEL_NAMES[channel] ?? channel
}

export function videoChannelName(channel: string): string {
  return VIDEO_CHANNEL_NAMES[channel] ?? channelName(channel)
}
