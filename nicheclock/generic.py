"""Is a multi-word phrase too generic to count as a topic by itself?

A phrase is generic when it is in the stoplist below, when it is a frequent phrase in the
background corpus onboarding ships (Wikipedia n-gram document frequencies), or when each of its
words is common in that corpus ("small teams", "party game")."""

from __future__ import annotations

from functools import lru_cache

from nicheclock.match import tokens

# Generic bigrams and short phrases that name a kind of content or a broad theme, not a topic.
STOPLIST = frozenset({
    "small team", "small teams", "small business", "early bird", "new product", "new products",
    "best product", "best products", "buying guide", "buying guides", "user guide", "user guides",
    "open source", "release date", "release dates", "release notes", "top picks", "how to", "step by step",
    "getting started", "best practices", "best practice", "case study", "use case", "use cases",
    "social media", "customer service", "machine learning tools", "ai tools", "ai tool",
    "news update", "latest news", "this week", "this year", "top ten", "top 10", "online store",
    "mobile app", "mobile apps", "desktop app", "desktop apps", "free trial", "free trials",
})
COMMON_TOKEN_SHARE = 0.003   # a word in >= 0.3 % of background documents is a common word
FREQUENT_BIGRAM_DOCS = 5     # a phrase in >= 5 background documents is a frequent phrase


@lru_cache(maxsize=1)
def _background():
    try:
        from nicheclock.onboard.extract import load_background
        return load_background()
    except Exception:       # the onboarding corpus is optional here
        return None


def is_generic(phrase: str) -> bool:
    toks = tokens(phrase)
    if not toks:
        return True
    norm = " ".join(toks)
    if norm in STOPLIST or phrase.lower().strip() in STOPLIST:
        return True
    bg = _background()
    if bg is None or not bg.df:
        return False
    plain = " ".join(phrase.lower().split())
    if bg.df.get(plain, 0) >= FREQUENT_BIGRAM_DOCS:
        return True
    n = max(bg.n_docs, 1)
    return len(toks) >= 2 and all(bg.df.get(t, 0) / n >= COMMON_TOKEN_SHARE for t in plain.split())
