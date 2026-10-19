import test from 'node:test'
import assert from 'node:assert/strict'
import {parseEnv, serializeEnv, validateValues} from '../.vitepress/theme/env-file.mjs'
const field = key => ({key, default:'', type:'str', text:{en:{group:'Test',description:'Example'},zh:{group:'测试',description:'示例'}}})
test('dotenv round-trip preserves keys, URLs, quotes, backslashes and multiline values', () => {
  const values = {KEY:'a # b " c \' d \\ e\nnext\r\t${LITERAL}', URL:'https://example.com/v1', EMPTY:''}
  const text = serializeEnv(values, Object.keys(values).map(field), 'en', 'Example')
  assert.deepEqual({...parseEnv(text)}, values)
})
test('imports BOM, export, comments and multiline quotes; duplicate variables use last value', () => {
  assert.deepEqual({...parseEnv('\uFEFFexport key = "first" # note\nkey=last # note\nPROMPT=\'line\none\'\nHASH=x#y\n')}, {KEY:'last',PROMPT:'line\none',HASH:'x#y'})
})
test('rejects malformed dotenv without executing or silently dropping lines', () => {
  for (const text of ['A="unfinished', 'not a variable', 'A="x" trailing', 'A=\0']) assert.throws(() => parseEnv(text))
  assert.equal(parseEnv('A=$(do-not-execute)').A, '$(do-not-execute)')
  assert.equal(parseEnv('__proto__=safe').__PROTO__, 'safe')
})
test('unknown variables survive export and cannot inject new assignments', () => {
  const values={KEY:'safe',EXTRA:'\nINJECTED=true'}
  assert.deepEqual({...parseEnv(serializeEnv(values,[field('KEY')],'zh','模板'))}, values)
})
test('validates numeric constraints, enums and related fields', () => {
  const schema=[{...field('COUNT'),type:'int',ge:1},{...field('LANG'),options:['en','zh-CN']}]
  assert.deepEqual(validateValues({COUNT:'1',LANG:'en'},schema),[])
  assert.deepEqual(validateValues({COUNT:'NaN',LANG:'xx',WINDOW_SIZE:'12',WINDOW_OVERLAP:'12'},schema),['COUNT','LANG','WINDOW_OVERLAP'])
})
