import { isWebUrl, type SourceRef } from './citations'

/** A source's title: a link for web pages, plain text for uploaded files. */
export default function SourceLink({ source }: { source: SourceRef }) {
  return isWebUrl(source.url) ? <a href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a> : <span>{source.title}</span>
}
