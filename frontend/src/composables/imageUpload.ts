import { useLocale } from '@/composables/useLocale'
const { t } = useLocale()
/**
 * 把 File 转 data URL（base64）；超过限制自动按比例缩放后再编码。
 */
const DEFAULT_MAX_BYTES = 8 * 1024 * 1024 // 8MB

export interface ReadOptions {
  maxBytes?: number
  /** 最大边长（像素）。设了之后会用 canvas 缩放再编码为 JPEG/PNG */
  maxEdge?: number
}

export async function fileToDataUrl(file: File, opts: ReadOptions = {}): Promise<string> {
  const maxBytes = opts.maxBytes ?? DEFAULT_MAX_BYTES
  if (!file.type.startsWith('image/')) {
    throw new Error(t('feedback.imagesOnly'))
  }

  // 如果文件不大也没指定缩放，直接 base64
  if (file.size <= maxBytes && !opts.maxEdge) {
    return await readAsDataUrl(file)
  }

  // 否则用 Canvas 缩放
  const dataUrl = await readAsDataUrl(file)
  return await shrinkDataUrl(dataUrl, {
    maxEdge: opts.maxEdge ?? 1280,
    maxBytes,
    mime: file.type === 'image/png' ? 'image/png' : 'image/jpeg',
  })
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error(t('feedback.readImage')))
    reader.readAsDataURL(file)
  })
}

async function shrinkDataUrl(
  dataUrl: string,
  opts: { maxEdge: number; maxBytes: number; mime: string },
): Promise<string> {
  const img = await loadImage(dataUrl)
  const { width, height } = img
  const ratio = Math.min(1, opts.maxEdge / Math.max(width, height))
  const w = Math.round(width * ratio)
  const h = Math.round(height * ratio)
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error(t('feedback.canvasUnavailable'))
  ctx.drawImage(img, 0, 0, w, h)

  // 尝试逐次降低 quality 直到符合 maxBytes
  let quality = 0.92
  for (let i = 0; i < 6; i++) {
    const url = canvas.toDataURL(opts.mime, quality)
    if (estimateBase64Bytes(url) <= opts.maxBytes) return url
    quality -= 0.12
    if (quality < 0.3) break
  }
  return canvas.toDataURL(opts.mime, 0.3)
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error(t('feedback.decodeImage')))
    img.src = src
  })
}

function estimateBase64Bytes(dataUrl: string): number {
  const idx = dataUrl.indexOf(',')
  if (idx < 0) return 0
  const b64 = dataUrl.slice(idx + 1)
  // 每 4 个 base64 字符表示 3 个字节
  return Math.floor((b64.length * 3) / 4)
}

/** 从 ClipboardEvent 中找图片文件（粘贴截图） */
export function extractImagesFromClipboard(event: ClipboardEvent): File[] {
  const items = Array.from(event.clipboardData?.items || [])
  const files: File[] = []
  for (const item of items) {
    if (item.kind === 'file') {
      const f = item.getAsFile()
      if (f && f.type.startsWith('image/')) files.push(f)
    }
  }
  return files
}
