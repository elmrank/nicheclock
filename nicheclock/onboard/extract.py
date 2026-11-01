"""Phrase extraction for onboarding: slugs + page titles -> keywords / formats / exclude / entities.

No LLM. TF-IDF-style scoring of 1-3-grams against a small bundled background corpus
(`data/background.json.gz`, provenance inside the file and in `build_background.py`).
Pure python by default; scikit-learn is an optional accelerator for the document-frequency step
and gives identical scores.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import quote, unquote, urlsplit

DATA_DIR = Path(__file__).parent / "data"
BACKGROUND_FILE = DATA_DIR / "background.json.gz"

# ---------------------------------------------------------------- lexicons

# Function words and web boilerplate: never part of a phrase, they break n-grams.
STOPWORDS = frozenset(
    """a an the and or nor but of to in on for with by at from into onto over under about as is are was were be been being
    am do does did done doing can could should would will shall may might must have has had having not no yes so than
    then too very just also only more most much many some any all each every other another such own same
    how what why when where which who whom whose this that these those there here it its it's i me my mine we us our
    ours you your yours he him his she her hers they them their theirs one new latest official full get gets got make
    makes made use uses using need needs know via per up out off down again over once
    blog post posts category categories tag tags author authors page pages amp html htm php index home about contact
    sitemap feed rss archive archives
    de la le les el en las los di il der die das und et du des da do de
    """.split()
)

# Content-format words: they describe the *kind of article*, not the topic.
# canonical form -> variants (token tuples are matched longest-first).
_FORMAT_VARIANTS: dict[str, tuple[str, ...]] = {
    "tier list": ("tier list", "tier lists", "tierlist"),
    "patch notes": ("patch notes", "patch note", "patchnotes"),
    "release date": ("release date", "release dates"),
    "system requirements": ("system requirements", "system requirement"),
    "cheat sheet": ("cheat sheet", "cheatsheet", "cheat sheets"),
    "how to": ("how to", "howto"),
    "step by step": ("step by step",),
    "deep dive": ("deep dive",),
    "case study": ("case study", "case studies"),
    "guide": ("guide", "guides"),
    "tips": ("tips", "tip"),
    "review": ("review", "reviews"),
    "checklist": ("checklist", "checklists"),
    "template": ("template", "templates"),
    "tutorial": ("tutorial", "tutorials"),
    "walkthrough": ("walkthrough", "walkthroughs"),
    "explained": ("explained", "explainer"),
    "vs": ("vs", "versus"),
    "best": ("best",),
    "top": ("top",),
    "comparison": ("comparison", "compared"),
    "alternatives": ("alternatives", "alternative"),
    "examples": ("examples", "example"),
    "ideas": ("ideas",),
    "faq": ("faq", "faqs"),
    "roundup": ("roundup", "round up"),
    "ranking": ("ranking", "rankings", "ranked"),
    "news": ("news",),
    "update": ("update", "updates"),
    "preview": ("preview", "previews"),
    "roadmap": ("roadmap",),
    "calendar": ("calendar",),
    "schedule": ("schedule",),
    "timeline": ("timeline",),
    "pricing": ("pricing", "price", "prices", "cost"),
    "requirements": ("requirements", "requirement"),
    "builds": ("builds", "build"),
    "overview": ("overview",),
    "analysis": ("analysis",),
    "summary": ("summary",),
    "list": ("list", "lists"),
}
FORMAT_PHRASES: tuple[str, ...] = tuple(_FORMAT_VARIANTS)
_FORMAT_LOOKUP: dict[tuple[str, ...], str] = {
    tuple(v.split()): canon for canon, variants in _FORMAT_VARIANTS.items() for v in variants
}
_FORMAT_MAXLEN = max(len(k) for k in _FORMAT_LOOKUP)

# Low-signal / filler words. Seen on the site -> suggested `exclude`. They also break n-grams.
EXCLUDE_WORDS = frozenset(
    """tips tricks beginner beginners basics basic easy ultimate complete hub cheats cheat hack hacks free download
    downloads apk crack cracked leaked quick simple everything essential essentials""".split()
)

# Weak signals that a site is about a given kind of niche (token level).
KIND_LEXICON: dict[str, frozenset[str]] = {
    "games": frozenset(
        """game games gaming gamer gamers ps5 ps4 playstation xbox nintendo switch esports dlc roguelike rpg fps mmo
        mmorpg multiplayer indie fortnite valorant zelda mario boss bosses raid quest
        playthrough speedrun gameplay console consoles pc expansion season seasons patch nerf buff""".split()
    ),
    "dev_ai": frozenset(
        """ai llm llms gpt chatgpt openai anthropic claude gemini api apis sdk python javascript typescript rust golang
        java kubernetes docker github git open source developer developers devops cloud database sql framework library
        programming code coding software machine learning model models agent agents mcp cli backend frontend linux
        """.split()
    ),
    "apps": frozenset(
        """app apps ios iphone ipad android mobile widget widgets macos watchos swiftui testflight appstore store
        subscription subscriptions""".split()
    ),
    "policy": frozenset(
        """law laws legal legislation bill bills act regulation regulations regulatory regulator regulators governance
        compliance gdpr ftc sec eu congress senate court courts lawsuit lawsuits copyright liability privacy policy
        policies rulemaking rule rules enforcement statute statutes veto fine fines settlement agency agencies audit
        risk vendor treaty commission attorney""".split()
    ),
}
# Content formats that, by themselves, indicate a niche kind.
KIND_FORMATS: dict[str, frozenset[str]] = {
    "games": frozenset({"tier list", "patch notes", "builds", "walkthrough"}),
    "dev_ai": frozenset(),
    "apps": frozenset(),
    "policy": frozenset(),
}

# ---------------------------------------------------------------- tokenizing

_APOS = re.compile(r"['\u2019`]")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_HEXISH = re.compile(r"[0-9a-f]{6,}")
_EXT = re.compile(r"\.(?:html?|php\d?|aspx?|jsp|shtml|xml|txt)$", re.I)


def fold(text: str) -> str:
    """Lowercase, strip accents and apostrophes ("Café" -> "cafe")."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return _APOS.sub("", text).lower()


def tokenize(text: str) -> list[str]:
    return _WORD.findall(fold(text))


def _junk(tok: str) -> bool:
    if len(tok) < 2 or tok.isdigit() or len(tok) > 30:
        return True
    has_digit = any(c.isdigit() for c in tok)
    if has_digit and len(tok) >= 8:
        return True
    return bool(has_digit and _HEXISH.fullmatch(tok))


def _code_like(tokens: Sequence[str], i: int) -> bool:
    """"fdn 216", "msh 257": a 2-4 letter token followed by a 2-3 digit number is a catalogue code, not a topic
    (4-digit numbers are years: "ai act 2024" keeps "act")."""
    t = tokens[i]
    return (2 <= len(t) <= 4 and t.isalpha() and i + 1 < len(tokens)
            and tokens[i + 1].isdigit() and 2 <= len(tokens[i + 1]) <= 3)


def analyze(tokens: Sequence[str], brand: frozenset[str] = frozenset(), nmax: int = 3):
    """Token stream -> (phrases, formats, excludes), each a set of strings.

    Format phrases, exclude words, stop words, brand tokens and junk tokens end an n-gram run,
    so "snowy owl buying guide" yields phrase "snowy owl" and format "buying guide".
    """
    phrases: set[str] = set()
    formats: set[str] = set()
    excludes: set[str] = set()
    run: list[str] = []

    def flush() -> None:
        for n in range(1, nmax + 1):
            for i in range(len(run) - n + 1):
                phrases.add(" ".join(run[i : i + n]))
        run.clear()

    i = 0
    while i < len(tokens):
        if _code_like(tokens, i):
            flush()
            i += 1
            continue
        for n in range(min(_FORMAT_MAXLEN, len(tokens) - i), 0, -1):
            key = tuple(tokens[i : i + n])
            canon = _FORMAT_LOOKUP.get(key)
            if canon:
                formats.add(canon)
                excludes.update(t for t in key if t in EXCLUDE_WORDS)
                flush()
                i += n
                break
        else:
            t = tokens[i]
            if t in EXCLUDE_WORDS:
                excludes.add(t)
                flush()
            elif t in STOPWORDS or t in brand or _junk(t):
                flush()
            else:
                run.append(t)
            i += 1
    flush()
    return phrases, formats, excludes


# ---------------------------------------------------------------- catalogue tokens

# Short tokens that are real vocabulary for niche sites although a general-English corpus rarely shows them.
KNOWN_SHORT = frozenset(
    """ai eu uk us un ml ar vr xr pc tv ui ux ev 3d 4k 5g ip hr pr seo api sdk cli css cdn app apps bot bots ios gpu cpu
    llm gpt rag mcp aws gcp saas b2b b2c crm erp ide git npm rust ruby java go php sql nosql
    gdpr ftc sec doj irs fda epa fcc nist iso hipaa ccpa coppa ferpa sox pci dsa dma tcpa eeoc nlrb ada
    rpg fps mmo moba dlc gta gtav cod wow lol nba nfl mlb nhl fifa ufc wwe ps5 ps4 ps3 psn xbox
    mod mods deck decks demo beta alpha raid boss loot perk perks skin skins maps map wiki
    card cards pack packs set sets draft meta tier build builds 2k 4x""".split()
)
DICT_MIN_DF = 4


def is_catalogue_token(tok: str, bg: "Background | None" = None) -> bool:
    """Numbers and short alphanumeric codes ("xqz", "kvt", "h100x") that are neither dictionary words nor known acronyms.

    "Dictionary" = the token (or its singular) occurs in >= DICT_MIN_DF documents of the background corpus.
    """
    if tok.isdigit():
        return True
    if len(tok) > 4 or not tok.isalnum() or tok in KNOWN_SHORT:
        return False
    df = bg.df if bg else {}
    stem = tok[:-1] if len(tok) > 3 and tok.endswith("s") else tok
    return max(df.get(tok, 0), df.get(stem, 0)) < DICT_MIN_DF


def _consonants_only(tok: str) -> bool:
    return len(tok) >= 2 and tok.isalpha() and not set(tok) & set("aeiouy")


def template_phrases(slug_sets: Sequence[Iterable[str]], title_sets: Sequence[Iterable[str]], *,
                     title_share: float = 0.3, slug_share: float = 0.03, min_titles: int = 8) -> frozenset[str]:
    """Phrases that sit in the page titles of many sampled pages but almost never in URL slugs.

    Page-type boilerplate such as "Bird Profile, Range & Habitat": every species page repeats it in its <title>, no URL does.
    """
    if len(title_sets) < min_titles or not slug_sets:
        return frozenset()
    t = Counter(p for s in title_sets for p in set(s))
    u = Counter(p for s in slug_sets for p in set(s))
    nt, nu = len(title_sets), len(slug_sets)
    return frozenset(p for p, c in t.items() if c / nt >= title_share and u.get(p, 0) / nu < slug_share)


# ---------------------------------------------------------------- URLs and titles

_SEP = re.compile(r"\s+[|\u2013\u2014\u00b7\u2022\u00bb:~]\s+|\s+-\s+|\s*\|\s*")
_SKIP_SEGMENTS = frozenset(
    """tag tags category categories author authors page feed wp-content wp-admin wp-json wp-includes cart checkout
    account login register search privacy privacy-policy terms terms-of-service cookie-policy contact about about-us
    disclaimer sitemap amp cdn-cgi""".split()
)
_SKIP_EXT = frozenset(
    """jpg jpeg png gif webp svg ico pdf zip gz rar mp3 mp4 webm mov avi css js json xml txt woff woff2 ttf eot csv
    xls xlsx doc docx""".split()
)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def brand_tokens(urls: Iterable[str]) -> frozenset[str]:
    """Tokens of the domain label ("birdnotes.org" -> birdnotes) plus label parts split on hyphens."""
    out: set[str] = set()
    for u in urls:
        host = host_of(u if "//" in u else "//" + u)
        parts = [p for p in host.split(".") if p and p not in {"www", "com", "net", "org", "io", "co", "app", "dev", "ai"}]
        for p in parts[:2]:
            out.update(t for t in tokenize(p) if len(t) > 2)
    return frozenset(out)


def is_content_url(url: str) -> bool:
    parts = urlsplit(url)
    segs = [unquote(s) for s in parts.path.split("/") if s]
    if segs:
        ext = segs[-1].rsplit(".", 1)[-1].lower() if "." in segs[-1] else ""
        if ext in _SKIP_EXT:
            return False
    return not any(s.lower() in _SKIP_SEGMENTS for s in segs)


def section_of(url: str) -> str:
    """First path segment when the URL has >=2 segments, else "/" (flat sites have no sections)."""
    segs = [s for s in urlsplit(url).path.split("/") if s]
    return segs[0].lower() if len(segs) >= 2 else "/"


def slug_of(url: str) -> str:
    """Last meaningful path segment (skips pure ids like /posts/12345)."""
    segs = [unquote(s) for s in urlsplit(url).path.split("/") if s]
    for seg in reversed(segs):
        seg = _EXT.sub("", seg)
        if not seg.replace("-", "").replace("_", "").isdigit():
            return seg
    return ""


def clean_titles(titles: Sequence[str], site_names: Iterable[str] = ()) -> tuple[list[str], set[str]]:
    """Strip "| Brand" style suffix/prefix segments shared by many titles; returns (clean, brand_segments)."""
    segs_last = Counter()
    segs_first = Counter()
    split = [[s.strip() for s in _SEP.split(t) if s.strip()] for t in titles]
    for parts in split:
        if len(parts) >= 2:
            segs_last[fold(parts[-1])] += 1
            segs_first[fold(parts[0])] += 1
    need = max(2, math.ceil(0.3 * len(titles)))
    brands = {s for s, c in segs_last.items() if c >= need} | {s for s, c in segs_first.items() if c >= need}
    brands |= {fold(n) for n in site_names if n}
    out = []
    for t, parts in zip(titles, split):
        keep = [p for p in parts if fold(p) not in brands]
        out.append(" ".join(keep) if keep else "")
    return out, brands


# ---------------------------------------------------------------- background corpus


@dataclass(frozen=True)
class Background:
    n_docs: int
    df: dict[str, int]
    meta: dict = field(default_factory=dict)


@lru_cache(maxsize=4)
def load_background(path: str | None = None) -> Background:
    p = Path(path) if path else BACKGROUND_FILE
    if not p.exists():
        return Background(n_docs=1, df={}, meta={"missing": str(p)})
    with gzip.open(p, "rt", encoding="utf-8") as f:
        data = json.load(f)
    return Background(n_docs=int(data["docs"]), df=data["df"], meta=data.get("meta", {}))


# ---------------------------------------------------------------- scoring


@dataclass(frozen=True)
class Doc:
    url: str
    section: str
    phrases: frozenset[str]
    formats: frozenset[str] = frozenset()
    excludes: frozenset[str] = frozenset()


@dataclass
class Extraction:
    docs: list[Doc]
    ranked: list[str]  # candidate keyword phrases, best first, subsumed phrases removed
    df: dict[str, int]
    score: dict[str, float]
    formats: list[tuple[str, int]]  # (format, doc count) by count desc
    excludes: list[tuple[str, int]]
    brand: frozenset[str] = frozenset()
    bg: Background = field(default_factory=lambda: Background(1, {}))


def _doc_freq(docs: Sequence[Iterable[str]], backend: str) -> Counter:
    if backend in ("sklearn", "auto"):
        try:
            from sklearn.feature_extraction.text import CountVectorizer  # type: ignore

            cv = CountVectorizer(analyzer=lambda d: d, binary=True, lowercase=False)
            x = cv.fit_transform([sorted(d) for d in docs])
            counts = x.sum(axis=0).A1
            return Counter({w: int(c) for w, c in zip(cv.get_feature_names_out(), counts)})
        except ImportError:
            if backend == "sklearn":
                raise
        except ValueError:  # empty vocabulary
            return Counter()
    c: Counter = Counter()
    for d in docs:
        c.update(d)
    return c


MAX_SHARE = 0.5


def score_phrases(
    docs: Sequence[Iterable[str]],
    bg: Background,
    *,
    min_df: int | None = None,
    backend: str = "python",
    skip: frozenset[str] = frozenset(),
    max_share: float | None = MAX_SHARE,
) -> tuple[dict[str, int], dict[str, float]]:
    """df and score per phrase. score = log(1+df) * log(1+lift), lift = site share / background share."""
    n = len(docs)
    if n == 0:
        return {}, {}
    if min_df is None:
        min_df = 2 if n < 1000 else 3
    df_all = _doc_freq(docs, backend)
    m = max(bg.n_docs, 1)
    cat_cache: dict[str, bool] = {}

    def _catalogue(tok: str, b: Background) -> bool:
        if tok not in cat_cache:
            cat_cache[tok] = is_catalogue_token(tok, b)
        return cat_cache[tok]

    df: dict[str, int] = {}
    score: dict[str, float] = {}
    for p, c in df_all.items():
        if c < min_df or (" " not in p and len(p) < 3):
            continue
        if p in skip or (max_share is not None and n >= 20 and c / n > max_share and bg.df.get(p, 0) >= 2):
            continue       # on most pages AND ordinary English ("ai", "guide"): too broad to define a niche
        toks = p.split()
        if len(toks) == 1:
            if _catalogue(p, bg):
                continue
        elif any(_consonants_only(t) and _catalogue(t, bg) for t in toks):
            continue                           # "fdc 12", "msc standard": a code inside a longer phrase
        lift = (c / n) / ((bg.df.get(p, 0) + 1) / (m + 1))
        s = math.log1p(c) * math.log1p(lift)
        if " " in p:
            s *= 1.3
        df[p] = c
        score[p] = round(s, 9)
    return df, score


def drop_subsumed(order: Sequence[str], df: dict[str, int], pool: int = 300) -> list[str]:
    """Remove phrase p when a longer candidate containing it (word-bounded) covers >=80% of its docs."""
    head = list(order[:pool])
    padded = {p: f" {p} " for p in head}
    keep = []
    for p in head:
        if any(k != p and padded[p] in padded[k] and df[k] >= 0.8 * df[p] for k in head):
            continue
        keep.append(p)
    return keep + list(order[pool:])


def extract(docs: Sequence[Doc], bg: Background | None = None, *, brand: frozenset[str] = frozenset(),
            backend: str = "python", min_df: int | None = None, skip: frozenset[str] = frozenset(),
            max_share: float | None = MAX_SHARE) -> Extraction:
    bg = bg or load_background()
    df, score = score_phrases([d.phrases for d in docs], bg, min_df=min_df, backend=backend, skip=skip,
                              max_share=max_share)
    order = sorted(score, key=lambda p: (-score[p], p))
    ranked = drop_subsumed(order, df)
    fmt = Counter(f for d in docs for f in d.formats)
    exc = Counter(e for d in docs for e in d.excludes)
    return Extraction(
        docs=list(docs),
        ranked=ranked,
        df=df,
        score=score,
        formats=sorted(fmt.items(), key=lambda kv: (-kv[1], kv[0])),
        excludes=sorted(exc.items(), key=lambda kv: (-kv[1], kv[0])),
        brand=brand,
        bg=bg,
    )


def build_docs(urls: Sequence[str], titles: dict[str, tuple[str, str]] | None = None,
               brand: frozenset[str] = frozenset(), drop: frozenset[str] = frozenset()) -> list[Doc]:
    """One Doc per content URL. `titles` maps URL -> (clean title, clean h1); `drop` phrases are removed."""
    titles = titles or {}
    docs: list[Doc] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen or not is_content_url(url):
            continue
        seen.add(url)
        streams = [tokenize(slug_of(url))]
        for text in titles.get(url, ()):
            if text:
                streams.append(tokenize(text))
        phrases: set[str] = set()
        formats: set[str] = set()
        excludes: set[str] = set()
        for toks in streams:
            p, f, e = analyze(toks, brand)
            phrases |= p
            formats |= f
            excludes |= e
        phrases -= drop
        if phrases or formats:
            docs.append(Doc(url, section_of(url), frozenset(phrases), frozenset(formats), frozenset(excludes)))
    return docs


# ---------------------------------------------------------------- Wikipedia entities

WIKI_API = "https://en.wikipedia.org/w/api.php"
PAGEVIEWS_ARTICLE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user"
TERMS_URL = "https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use"
TERMS_NOTE = "Wikimedia API Etiquette: identify the client with a descriptive User-Agent and keep requests serial."


def default_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "nicheclock" / "wikipedia_titles.json"


def title_matches(phrase: str, title: str) -> bool:
    """True when `title` (minus a trailing "(disambiguator)") reads exactly like `phrase` once normalised."""
    return _norm_title(title) == phrase


def _norm_title(s: str) -> str:
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)
    return " ".join(tokenize(s))


def _title_variants(phrase: str) -> list[str]:
    words = phrase.split()
    first = phrase[:1].upper() + phrase[1:]
    titled = " ".join(w.capitalize() for w in words)
    upper = phrase.upper() if len(words) <= 4 and all(len(w) <= 5 for w in words) else None
    short = " ".join(w.upper() if len(w) <= 2 else w for w in words)       # "us owl" -> "US owl"
    short = short[:1].upper() + short[1:]
    out: list[str] = []
    for v in (first, titled, upper, short):
        if v and v not in out:
            out.append(v)
    return out


# An entity is *broad* when it is a general page everybody reads about: a place, a generic concept, or an
# article with a big audience. Broad entities go to `context:` (they only count next to a niche keyword).
BROAD_VIEWS = 5000
_PLACE = re.compile(
    r"\b(?:city|capital|country|u\.s\. state|state of the united states|county|province|town|village|region|"
    r"continent|island|municipality|district|borough|sovereign state)\b", re.I)
_CONCEPT = re.compile(
    r"^(?:an? |the )?(?:type|form|kind|class|category|method|process|practice|model|concept|field|branch|study|"
    r"technique|approach|term|means|idea|principle|condition|state of|area|research area|set of|software agent|"
    r"intelligent agent|system|systems|programs?|tools?|strategy|style|genre|stage|phase|role|job|profession)\b|"
    r"\b(?:funding model|business model|process of|branch of|field of|study of|area of|state of being|"
    r"condition of|general term)\b", re.I)


def common_noun_reason(title: str, description: str, lead: str) -> str:
    """Why `title` is a common-noun phrase rather than a proper noun ("" when it looks like a name).

    Signs: its Wikipedia short description names a kind of thing ("Type of ...", "Funding model ..."); its lead
    sentences use the title in lowercase mid-sentence ("... a handy field guide"); or they pluralise it
    ("Bird feeders are ..."). Laws, products, organisations and works are capitalised, singular names.
    """
    if _CONCEPT.search(description or ""):
        return "description"
    # Wikipedia titles common nouns in sentence case ("Operational risk", "Bird feeder", "Field guide") and names
    # in capitals ("Merlin Bird ID", "Migratory Bird Treaty Act"): a lowercase last word marks a common noun
    words = re.sub(r"\s*\([^)]*\)\s*$", "", title).split()
    if len(words) >= 2 and words[-1].isalpha() and words[-1].islower() and words[-1] not in STOPWORDS:
        return "sentence-case"
    if not lead:
        return ""
    if re.search(r"\b" + re.escape(title) + r"(?:s|es)\b", lead, re.I) and not title.endswith("s"):
        return "plural"
    # sentence-case title ("Field guide"): a proper noun would keep its capitals when repeated mid-sentence
    if title[1:] == title[1:].lower() and title[:1].isupper():
        lower = title[:1].lower() + title[1:]
        for m in re.finditer(r"\b" + re.escape(lower) + r"\b", lead):
            if m.start() > 0 and lead[:m.start()].rstrip()[-1:] not in ".!?":
                return "lowercase"
    return ""


def is_broad_entity(description: str, views: int | None) -> bool:
    """Place, generic concept (by Wikipedia short description) or > BROAD_VIEWS median daily views."""
    return bool(_PLACE.search(description or "") or _CONCEPT.search(description or "")
                or (views is not None and views > BROAD_VIEWS))


def _related(phrase: str, title: str) -> bool:
    """Title reads like the phrase, shares a word with it, or the phrase is its acronym ("gdpr")."""
    words = _norm_title(title).split()
    return (" ".join(words) == phrase or bool(set(phrase.split()) & set(words))
            or phrase == "".join(w[0] for w in words if w not in STOPWORDS))


# A single word resolves to a Wikipedia page for ordinary nouns too ("Vendor", "Governance"). Only pages whose
# short description names a thing people follow (a game, company, law, person, place...) count as entities.
_NAMED_ENTITY = re.compile(
    r"\b(?:video game|game|games|company|companies|corporation|organi[sz]ation|brand|software|website|web service|"
    r"application|app|operating system|programming language|film|television|tv series|web series|series|band|"
    r"musician|singer|rapper|actor|actress|streamer|youtuber|footballer|politician|businessperson|businessman|"
    r"fictional (?:character|universe)|franchise|album|song|novel|manga|anime|comic|esports|league|club|"
    r"university|agency|commission|regulation|directive|act of|law|treaty|statute|bill|u\.s\. state|state of the united states|"
    r"city|country|"
    r"town|village|county|province|capital)\b", re.I)


def is_named_entity(short_description: str) -> bool:
    return bool(_NAMED_ENTITY.search(short_description or ""))


class EntityResolver:
    """Resolves phrases to en.wikipedia titles (redirects followed, disambiguation pages dropped).

    Cached on disk (including misses) so repeated `init` runs cost no requests.
    Step 1: batched exact-title lookup with case variants (one request per ~16 phrases).
    Step 2: MediaWiki `opensearch` for a few multi-word phrases still unresolved.
    """

    def __init__(self, client, cache_path: str | Path | None = None, *, opensearch_limit: int = 12):
        self.client = client
        self.path = Path(cache_path) if cache_path else default_cache_path()
        self.opensearch_limit = opensearch_limit
        self.cache: dict[str, str] = {}
        try:
            self.cache = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.requests = 0
        self.desc: dict[str, str] = {}   # canonical title -> Wikipedia short description (this run)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cache, sort_keys=True, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def _api(self, **params) -> dict | None:
        params.update(format="json", formatversion="2")
        self.requests += 1
        try:
            r = self.client.get(WIKI_API, params=params, robots=False)
            if getattr(r, "status_code", 200) >= 400:
                return None
            return r.json()
        except Exception:
            return None

    def _lookup_titles(self, titles: list[str]) -> dict[str, str] | None:
        """variant title -> canonical title for existing, non-disambiguation pages; None when the API failed."""
        data = self._api(action="query", titles="|".join(titles), redirects="1", prop="pageprops",
                         ppprop="disambiguation|wikibase-shortdesc")
        if not data or "query" not in data:
            return None
        q = data["query"]
        hop = {m["from"]: m["to"] for m in q.get("normalized", [])}
        hop2 = {m["from"]: m["to"] for m in q.get("redirects", [])}
        pages = {p["title"]: p for p in q.get("pages", [])}
        out: dict[str, str] = {}
        for t in titles:
            cur = hop.get(t, t)
            cur = hop2.get(cur, cur)
            page = pages.get(cur)
            if page and not page.get("missing") and not page.get("invalid") \
                    and "disambiguation" not in page.get("pageprops", {}):
                out[t] = cur
                self.desc[cur] = page.get("pageprops", {}).get("wikibase-shortdesc", "")
                self.cache[f"desc:{cur}"] = self.desc[cur]
        return out

    def _opensearch(self, phrase: str) -> str | None | bool:
        """Title, None (no exact match) or False (the request failed: do not cache)."""
        # opensearch has its own (non-formatversion) response: [query, [titles], [descr], [urls]]
        self.requests += 1
        try:
            r = self.client.get(WIKI_API, robots=False, params={"action": "opensearch", "search": phrase,
                                                                "limit": "5", "namespace": "0", "format": "json"})
            arr = r.json()
            titles = arr[1]
        except Exception:
            return False
        want = " ".join(tokenize(phrase))
        for t in titles:
            if _norm_title(t) == want:
                found = self._lookup_titles([t])
                return (found or {}).get(t)
        return None

    def describe(self, title: str) -> str:
        return self.desc.get(title) or self.cache.get(f"desc:{title}", "")

    def leads(self, titles: Sequence[str]) -> dict[str, str]:
        """Lead sentences (plain text, first two) of en.wikipedia articles, cached on disk; "" when unavailable."""
        todo = [t for t in dict.fromkeys(titles) if f"lead:{t}" not in self.cache]
        for i in range(0, len(todo), 20):
            chunk = todo[i : i + 20]
            data = self._api(action="query", titles="|".join(chunk), prop="extracts", exintro="1", explaintext="1",
                             exsentences="2", exlimit="20", redirects="1")
            if not data or "query" not in data:
                continue        # not cached: retried next run
            got = {p["title"]: (p.get("extract") or "")[:600] for p in data["query"].get("pages", [])}
            for t in chunk:
                self.cache[f"lead:{t}"] = got.get(t, "")
        if todo:
            self._save()
        return {t: self.cache.get(f"lead:{t}", "") for t in titles}

    def median_daily_views(self, title: str, today: date | None = None) -> int | None:
        """Median daily en.wikipedia views of the article over the last 30 days (Wikimedia REST, cached 30 days)."""
        today = today or date.today()
        key = f"views:{title}"
        hit = self.cache.get(key)
        if hit:
            n, _, d = hit.partition("@")
            try:
                if (today - date.fromisoformat(d)).days < 30:
                    return int(n)
            except ValueError:
                pass
        end = today - timedelta(days=1)
        start = end - timedelta(days=29)
        url = (f"{PAGEVIEWS_ARTICLE}/{quote(title.replace(' ', '_'), safe='')}/daily/"
               f"{start:%Y%m%d}00/{end:%Y%m%d}00")
        self.requests += 1
        try:
            r = self.client.get(url)
            if getattr(r, "status_code", 200) != 200:
                return None
            views = sorted(int(i["views"]) for i in r.json().get("items", []))
        except Exception:
            return None
        if not views:
            return None
        med = views[len(views) // 2]
        self.cache[key] = f"{med}@{today.isoformat()}"
        self._save()
        return med

    def _accept(self, phrase: str, title: str) -> bool:
        if not title or title.lower().startswith("list of"):
            return False
        if " " not in phrase and not is_named_entity(self.desc.get(title, "")):
            return False        # "Vendor", "Governance": ordinary nouns, not entities
        # reached through a redirect: keep only when the title still relates to the phrase
        # ("mbta" -> "Migratory Bird Treaty Act"; "snowy owl" -> "Hedwig" is out)
        return _related(phrase, title)

    def resolve(self, phrases: Sequence[str], limit: int = 30) -> dict[str, str]:
        """phrase -> Wikipedia title for the first `limit` phrases that resolve (cached misses are free)."""
        todo = [p for p in phrases[:limit] if p not in self.cache]
        batch: list[tuple[str, list[str]]] = [(p, _title_variants(p)) for p in todo]
        i = 0
        while i < len(batch):
            chunk, titles, n = [], [], 0
            while i < len(batch) and n + len(batch[i][1]) <= 40:
                chunk.append(batch[i])
                titles += batch[i][1]
                n += len(batch[i][1])
                i += 1
            found = self._lookup_titles(titles)
            if found is None:          # API hiccup: do not cache these phrases as "no article"
                continue
            for p, variants in chunk:
                hit = next((found[v] for v in variants if v in found), "")
                hit = hit if self._accept(p, hit) else ""
                self.cache[p] = hit
        misses = [p for p in todo if p in self.cache and not self.cache[p] and " " in p]
        left = misses[: self.opensearch_limit]
        for p in misses[self.opensearch_limit:]:
            del self.cache[p]                      # no search budget left: not a confirmed miss, try again next run
        for p in left:
            hit = self._opensearch(p)
            if hit is False:
                del self.cache[p]                  # request failed: leave it uncached, retry next run
                continue
            self.cache[p] = hit if hit and self._accept(p, hit) else ""
        if todo:
            self._save()
        return {p: self.cache[p] for p in phrases[:limit] if self.cache.get(p)}


def split_keywords_entities(ranked: Sequence[str], resolver: EntityResolver | None, *,
                            entity_limit: int = 30) -> tuple[list[str], dict[str, str]]:
    """Return (keyword phrases, {phrase: wikipedia title}).

    A phrase leaves the keyword list only when its Wikipedia title reads exactly like it ("merlin bird id" ->
    "Merlin Bird ID"). When the title differs ("mbta" -> "Migratory Bird Treaty Act") the phrase stays a
    keyword too, because text matching needs the words people actually write.
    """
    if resolver is None:
        return list(ranked), {}
    ents = resolver.resolve(list(ranked), limit=entity_limit)
    return [p for p in ranked if p not in ents or not title_matches(p, ents[p])], ents
