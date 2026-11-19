"""Source filtering: duplicates, junk, blocked domains and authority ranking."""

from research_assistant.sources import authority_bonus, filter_and_rank, normalize_url

TEXT = "x" * 120


def result(url, score=0.5, content=TEXT):
    return {"url": url, "title": url, "score": score, "content": content}


def test_normalize_url_ignores_tracking_www_fragment_and_trailing_slash():
    a = normalize_url("http://www.Example.com/a/b/?utm_source=x&id=7#top")
    b = normalize_url("https://example.com/a/b?id=7")
    assert a == b
    assert normalize_url("https://example.com/a?id=1") != normalize_url("https://example.com/a?id=2")


def test_duplicates_blocked_domains_and_thin_pages_are_dropped():
    results = [
        result("https://www.example.com/page?utm_campaign=a"),
        result("https://example.com/page/"),  # same page
        result("https://www.pinterest.com/pin/1"),  # blocked
        result("https://m.quora.com/q"),  # blocked subdomain
        result("https://thin.example.org/p", content="too short"),
        result("https://other.example.net/report"),
    ]
    kept = filter_and_rank(results, limit=5, blocked=["pinterest.com", "quora.com"])
    assert [r["url"] for r in kept] == ["https://www.example.com/page?utm_campaign=a", "https://other.example.net/report"]


def test_authoritative_hosts_rank_higher_and_limit_applies():
    results = [
        result("https://blog.example.com/a", score=0.60),
        result("https://data.energy.gov/b", score=0.50),  # .gov bonus 0.2 -> 0.70
        result("https://www.iea.org/c", score=0.52),  # preferred bonus 0.15 -> 0.67
        result("https://shop.example.com/d", score=0.58),
    ]
    kept = filter_and_rank(results, limit=3, preferred=["iea.org"])
    assert [r["url"] for r in kept] == ["https://data.energy.gov/b", "https://www.iea.org/c", "https://blog.example.com/a"]
    assert authority_bonus("https://cs.stanford.edu/x", []) == 0.2
    assert authority_bonus("https://example.com", []) == 0.0


def test_falls_back_to_original_results_when_everything_is_filtered():
    results = [result("https://www.pinterest.com/a"), result("https://www.pinterest.com/b")]
    assert len(filter_and_rank(results, limit=1, blocked=["pinterest.com"])) == 1
    assert filter_and_rank([], limit=3) == []
