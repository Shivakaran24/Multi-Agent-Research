import { marked, type Token, type Tokens } from 'marked'

/**
 * Builds a real Word (.docx) file in the browser from the brief's Markdown, with no extra
 * dependency: WordprocessingML parts packed into an uncompressed ZIP.
 */

const enc = new TextEncoder()
const esc = (text: string) => text.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

type Style = { b?: boolean; i?: boolean; code?: boolean; link?: boolean }

class Builder {
  rels: string[] = []
  nums: number[] = [] // ids of ordered-list instances, each restarting at 1

  run(text: string, style: Style = {}): string {
    if (!text) return ''
    const props = [
      style.link ? '<w:rStyle w:val="Hyperlink"/>' : '',
      style.code ? '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:sz w:val="20"/>' : '',
      style.b ? '<w:b/>' : '',
      style.i ? '<w:i/>' : '',
    ].join('')
    const content = text.split('\n').map(part => `<w:t xml:space="preserve">${esc(part)}</w:t>`).join('<w:br/>')
    return `<w:r>${props ? `<w:rPr>${props}</w:rPr>` : ''}${content}</w:r>`
  }

  inline(tokens: Token[] | undefined, style: Style = {}): string {
    return (tokens ?? []).map(token => {
      switch (token.type) {
        case 'strong': return this.inline((token as Tokens.Strong).tokens, { ...style, b: true })
        case 'em': return this.inline((token as Tokens.Em).tokens, { ...style, i: true })
        case 'codespan': return this.run((token as Tokens.Codespan).text, { ...style, code: true })
        case 'br': return '<w:r><w:br/></w:r>'
        case 'del': return this.inline((token as Tokens.Del).tokens, style)
        case 'link': {
          const link = token as Tokens.Link
          if (!/^https?:\/\//i.test(link.href)) return this.inline(link.tokens, style)
          this.rels.push(link.href)
          const id = `rIdL${this.rels.length}`
          return `<w:hyperlink r:id="${id}" w:history="1">${this.inline(link.tokens, { ...style, link: true })}</w:hyperlink>`
        }
        case 'text': {
          const t = token as Tokens.Text
          return t.tokens?.length ? this.inline(t.tokens, style) : this.run(t.text, style)
        }
        case 'escape': return this.run((token as Tokens.Escape).text, style)
        default: return this.run('raw' in token ? String(token.raw) : '', style)
      }
    }).join('')
  }

  para(runs: string, props = ''): string {
    return `<w:p>${props ? `<w:pPr>${props}</w:pPr>` : ''}${runs}</w:p>`
  }

  list(list: Tokens.List, level: number): string {
    let numId = 1
    if (list.ordered) {
      numId = 2 + this.nums.length
      this.nums.push(numId)
    }
    return list.items.map(item => {
      const out: string[] = []
      let first = true
      for (const child of item.tokens) {
        if (child.type === 'list') out.push(this.list(child as Tokens.List, Math.min(level + 1, 2)))
        else if (child.type === 'text' || child.type === 'paragraph') {
          const runs = this.inline((child as Tokens.Text).tokens ?? [child])
          out.push(this.para(runs, `<w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="${level}"/><w:numId w:val="${first ? numId : 0}"/></w:numPr>`))
          first = false
        } else out.push(this.block(child))
      }
      return out.join('')
    }).join('')
  }

  table(table: Tokens.Table): string {
    const cell = (c: Tokens.TableCell, head: boolean) =>
      `<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/>${head ? '<w:shd w:val="clear" w:color="auto" w:fill="EEF2EC"/>' : ''}</w:tcPr>${this.para(this.inline(c.tokens, head ? { b: true } : {}))}</w:tc>`
    const row = (cells: Tokens.TableCell[], head: boolean) => `<w:tr>${cells.map(c => cell(c, head)).join('')}</w:tr>`
    const border = ['top', 'left', 'bottom', 'right', 'insideH', 'insideV'].map(s => `<w:${s} w:val="single" w:sz="4" w:space="0" w:color="C9D1C6"/>`).join('')
    return `<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>${border}</w:tblBorders></w:tblPr>${row(table.header, true)}${table.rows.map(r => row(r, false)).join('')}</w:tbl>${this.para('')}`
  }

  block(token: Token): string {
    switch (token.type) {
      case 'heading': {
        const h = token as Tokens.Heading
        return this.para(this.inline(h.tokens), `<w:pStyle w:val="Heading${Math.min(h.depth, 3)}"/>`)
      }
      case 'paragraph': return this.para(this.inline((token as Tokens.Paragraph).tokens))
      case 'text': return this.para(this.inline((token as Tokens.Text).tokens ?? [token]))
      case 'list': return this.list(token as Tokens.List, 0)
      case 'blockquote': return (token as Tokens.Blockquote).tokens.map(t => t.type === 'paragraph' ? this.para(this.inline((t as Tokens.Paragraph).tokens), '<w:pStyle w:val="Quote"/>') : this.block(t)).join('')
      case 'code': return (token as Tokens.Code).text.split('\n').map(line => this.para(this.run(line, { code: true }), '<w:pStyle w:val="Code"/>')).join('')
      case 'hr': return this.para('', '<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="C9D1C6"/></w:pBdr>')
      case 'table': return this.table(token as Tokens.Table)
      case 'space': case 'html': return ''
      default: return ''
    }
  }
}

const NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
const XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'

const heading = (id: string, size: number, before: number, font = 'Georgia') =>
  `<w:style w:type="paragraph" w:styleId="${id}"><w:name w:val="heading ${id.slice(-1)}"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="${before}" w:after="120"/><w:outlineLvl w:val="${Number(id.slice(-1)) - 1}"/></w:pPr><w:rPr><w:rFonts w:ascii="${font}" w:hAnsi="${font}"/><w:b/><w:color w:val="2D4A38"/><w:sz w:val="${size}"/></w:rPr></w:style>`

const STYLES = `${XML}<w:styles ${NS}><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="22"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="140" w:line="288" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
${heading('Heading1', 40, 0)}${heading('Heading2', 30, 320)}${heading('Heading3', 25, 240)}
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="60"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:pPr><w:pBdr><w:left w:val="single" w:sz="12" w:space="8" w:color="8DAE91"/></w:pBdr><w:ind w:left="360"/></w:pPr><w:rPr><w:i/><w:color w:val="5C6659"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:basedOn w:val="Normal"/><w:pPr><w:shd w:val="clear" w:color="auto" w:fill="F2F3EF"/><w:spacing w:after="0"/></w:pPr><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:sz w:val="20"/></w:rPr></w:style>
<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr><w:color w:val="2F7A55"/><w:u w:val="single"/></w:rPr></w:style></w:styles>`

function numbering(orderedIds: number[]): string {
  const levels = (fmt: string, text: (i: number) => string) =>
    [0, 1, 2].map(i => `<w:lvl w:ilvl="${i}"><w:start w:val="1"/><w:numFmt w:val="${fmt}"/><w:lvlText w:val="${text(i)}"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="${720 + i * 360}" w:hanging="360"/></w:pPr></w:lvl>`).join('')
  const bullet = levels('bullet', i => ['•', '◦', '▪'][i])
  const decimal = levels('decimal', i => `%${i + 1}.`)
  const nums = ['<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>', ...orderedIds.map(id => `<w:num w:numId="${id}"><w:abstractNumId w:val="1"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>`)]
  return `${XML}<w:numbering ${NS}><w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>${bullet}</w:abstractNum><w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/>${decimal}</w:abstractNum>${nums.join('')}</w:numbering>`
}

// ---- minimal ZIP writer (stored, no compression)

const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[n] = c >>> 0
  }
  return table
})()

function crc32(data: Uint8Array): number {
  let c = 0xffffffff
  for (const byte of data) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

function zip(files: { name: string; data: Uint8Array }[]): Uint8Array {
  const parts: Uint8Array[] = []
  const central: Uint8Array[] = []
  let offset = 0
  const dosTime = 0, dosDate = (30 << 9) | (1 << 5) | 1 // 2010-01-01; Word ignores it
  for (const file of files) {
    const name = enc.encode(file.name)
    const crc = crc32(file.data)
    const local = new DataView(new ArrayBuffer(30))
    local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint16(6, 0x0800, true)
    local.setUint16(10, dosTime, true); local.setUint16(12, dosDate, true); local.setUint32(14, crc, true)
    local.setUint32(18, file.data.length, true); local.setUint32(22, file.data.length, true); local.setUint16(26, name.length, true)
    parts.push(new Uint8Array(local.buffer), name, file.data)
    const entry = new DataView(new ArrayBuffer(46))
    entry.setUint32(0, 0x02014b50, true); entry.setUint16(4, 20, true); entry.setUint16(6, 20, true); entry.setUint16(8, 0x0800, true)
    entry.setUint16(12, dosTime, true); entry.setUint16(14, dosDate, true); entry.setUint32(16, crc, true)
    entry.setUint32(20, file.data.length, true); entry.setUint32(24, file.data.length, true); entry.setUint16(28, name.length, true)
    entry.setUint32(42, offset, true)
    central.push(new Uint8Array(entry.buffer), name)
    offset += 30 + name.length + file.data.length
  }
  const size = central.reduce((n, p) => n + p.length, 0)
  const end = new DataView(new ArrayBuffer(22))
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true)
  end.setUint32(12, size, true); end.setUint32(16, offset, true)
  const all = [...parts, ...central, new Uint8Array(end.buffer)]
  const out = new Uint8Array(all.reduce((n, p) => n + p.length, 0))
  let at = 0
  for (const part of all) { out.set(part, at); at += part.length }
  return out
}

/** Returns the bytes of a .docx file for the given Markdown. */
export function markdownToDocx(markdown: string): Uint8Array {
  const b = new Builder()
  const body = marked.lexer(markdown).map(token => b.block(token)).join('')
  const documentXml = `${XML}<w:document ${NS}><w:body>${body}<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1300" w:bottom="1440" w:left="1300" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr></w:body></w:document>`
  const linkRels = b.rels.map((href, i) => `<Relationship Id="rIdL${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="${esc(href)}" TargetMode="External"/>`).join('')
  const files = [
    ['[Content_Types].xml', `${XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/></Types>`],
    ['_rels/.rels', `${XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>`],
    ['word/document.xml', documentXml],
    ['word/styles.xml', STYLES],
    ['word/numbering.xml', numbering(b.nums)],
    ['word/_rels/document.xml.rels', `${XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdS" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rIdN" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>${linkRels}</Relationships>`],
  ].map(([name, xml]) => ({ name, data: enc.encode(xml) }))
  return zip(files)
}

export const DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
