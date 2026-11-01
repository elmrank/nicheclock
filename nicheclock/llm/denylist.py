"""Sources whose terms forbid sending their content to an AI model: never part of a prompt.

Data-rights review 2026-10-31: the terms of these publishers forbid AI/ML/TDM/RAG/grounding use of their content, and a headline that
reaches us through GDELT, Google News or Hacker News is still the publisher's content. Their items stay in
the feed and keep their rule-based score; they just never go to a model and carry
"not sent to model: source terms" instead of a fit score.

The check is on the domain of the item URL (subdomains included), on a GDELT item's ``domain`` and on the
domain of the feed a user RSS item came from. A topic is held back when ANY of its signals matches, since
its title may come from that signal. Extend the list with ``llm.denylist: [example.com]``; switch one feed
off with ``llm: false`` in the niche's ``rss:`` / ``watch:``.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit


NOT_SENT = "not sent to model: source terms"   # stored marker (DB, feed.json): never translate; shown via i18n llm.not_sent

DEFAULT_DENYLIST: tuple[str, ...] = (
    "news.google.com",
    "theguardian.com",
    # Condé Nast
    "wired.com", "arstechnica.com", "pitchfork.com", "newyorker.com", "vanityfair.com", "gq.com", "vogue.com",
    # PMC
    "variety.com", "deadline.com", "billboard.com", "rollingstone.com", "hollywoodreporter.com",
    "nme.com", "scmp.com",
    # Future plc
    "pcgamer.com", "gamesradar.com", "techradar.com", "tomshardware.com",
    "futurism.com",
)


def host_of(value: str | None) -> str:
    """Lower-case host of a URL or bare domain, without port, credentials or a leading ``www.``."""
    v = (value or "").strip().lower()
    if not v:
        return ""
    host = urlsplit(v if "//" in v else "//" + v).hostname or ""
    return host.removeprefix("www.").rstrip(".")


class Denylist:
    def __init__(self, extra: Iterable[str] = ()) -> None:
        self.domains = frozenset(h for h in (host_of(d) for d in (*DEFAULT_DENYLIST, *extra)) if h)

    def denies(self, url_or_domain: str | None) -> bool:
        h = host_of(url_or_domain)
        if not h:
            return False
        parts = h.split(".")
        return any(".".join(parts[i:]) in self.domains for i in range(len(parts)))

    def reason(self, topic: Any, off_feeds: Sequence[str] = ()) -> str | None:
        """``NOT_SENT`` when any signal of ``topic`` comes from a denied domain or a feed switched off."""
        off = {f.strip() for f in off_feeds}
        for s in topic.signals:
            x = s.extra or {}
            feed = x.get("feed")
            if feed and feed in off:
                return NOT_SENT
            for cand in (s.url, x.get("domain"), x.get("link"), feed):
                if isinstance(cand, str) and self.denies(cand):
                    return NOT_SENT
        return None


def for_llm_config(llm: dict[str, Any] | None) -> Denylist:
    """The default list plus the ``denylist:`` of a raw ``llm:`` mapping (None: defaults only)."""
    extra = (llm or {}).get("denylist") or []
    return Denylist([str(x) for x in extra] if isinstance(extra, list) else [])
