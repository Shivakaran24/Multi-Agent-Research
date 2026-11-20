"""Check that every source is real before it can be cited.

A source passes only if it can be opened and its text matches what the search reported, or, for an
academic paper, if its DOI / arXiv ID is confirmed by Crossref / arXiv. The passage later shown to
the writer and the claim checker is taken from the verified page itself, not from the search snippet.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html.parser import HTMLParser

from research_assistant.documents import best_excerpt, extract_pdf_text
from research_assistant.schemas import ResearchFinding, Source

logger = logging.getLogger(__name__)

MAX_PAGE_BYTES = 1_500_000
MAX_PDF_BYTES = 5 * 1024 * 1024
MIN_PAGE_CHARS = 200
MATCH_THRESHOLD = 0.4
PASSAGE_CHARS = 1_200
USER_AGENT = "FieldworkVerifier/1.0 (+source check)"
_DOI = re.compile(r"(?:doi\.org|dx\.doi\.org)/(10\.\d{4,9}/[^\s?#]+)", re.I)
_ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})(?:v\d+)?(?:\.pdf)?", re.I)
_WORD = re.compile(r"[a-z0-9]{5,}")


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    kind: str = "page"  # page | paper | provided
    title: str | None = None
    passage: str = ""


@dataclass
class VerificationReport:
    checked: int = 0
    verified: int = 0
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (url, reason)

    @property
    def note(self) -> str:
        if not self.checked:
            return ""
        line = f"> Verification: {self.verified} of {self.checked} sources were opened and checked"
        if self.dropped:
            line += f"; {len(self.dropped)} could not be verified and were left out"
        return line + "."


# --- fetching -----------------------------------------------------------------------------------


def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        target = urllib.parse.urlparse(newurl)
        if target.scheme not in {"http", "https"} or not _public_host(target.hostname or ""):
            raise urllib.error.URLError("redirect to a non-public address")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}:
            self._skip += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"} and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):  # noqa: ANN001
        if self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.parts.append(data.strip())


def html_text(html: str) -> tuple[str, str]:
    parser = _Text()
    try:
        parser.feed(html)
    except Exception:  # malformed markup: use what was parsed
        pass
    return " ".join(" ".join(parser.parts).split()), " ".join(parser.title.split())


def fetch_page(url: str, timeout: float = 8.0) -> tuple[str, str]:
    """Open a public http(s) page. Returns (page text, title); raises on any failure."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not _public_host(parsed.hostname):
        raise ValueError("not a public web address")
    opener = urllib.request.build_opener(_SafeRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,text/plain"})
    with opener.open(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")
        kind = (response.headers.get("Content-Type") or "").lower()
        if "pdf" in kind:
            data = response.read(MAX_PDF_BYTES + 1)
            if len(data) > MAX_PDF_BYTES:
                raise ValueError("PDF too large to check")
            return extract_pdf_text(data), ""
        if not ("html" in kind or "text/plain" in kind or "xml" in kind):
            raise ValueError("not a readable page")
        raw = response.read(MAX_PAGE_BYTES).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    return html_text(raw) if "html" in kind or "xml" in kind else (" ".join(raw.split()), "")


# --- papers -------------------------------------------------------------------------------------


def _get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public API hosts
        return response.read(1_000_000)


def lookup_paper(url: str, timeout: float = 8.0) -> tuple[str, str] | None:
    """Confirm a DOI (Crossref) or arXiv ID. Returns (title, abstract) or None if it is not a paper link."""
    doi = _DOI.search(url)
    if doi:
        identifier = doi.group(1).rstrip(".,;)")
        data = json.loads(_get(f"https://api.crossref.org/works/{urllib.parse.quote(identifier)}", timeout))
        message = data["message"]
        title = " ".join((message.get("title") or [""])[0].split())
        abstract = re.sub(r"<[^>]+>", " ", message.get("abstract") or "")
        if not title:
            raise ValueError("DOI not found")
        return title, " ".join(abstract.split())
    arxiv = _ARXIV.search(url)
    if arxiv:
        feed = ET.fromstring(_get(f"https://export.arxiv.org/api/query?id_list={urllib.parse.quote(arxiv.group(1))}", timeout))
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entry = feed.find("a:entry", ns)
        title = " ".join((entry.findtext("a:title", "", ns) if entry is not None else "").split())
        if not title or title.lower() == "error":
            raise ValueError("arXiv paper not found")
        return title, " ".join((entry.findtext("a:summary", "", ns) or "").split())
    return None


# --- verification -------------------------------------------------------------------------------


def _overlap(reported: str, page: str) -> float:
    wanted = set(_WORD.findall(reported.lower()))
    if not wanted:
        return 1.0
    have = set(_WORD.findall(page.lower()))
    return len(wanted & have) / len(wanted)


def verify_source(
    source: Source,
    topic: str,
    fetch: Callable[[str], tuple[str, str]] = fetch_page,
    paper: Callable[[str], tuple[str, str] | None] = lookup_paper,
) -> Verdict:
    url = str(source.url)
    if url.startswith("upload:"):
        return Verdict(True, kind="provided", passage=source.content)
    # Papers: identity is confirmed by the registry, claims are later checked against the abstract
    # (and the page text when the publisher lets us read it).
    try:
        found = paper(url)
    except Exception as exc:
        found = None
        paper_error = f"paper registry check failed ({type(exc).__name__})"
    else:
        paper_error = ""
    if found:
        title, abstract = found
        passage = abstract
        try:
            text, _ = fetch(url)
            passage = best_excerpt(text, f"{topic} {title}")[:PASSAGE_CHARS] if len(text) >= MIN_PAGE_CHARS else abstract
        except Exception:
            pass  # paywalled or blocked: the registry record still proves the paper exists
        if len(passage) < 80:
            return Verdict(False, "paper confirmed but no abstract or text available to check claims against")
        return Verdict(True, kind="paper", title=title, passage=passage[:PASSAGE_CHARS])
    try:
        text, title = fetch(url)
    except Exception as exc:  # any failure means "not verified"
        reason = getattr(exc, "reason", None) or str(exc) or type(exc).__name__
        return Verdict(False, paper_error or f"could not be opened ({str(reason)[:80]})")
    if len(text) < MIN_PAGE_CHARS:
        return Verdict(False, "page has no readable text")
    reported = f"{source.title} {source.snippet or ''} {source.content or ''}"
    if _overlap(reported, text) < MATCH_THRESHOLD:
        return Verdict(False, "page text does not match what the search reported")
    return Verdict(True, title=title or None, passage=best_excerpt(text, f"{topic} {source.title}")[:PASSAGE_CHARS])


def verify_findings(
    findings: list[ResearchFinding],
    topic: str,
    *,
    verify: Callable[[Source, str], Verdict] = verify_source,
    workers: int = 8,
) -> tuple[list[ResearchFinding], VerificationReport]:
    """Open every unique source once; keep only verified ones, with passages from the live page."""
    unique: dict[str, Source] = {}
    for finding in findings:
        for source in finding.sources:
            unique.setdefault(str(source.url), source)
    report = VerificationReport(checked=len(unique))
    verdicts: dict[str, Verdict] = {}
    if unique:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(unique)))) as pool:
            for url, verdict in zip(unique, pool.map(lambda s: _safe(verify, s, topic), unique.values()), strict=True):
                verdicts[url] = verdict
    for url, verdict in verdicts.items():
        if verdict.ok:
            report.verified += 1
        else:
            report.dropped.append((url, verdict.reason))
    kept: list[ResearchFinding] = []
    for finding in findings:
        sources = []
        for source in finding.sources:
            verdict = verdicts[str(source.url)]
            if verdict.ok:
                sources.append(
                    source.model_copy(update={
                        "title": verdict.title if verdict.kind == "paper" and verdict.title else source.title,
                        "content": verdict.passage or source.content,
                    })
                )
        kept.append(finding.model_copy(update={"sources": sources}))
    return kept, report


def _safe(verify: Callable[[Source, str], Verdict], source: Source, topic: str) -> Verdict:
    try:
        return verify(source, topic)
    except Exception as exc:  # a verifier bug must never publish an unchecked source
        logger.warning("Source verification crashed for %s", source.url, exc_info=True)
        return Verdict(False, f"verification error ({type(exc).__name__})")
