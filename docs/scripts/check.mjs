import assert from 'node:assert/strict'
import { readFileSync, readdirSync, lstatSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import en from '../.vitepress/locales/en.mjs'
import zh from '../.vitepress/locales/zh-CN.mjs'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const pages = JSON.parse(readFileSync(resolve(root, 'pages.json'), 'utf8'))
function keys(value, prefix = '') {
  return Object.entries(value).flatMap(([key, child]) => child && typeof child === 'object'
    ? keys(child, `${prefix}${key}.`) : [`${prefix}${key}`]).sort()
}
assert.deepEqual(keys(en), keys(zh), 'Site locale keys must match')
assert.equal(new Set(pages.map(page => page.slug)).size, pages.length, 'Duplicate slugs')
assert.equal(new Set(pages.map(page => page.zh)).size, pages.length, 'Duplicate Chinese source names')
for (const page of pages) {
  assert.match(page.slug, /^[a-z][a-z-]*$/)
  assert.ok(en.pages[page.slug] && zh.pages[page.slug], `${page.slug}: missing navigation translation`)
  assert.ok(en.groups[page.group] && zh.groups[page.group], `${page.slug}: unknown group`)
}
for (const [directory, expected] of [['wiki', pages.map(p => `${p.zh}.md`)], ['en', pages.map(p => `${p.slug}.md`)]]) {
  const files = readdirSync(resolve(root, directory)).filter(name => !name.startsWith('_')).sort()
  assert.deepEqual(files, expected.sort(), `${directory}: every article must have a mapped counterpart`)
  assert.ok(!lstatSync(resolve(root, directory)).isSymbolicLink(), 'No source directory symlinks')
  for (const file of files) assert.ok(!lstatSync(resolve(root, directory, file)).isSymbolicLink(), 'No source symlinks')
}
const envKeys = text => [...text.matchAll(/^\| `([A-Z][A-Z0-9_]+)` \|/gm)].map(match => match[1]).sort()
assert.deepEqual(envKeys(readFileSync(resolve(root, 'en/environment.md'), 'utf8')),
  envKeys(readFileSync(resolve(root, 'wiki/后端环境变量.md'), 'utf8')), 'Environment reference fields must match')
// Generation checks paired sections, mapped relative pages and section links.
const result = spawnSync('python3', [resolve(root, '../.github/scripts/prepare_docs.py'), 'site'], {encoding: 'utf8'})
assert.equal(result.status, 0, result.stderr)
process.stdout.write(result.stdout)
console.log(`Validated ${pages.length} article pairs and site locale keys.`)
