import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({
  breaks: true, // 单换行就分段，更贴近聊天
  gfm: true,
})

// 匹配 AI 输出里 [sticker:名字] / [sticker=名字] 占位
const STICKER_TOKEN_RE = /\[sticker(?::|=)([^\]\s]+)\]/gi

function backendBaseUrl(): string {
  // 从 localStorage 取（与 settings store 同源），失败回退到空（走 Vite 代理）
  try {
    const raw = localStorage.getItem('xuwen.settings.v1')
    if (raw) {
      const parsed = JSON.parse(raw)
      if (typeof parsed.backendBaseUrl === 'string') {
        return parsed.backendBaseUrl
      }
    }
  } catch {
    /* 忽略读取失败 */
  }
  return ''
}

/** 把 [sticker:xxx] 替换成 <img> HTML 标签（在 markdown 之前做，避免被 marked 误解析）。 */
function replaceStickers(input: string): string {
  const base = backendBaseUrl().replace(/\/$/, '')
  return input.replace(STICKER_TOKEN_RE, (_match, name: string) => {
    const safeName = encodeURIComponent(name)
    const url = `${base}/v1/stickers/${safeName}/image`
    return (
      `<img src="${url}" alt="${name}" class="inline-block max-w-[180px] max-h-[180px] ` +
      `rounded-md align-middle my-1 shadow-letter" loading="lazy" />`
    )
  })
}

/** 把 LLM 回复的 markdown 渲染为安全 HTML（防 XSS）。
 *
 * 顺序：先替换 [sticker:xxx] → <img>，再交给 marked + DOMPurify。
 * DOMPurify 默认允许 <img>，所以表情包能保留下来。
 */
export function renderMarkdown(input: string): string {
  if (!input) return ''
  const withStickers = replaceStickers(input)
  const rawHtml = marked.parse(withStickers, { async: false }) as string
  return DOMPurify.sanitize(rawHtml, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['iframe', 'script', 'style'],
  })
}
