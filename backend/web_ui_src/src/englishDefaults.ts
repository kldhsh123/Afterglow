import type { Preset } from './api'

// Provider identifiers and endpoint paths are configuration data, not UI copy.
const OPENAI_URL = 'https://api.openai.com/v1'
const AUXILIARY_MODEL = 'gpt-4.1-mini'

interface ServiceFields {
  OPENAI_BASE_URL: string
  CHAT_MODEL: string
  EMBEDDING_API_URL: string
  EMBEDDING_MODEL: string
  EMBEDDING_DIM: number
  LABEL_API_URL: string
  LABEL_MODEL: string
  LIFE_API_URL: string
  LIFE_MODEL: string
  RESPONSE_POLICY_API_URL: string
  RESPONSE_POLICY_MODEL: string
  VISION_API_URL: string
  VISION_MODEL: string
  ADAPTIVE_CHUNK_API_URL: string
  ADAPTIVE_CHUNK_MODEL: string
}

export function applyEnglishDefaults(
  form: ServiceFields,
  configured: ReadonlySet<string>,
  google?: Pick<Preset, 'base_url' | 'default_model'>,
) {
  function fill(url: keyof ServiceFields, model: keyof ServiceFields, apiKey: string, baseUrl: string, modelName: string) {
    if (form[url] || form[model] || [url, model, apiKey].some(key => configured.has(key))) return false
    Object.assign(form, { [url]: baseUrl, [model]: modelName })
    return true
  }
  fill('OPENAI_BASE_URL', 'CHAT_MODEL', 'OPENAI_API_KEY',
    google?.base_url || 'https://generativelanguage.googleapis.com/v1beta/openai',
    google?.default_model || 'gemini-2.5-flash')
  if (fill('EMBEDDING_API_URL', 'EMBEDDING_MODEL', 'EMBEDDING_API_KEY', OPENAI_URL, 'text-embedding-3-small')
    && !configured.has('EMBEDDING_DIM') && form.EMBEDDING_DIM === 1024) {
    form.EMBEDDING_DIM = 1536
  }
  for (const prefix of ['LABEL', 'LIFE', 'RESPONSE_POLICY', 'VISION', 'ADAPTIVE_CHUNK'] as const) {
    fill(`${prefix}_API_URL`, `${prefix}_MODEL`, `${prefix}_API_KEY`, OPENAI_URL, AUXILIARY_MODEL)
  }
}
