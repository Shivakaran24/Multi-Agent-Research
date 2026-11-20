import { test } from 'node:test'
import assert from 'node:assert/strict'
import { composeTopic, DEFAULT_OPTIONS, fromStoredOptions, hasCustomOptions, optionsProblem, parseLinks, toRequestOptions, toStoredOptions } from '../src/options.ts'
import { formatTokens } from '../src/format.ts'

test('parseLinks splits on whitespace and drops duplicates', () => {
  assert.deepEqual(parseLinks(' https://a.example/x \n https://b.example\nhttps://a.example/x '), ['https://a.example/x', 'https://b.example'])
  assert.deepEqual(parseLinks('  \n '), [])
})

test('optionsProblem reports what the user can fix', () => {
  assert.equal(optionsProblem(DEFAULT_OPTIONS), '')
  assert.match(optionsProblem({ ...DEFAULT_OPTIONS, links: 'ftp://nope' }), /http/)
  assert.match(optionsProblem({ ...DEFAULT_OPTIONS, links: Array.from({ length: 6 }, (_, i) => `https://x${i}.example`).join('\n') }), /at most 5/)
  assert.match(optionsProblem({ ...DEFAULT_OPTIONS, compare: true, compareWith: ' ' }), /second topic/)
  assert.match(optionsProblem({ ...DEFAULT_OPTIONS, language: '<b>' }), /Language/)
  assert.equal(optionsProblem({ ...DEFAULT_OPTIONS, language: '' }), '')
})

test('comparison mode builds one topic and forces the comparison style', () => {
  const o = { ...DEFAULT_OPTIONS, compare: true, compareWith: ' pumped hydro ', style: 'executive' as const }
  assert.equal(composeTopic(' grid batteries ', o), 'Compare grid batteries versus pumped hydro')
  assert.equal(toRequestOptions(o).style, 'comparison')
  assert.equal(composeTopic(' grid batteries ', DEFAULT_OPTIONS), 'grid batteries')
})

test('request options match the API field names', () => {
  const sent = toRequestOptions({ ...DEFAULT_OPTIONS, timeRange: 'year', links: 'https://a.example', notes: ' focus on Spain ', language: ' Hindi ', verify: false })
  assert.deepEqual(sent, {
    style: 'brief', audience: 'general', length: 'standard', language: 'Hindi',
    time_range: 'year', urls: ['https://a.example'], notes: 'focus on Spain', documents: [], verify_claims: false,
  })
  assert.equal(toRequestOptions(DEFAULT_OPTIONS).time_range, null)
  assert.equal(hasCustomOptions(DEFAULT_OPTIONS), false)
  assert.equal(hasCustomOptions({ ...DEFAULT_OPTIONS, length: 'long' }), true)
})

test('formatTokens is compact', () => {
  assert.deepEqual([0, 950, 1000, 18400, 1250000].map(formatTokens), ['0', '950', '1k', '18.4k', '1.25M'])
})

test('uploaded documents are sent with a run but never stored in templates', () => {
  const o = { ...DEFAULT_OPTIONS, documents: [{ name: 'a.pdf', text: 'hello' }] }
  assert.deepEqual(toRequestOptions(o).documents, [{ name: 'a.pdf', text: 'hello' }])
  assert.ok(!('documents' in toStoredOptions(o)))
  assert.match(optionsProblem({ ...DEFAULT_OPTIONS, documents: Array.from({ length: 4 }, (_, i) => ({ name: `${i}.pdf`, text: 'x' })) }), /at most 3/)
})

test('a stored template round-trips into composer options', () => {
  const original = { ...DEFAULT_OPTIONS, style: 'executive' as const, audience: 'technical' as const, length: 'long' as const, language: 'Hindi', timeRange: 'year' as const, links: 'https://a.example', notes: 'n', verify: false }
  assert.deepEqual(fromStoredOptions(toStoredOptions(original)), original)
  assert.deepEqual(fromStoredOptions(null), DEFAULT_OPTIONS)
  assert.equal(fromStoredOptions({ time_range: 'week' as never }).timeRange, '')
})
