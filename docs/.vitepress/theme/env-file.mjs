// Read dotenv syntax without executing shell commands or expanding variables.
export function parseEnv(source) {
  const result = Object.create(null)
  const lines = source.replace(/^\uFEFF/, '').replaceAll('\r\n', '\n').split('\n')
  for (let line = 0; line < lines.length; line++) {
    const text = lines[line].trimStart()
    if (!text || text.startsWith('#')) continue
    const match = text.match(/^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/)
    if (!match) throw Object.assign(new Error('syntax'), {line: line + 1})
    let value = match[2]
    if (/^["']/.test(value)) {
      const quote = value[0]
      let joined = value.slice(1), end = -1
      for (;;) {
        for (let i = 0; i < joined.length; i++) {
          if (joined[i] === '\\') { i++; continue }
          if (joined[i] === quote) { end = i; break }
        }
        if (end >= 0) break
        if (++line >= lines.length) throw Object.assign(new Error('quote'), {line: line + 1})
        joined += '\n' + lines[line]
      }
      if (!/^\s*(?:#.*)?$/.test(joined.slice(end + 1))) throw Object.assign(new Error('syntax'), {line: line + 1})
      value = joined.slice(0, end)
      const escapes = {'\\':'\\', "'":"'", '"':'"', a:'\x07', b:'\b', f:'\f', n:'\n', r:'\r', t:'\t', v:'\v'}
      value = quote === "'" ? value.replace(/\\([\\'])/g, (_, c) => c)
        : value.replace(/\\([\\'"abfnrtv])/g, (_, c) => escapes[c])
    } else value = value.replace(/\s+#.*$/, '').trimEnd()
    if (value.includes('\0')) throw Object.assign(new Error('null'), {line: line + 1})
    result[match[1].toUpperCase()] = value
  }
  return result
}

function quote(value) {
  if (String(value).includes('\0')) throw new Error('null')
  return '"' + String(value).replaceAll('\\', '\\\\').replaceAll('"', '\\"')
    .replaceAll('\n', '\\n').replaceAll('\r', '\\r').replaceAll('\t', '\\t') + '"'
}

export function serializeEnv(values, schema, locale, header) {
  const lines = ['# ' + header.replaceAll('\n', '\n# ')]
  let group = ''
  const known = new Set(schema.map(field => field.key))
  for (const field of schema) {
    const text = field.text[locale]
    if (text.group !== group) { group = text.group; lines.push('', '# ----- ' + group + ' -----') }
    lines.push('# ' + text.description.replaceAll('\n', '\n# '), `${field.key}=${quote(values[field.key] ?? field.default)}`)
  }
  for (const [key, value] of Object.entries(values)) {
    if (!known.has(key) && /^[A-Z_][A-Z0-9_]*$/.test(key)) lines.push(`${key}=${quote(value)}`)
  }
  return lines.join('\n') + '\n'
}

export function validateValues(values, schema) {
  const errors = []
  for (const field of schema) {
    const raw = values[field.key] ?? field.default
    const numeric = field.type === 'int' || field.type === 'float'
    const n = Number(raw)
    if ((numeric && (!String(raw).trim() || !Number.isFinite(n) || (field.type === 'int' && !Number.isInteger(n))))
      || (field.type === 'bool' && !['true', 'false'].includes(raw))
      || (field.options && !field.options.includes(raw))
      || String(raw).includes('\0')
      || (numeric && ['ge','gt','le','lt'].some(op => field[op] !== undefined && !({ge:n >= field[op], gt:n > field[op], le:n <= field[op], lt:n < field[op]})[op]))) errors.push(field.key)
  }
  if (Number(values.WINDOW_OVERLAP) >= Number(values.WINDOW_SIZE)) errors.push('WINDOW_OVERLAP')
  if (Number(values.ADAPTIVE_CHUNK_MAX_CHARS) < Number(values.ADAPTIVE_CHUNK_TARGET_CHARS)) errors.push('ADAPTIVE_CHUNK_MAX_CHARS')
  if (values.RELATIONSHIP_TYPE === 'custom' && !values.RELATIONSHIP_DESCRIPTION?.trim()) errors.push('RELATIONSHIP_DESCRIPTION')
  return [...new Set(errors)]
}
