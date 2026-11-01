"""``nicheclock.yaml``: dataclasses, load / save / validate (schema in ARCHITECTURE.md)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from nicheclock.i18n import t
from nicheclock.regions import MODES, invalid as invalid_regions, normalize as normalize_regions

# Source names a niche may enable (matches nicheclock/sources/<name>.py; ics is implied by `ics:`).
KNOWN_SOURCES = (
    "google_trends", "wikipedia", "hn", "github", "apple", "gdelt", "federal_register", "rss",
)
DEFAULT_SOURCES = ("google_trends", "wikipedia", "hn", "github", "gdelt")
SCHEDULES = ("daily", "hourly", "weekly")
ENV_CONFIG = "NICHECLOCK_CONFIG"
DEFAULT_CONFIG_NAME = "nicheclock.yaml"


class ConfigError(ValueError):
    """Raised when the YAML cannot be parsed into a Config at all."""


@dataclass
class Identity:
    user_agent: str = ""
    contact_email: str = ""

    def is_set(self) -> bool:
        return bool(self.user_agent.strip()) and "@" in self.contact_email


@dataclass
class Niche:
    name: str
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    # Broad titles (places, big companies, generic topics). A context hit counts only together
    # with a niche keyword or a specific entity in the same item; it never passes the floor alone.
    context: list[str] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=lambda: list(DEFAULT_SOURCES))
    rss: list[str] = field(default_factory=list)      # feeds init discovered or you added
    watch: list[str] = field(default_factory=list)    # feeds you explicitly asked to watch (init --watch)
    ics: list[str] = field(default_factory=list)
    # Feeds of `rss:` / `watch:` written as {url: ..., llm: false}: their items never go to the model.
    llm_off_feeds: list[str] = field(default_factory=list)
    upcoming: bool = True
    regions: list[str] | None = None        # None = the project's regions
    region_mode: str | None = None          # None = the project's mode
    # Source knobs, all optional: geo: [US], wiki_lang: en, github_token_env: NAME,
    # gdelt_max_queries: 3, rss_unfiltered: false, ...
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Project:
    name: str
    urls: list[str] = field(default_factory=list)
    niches: list[Niche] = field(default_factory=list)
    schedule: str = "daily"
    notify: list[dict[str, Any]] = field(default_factory=list)
    gsc: dict[str, Any] | None = None
    regions: list[str] = field(default_factory=lambda: ["global"])   # ["global"] or ISO country codes
    region_mode: str = "combined"                                      # per_country | combined
    # Who the site is, for the model: {title, description}. `init` fills it from the home page.
    profile: dict[str, str] = field(default_factory=dict)

    def niche(self, name: str) -> Niche | None:
        return next((n for n in self.niches if n.name == name), None)


@dataclass
class Config:
    identity: Identity = field(default_factory=Identity)
    data_dir: Path | str = "./data"
    out_dir: Path | str = "./out"
    default_lag_days: int = 3
    projects: list[Project] = field(default_factory=list)
    llm: dict[str, Any] | None = None
    base_dir: Path | None = None   # directory relative paths resolve against (config file's dir)
    path: Path | None = None       # file this config was loaded from
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        base = Path(self.base_dir) if self.base_dir else Path.cwd()
        self.base_dir = base
        self.data_dir = resolve_path(self.data_dir, base)
        self.out_dir = resolve_path(self.out_dir, base)

    def project(self, name: str | None = None) -> Project:
        """Project by name; with ``None`` the only project (error when ambiguous)."""
        if name is None:
            if len(self.projects) == 1:
                return self.projects[0]
            raise ConfigError(
                t("config.ambiguous_project", names=", ".join(p.name for p in self.projects))
                if self.projects else t("config.no_projects"))
        for p in self.projects:
            if p.name == name:
                return p
        raise ConfigError(t("config.unknown_project", name=name))

    def selected(self, name: str | None = None) -> list[Project]:
        return [self.project(name)] if name else list(self.projects)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "identity": {"user_agent": self.identity.user_agent,
                         "contact_email": self.identity.contact_email},
            "data_dir": _portable(self.data_dir, self.base_dir),  # type: ignore[arg-type]
            "out_dir": _portable(self.out_dir, self.base_dir),  # type: ignore[arg-type]
            "default_lag_days": self.default_lag_days,
            "projects": [_project_dict(p) for p in self.projects],
            "llm": self.llm,
        }
        return out


def resolve_path(p: Path | str, base: Path) -> Path:
    path = Path(os.path.expandvars(str(p))).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _portable(p: Path, base: Path) -> str:
    try:
        rel = p.resolve().relative_to(base.resolve())
        return "./" + str(rel) if str(rel) != "." else "."
    except ValueError:
        return str(p)


_NICHE_KEYS = {"name", "keywords", "entities", "context", "formats", "exclude", "sources", "rss",
               "watch", "ics", "upcoming", "options", "regions", "region_mode"}
_PROJECT_KEYS = {"name", "urls", "niches", "schedule", "notify", "gsc", "profile", "regions", "region_mode"}
_PROFILE_KEYS = ("title", "description")
_TOP_KEYS = {"identity", "data_dir", "out_dir", "default_lag_days", "projects", "llm"}


def _project_dict(p: Project) -> dict[str, Any]:
    d: dict[str, Any] = {"name": p.name, "urls": list(p.urls), "niches": [], "schedule": p.schedule}
    for n in p.niches:
        nd: dict[str, Any] = {"name": n.name, "keywords": n.keywords, "entities": n.entities}
        if n.context:
            nd["context"] = n.context
        mark = lambda urls: [{"url": u, "llm": False} if u in n.llm_off_feeds else u for u in urls]  # noqa: E731
        nd.update({"formats": n.formats, "exclude": n.exclude, "sources": n.sources, "rss": mark(n.rss)})
        if n.watch:
            nd["watch"] = mark(n.watch)
        nd.update({"ics": n.ics, "upcoming": n.upcoming})
        if n.regions is not None:
            nd["regions"] = n.regions
        if n.region_mode is not None:
            nd["region_mode"] = n.region_mode
        if n.options:
            nd["options"] = n.options
        d["niches"].append(nd)
    if p.regions != ["global"]:
        d["regions"] = p.regions
    if p.region_mode != "combined":
        d["region_mode"] = p.region_mode
    if p.notify:
        d["notify"] = p.notify
    if p.gsc:
        d["gsc"] = p.gsc
    if p.profile:
        d["profile"] = p.profile
    return d


def _feeds(v: Any) -> tuple[list[str], list[str]]:
    """A feed list whose entries are URLs or ``{url, llm}`` mappings -> (urls, urls with ``llm: false``)."""
    urls: list[str] = []
    off: list[str] = []
    for x in _strs_or_maps(v):
        if isinstance(x, dict):
            u = str(x.get("url") or "")
            if u and x.get("llm") is False:
                off.append(u)
        else:
            u = str(x)
        if u:
            urls.append(u)
    return urls, off


def _strs_or_maps(v: Any) -> list[Any]:
    if v is None:
        return []
    return [v] if isinstance(v, (str, int, float, dict)) else list(v)


def _strs(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (str, int, float)):
        return [str(v)]
    return [str(x) for x in v]


def config_from_dict(raw: dict[str, Any], base_dir: Path | None = None,
                     path: Path | None = None) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError(t("config.root_not_mapping"))
    warnings = [t("config.unknown_top_key", key=k) for k in raw if k not in _TOP_KEYS]
    ident = raw.get("identity") or {}
    projects: list[Project] = []
    for pr in raw.get("projects") or []:
        warnings += [t("config.unknown_project_key", project=pr.get("name"), key=k) for k in pr if k not in _PROJECT_KEYS]
        niches = []
        for nr in pr.get("niches") or []:
            warnings += [t("config.unknown_niche_key", niche=nr.get("name"), key=k) for k in nr if k not in _NICHE_KEYS]
            kw: dict[str, Any] = {k: _strs(nr.get(k)) for k in ("keywords", "entities", "context", "formats", "exclude", "ics")}
            (kw["rss"], off_rss), (kw["watch"], off_watch) = _feeds(nr.get("rss")), _feeds(nr.get("watch"))
            kw["llm_off_feeds"] = off_rss + off_watch
            if nr.get("sources") is not None:
                kw["sources"] = _strs(nr["sources"])
            niches.append(Niche(
                name=str(nr.get("name", "")), upcoming=bool(nr.get("upcoming", True)),
                options=dict(nr.get("options") or {}),
                regions=normalize_regions(nr["regions"]) if nr.get("regions") is not None else None,
                region_mode=str(nr["region_mode"]) if nr.get("region_mode") is not None else None, **kw))
        projects.append(Project(
            name=str(pr.get("name", "")), urls=_strs(pr.get("urls")), niches=niches,
            schedule=str(pr.get("schedule", "daily")), notify=list(pr.get("notify") or []),
            gsc=dict(pr["gsc"]) if pr.get("gsc") else None,
            regions=normalize_regions(pr.get("regions")), region_mode=str(pr.get("region_mode") or "combined"),
            profile={k: str(v) for k, v in (pr.get("profile") or {}).items() if k in _PROFILE_KEYS and v},
        ))
    return Config(
        identity=Identity(str(ident.get("user_agent") or ""), str(ident.get("contact_email") or "")),
        data_dir=raw.get("data_dir") or "./data", out_dir=raw.get("out_dir") or "./out",
        default_lag_days=int(raw.get("default_lag_days", 3)), projects=projects,
        llm=raw.get("llm") or None, base_dir=base_dir, path=path, warnings=warnings,
    )


def load_config(path: str | Path) -> Config:
    p = Path(path).expanduser()
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(t("config.not_found", path=p)) from None
    except yaml.YAMLError as e:
        raise ConfigError(t("config.invalid_yaml", path=p, error=e)) from e
    cfg = config_from_dict(raw or {}, base_dir=p.resolve().parent, path=p.resolve())
    return cfg


def save_config(cfg: Config, path: str | Path | None = None) -> Path:
    p = Path(path).expanduser() if path else cfg.path
    if p is None:
        raise ConfigError(t("config.no_save_path"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg.to_dict(), sort_keys=False, allow_unicode=True, width=100),
                 encoding="utf-8")
    cfg.path = p.resolve()
    return p


def resolve_config_path(explicit: str | Path | None = None) -> Path:
    """--config, else $NICHECLOCK_CONFIG, else ./nicheclock.yaml."""
    if explicit:
        return Path(explicit).expanduser()
    if os.environ.get(ENV_CONFIG):
        return Path(os.environ[ENV_CONFIG]).expanduser()
    return Path.cwd() / DEFAULT_CONFIG_NAME


def validate_config(cfg: Config, require_identity: bool = False) -> list[str]:
    """Human-readable problems; empty list means valid. Identity only counts when required."""
    errs = list(cfg.warnings)
    if require_identity and not cfg.identity.is_set():
        errs.append(t("config.identity_required"))
    if cfg.default_lag_days < 0:
        errs.append(t("config.lag_negative"))
    if cfg.llm is not None:
        from nicheclock.llm.settings import problems
        errs += problems(cfg.llm)
    if not cfg.projects:
        errs.append(t("config.no_projects"))
    seen: set[str] = set()
    for p in cfg.projects:
        if not p.name:
            errs.append(t("config.project_no_name"))
        elif p.name in seen:
            errs.append(t("config.project_duplicate", name=p.name))
        seen.add(p.name)
        if p.schedule not in SCHEDULES:
            errs.append(t("config.schedule_invalid", name=p.name, allowed=", ".join(SCHEDULES)))
        if not p.niches:
            errs.append(t("config.project_no_niches", name=p.name))
        nseen: set[str] = set()
        for n in p.niches:
            where = f"project {p.name!r} niche {n.name!r}"
            if not n.name:
                errs.append(t("config.niche_no_name", name=p.name))
            if n.name in nseen:
                errs.append(t("config.niche_duplicate", where=where))
            nseen.add(n.name)
            if not (n.keywords or n.entities):
                errs.append(t("config.niche_needs_terms", where=where))
            for s in n.sources:
                if s not in KNOWN_SOURCES:
                    errs.append(t("config.source_unknown", where=where, source=s, known=", ".join(KNOWN_SOURCES)))
            if "rss" in n.sources and not (n.rss or n.watch):
                errs.append(t("config.rss_empty", where=where))
            if n.watch and "rss" not in n.sources:
                errs.append(t("config.watch_needs_rss", where=where))
            for u in n.rss + n.watch + n.ics:
                if not u.startswith(("http://", "https://")):
                    errs.append(t("config.feed_url", where=where, url=u))
        for nt in p.notify:
            if not isinstance(nt, dict) or "type" not in nt:
                errs.append(t("config.notify_type", name=p.name))
        errs += _region_errors(f"project {p.name!r}", p.regions, p.region_mode)
        for n in p.niches:
            if n.regions is not None or n.region_mode is not None:
                errs += _region_errors(f"project {p.name!r} niche {n.name!r}", n.regions or ["global"],
                                       n.region_mode or "combined")
        if p.gsc is not None and not p.gsc.get("property"):
            errs.append(t("config.gsc_property", name=p.name))
    return errs


def _region_errors(where: str, regions: list[str], mode: str) -> list[str]:
    errs = []
    bad = invalid_regions(regions)
    if bad:
        errs.append(t("config.region_invalid", where=where, bad=", ".join(bad)))
    if mode not in MODES:
        errs.append(t("config.region_mode_invalid", where=where, modes=" | ".join(MODES)))
    return errs


def validate_dict(raw: dict[str, Any], require_identity: bool = False) -> list[str]:
    """Validate a raw YAML mapping (what `edit` / onboarding handle) without a file."""
    try:
        return validate_config(config_from_dict(raw), require_identity)
    except (ConfigError, TypeError, ValueError, AttributeError) as e:
        return [f"config structure invalid: {e}"]
