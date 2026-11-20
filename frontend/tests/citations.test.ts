import { test } from 'node:test'
import assert from 'node:assert/strict'
import { hostOf, isWebUrl, linkCitations, splitReport } from '../src/citations.ts'

const REPORT = '# Title\n\nCosts fell [1] and rose [2] but [9] is unknown.\n\n## Sources\n\n[1] IEA: Storage — https://www.iea.org/a\n[2] Ember (2025) — https://ember-energy.org/b?x=(1)\n'

test('splitReport separates the body from numbered sources', () => {
  const { body, sources } = splitReport(REPORT)
  assert.ok(body.includes('Costs fell [1]') && !body.includes('## Sources'))
  assert.deepEqual(sources.map(s => [s.n, s.title]), [[1, 'IEA: Storage'], [2, 'Ember (2025)']])
  assert.deepEqual(splitReport('no sources here'), { body: 'no sources here', sources: [] })
})

test('linkCitations links known numbers only and escapes parentheses', () => {
  const { body, sources } = splitReport(REPORT)
  const linked = linkCitations(body, sources)
  assert.ok(linked.includes('[[1]](https://www.iea.org/a)'))
  assert.ok(linked.includes('[[2]](https://ember-energy.org/b?x=%281%29)'))
  assert.ok(linked.includes('[9]') && !linked.includes('[[9]]'))
})

test('hostOf strips www and tolerates bad input', () => {
  assert.equal(hostOf('https://www.iea.org/a'), 'iea.org')
  assert.equal(hostOf('not a url'), '')
})

test('uploaded documents are listed as sources but never linked', () => {
  const { body, sources } = splitReport('Spain cut costs [1] and [2].\n\n## Sources\n\n[1] Spain report.pdf — upload:Spain report.pdf\n[2] IEA — https://www.iea.org/a\n')
  assert.deepEqual(sources.map(s => s.url), ['upload:Spain report.pdf', 'https://www.iea.org/a'])
  const linked = linkCitations(body, sources)
  assert.ok(linked.includes('[1]') && !linked.includes('[[1]]') && linked.includes('[[2]](https://www.iea.org/a)'))
  assert.equal(hostOf('upload:Spain report.pdf'), 'uploaded file')
  assert.deepEqual([isWebUrl('https://x.example'), isWebUrl('upload:a.pdf')], [true, false])
})
