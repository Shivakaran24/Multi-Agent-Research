import { test } from 'node:test'
import assert from 'node:assert/strict'
import { markdownToDocx } from '../src/docx.ts'

const text = (bytes: Uint8Array) => new TextDecoder().decode(bytes)

const MARKDOWN = `# Grid & storage <brief>

Intro with R&D, "quoted" and **bold**, *italic*, \`code\` and a [link](https://example.com/a?x=1&y=2).

## Steps

1. First
2. Second
   - nested

- Bullet one
- Bullet two

| A | B |
|---|---|
| 1 | 2 |

> Quoted

line one
line two
`

test('produces a ZIP with the parts Word needs', () => {
  const bytes = markdownToDocx(MARKDOWN)
  assert.equal(String.fromCharCode(bytes[0], bytes[1]), 'PK')
  const all = text(bytes)
  for (const part of ['[Content_Types].xml', '_rels/.rels', 'word/document.xml', 'word/styles.xml', 'word/numbering.xml', 'word/_rels/document.xml.rels']) {
    assert.ok(all.includes(part), `missing ${part}`)
  }
})

test('document XML carries headings, lists, tables, links and escapes text', () => {
  const all = text(markdownToDocx(MARKDOWN))
  assert.ok(all.includes('w:val="Heading1"') && all.includes('w:val="Heading2"'))
  assert.ok(all.includes('Grid &amp; storage ') && all.includes('&lt;brief&gt;'))
  assert.ok(!all.includes('&amp;amp;') && !all.includes('&amp;lt;'), 'text must not be escaped twice')
  assert.ok(all.includes('R&amp;D, &quot;quoted&quot; and'), 'ampersands and quotes appear once-escaped')
  assert.ok(all.includes('<w:b/>') && all.includes('<w:i/>'))
  assert.ok(all.includes('<w:tbl>') && all.includes('<w:hyperlink'))
  assert.ok(all.includes('Target="https://example.com/a?x=1&amp;y=2"'))
  assert.ok(all.includes('<w:numId w:val="1"/>'), 'bullets use the bullet list')
  assert.ok(all.includes('<w:numId w:val="2"/>'), 'ordered list gets its own numbering')
  assert.ok(all.includes('<w:br/>'), 'soft line breaks are kept')
})

test('only http(s) links become hyperlinks and an empty document is still valid', () => {
  const all = text(markdownToDocx('[bad](javascript:alert(1)) and [ok](https://ok.example)'))
  assert.ok(!all.includes('javascript:'))
  assert.equal((all.match(/<w:hyperlink /g) || []).length, 1)
  assert.equal(String.fromCharCode(...markdownToDocx('').slice(0, 2)), 'PK')
})
