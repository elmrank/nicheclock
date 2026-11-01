"""The docs are tested: front matter, links, and every config / JSON / shell snippet in docs/*.md.

Offline and fast. The commands themselves run for real in the docs-smoke job (tests/docs_smoke.sh).
"""
from __future__ import annotations

import json
import re
import subprocess

import pytest
import yaml

from nicheclock.config import config_from_dict, validate_config
from tests.docs_blocks import DOCS, blocks, published, split_front_matter

PAGES = published()
BASE = {
    "identity": {"user_agent": "docs-test/1.0 (+mailto:docs@example.com)", "contact_email": "docs@example.com"},
    "projects": [{"name": "example", "urls": ["https://example.com"],
                  "niches": [{"name": "demo", "keywords": ["demo phrase"], "sources": ["hn"]}]}],
}
EM_EN = ("\u2014", "\u2013")
BANNED = ("super intelligence", "seamless", "revolutionary", "game-changing", "cutting-edge", "unlock the power")


def ids():
    return [p.name for p in PAGES]


def prose(text: str) -> str:
    """Text outside fenced code."""
    out, fenced = [], False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            out.append(ln)
    return "\n".join(out)


def slug(heading: str) -> str:
    h = re.sub(r"<[^>]+>", "", heading).strip().lower()
    h = re.sub(r"[`*_]", "", h)
    h = re.sub(r"[^\w\s-]", "", h)
    return re.sub(r"\s+", "-", h).strip("-")


def test_pages_exist():
    names = {p.stem for p in PAGES}
    assert {"index", "quickstart", "configuration", "docker", "models", "mcp", "api", "search-console", "sources",
            "troubleshooting", "faq", "limitations", "roadmap", "contributing", "onboarding", "regions"} <= names


@pytest.mark.parametrize("page", PAGES, ids=ids())
def test_front_matter_and_prose(page):
    meta, body = split_front_matter(page.read_text(encoding="utf-8"))
    assert 0 < len(meta["title"]) <= 45, meta["title"]
    assert 100 <= len(meta["description"]) <= 160, (len(meta["description"]), meta["description"])
    assert meta["order"].isdigit()
    assert len(re.findall(r"^# ", prose(body), re.M)) == 1, "exactly one H1"
    text = prose(body)
    assert not any(c in text for c in EM_EN), "em or en dash in prose"
    low = text.lower()
    assert not [w for w in BANNED if w in low]
    assert not re.search(r"[^\x00-\x7f]{3,}", text.replace("\u2019", "'")), "non-English text"
    for host in re.findall(r"[\w.-]*elmrank\.com", body):
        assert host == "nicheclock.elmrank.com", host


@pytest.mark.parametrize("page", PAGES, ids=ids())
def test_links_and_images(page):
    meta, body = split_front_matter(page.read_text(encoding="utf-8"))
    for target in re.findall(r"\]\(([^)\s]+)\)", prose(body)):
        if target.startswith(("http://", "https://", "mailto:", "/")):
            continue
        path, _, frag = target.partition("#")
        dest = page if not path else (page.parent / path)
        assert dest.is_file(), f"{page.name}: broken link {target}"
        if frag:
            heads = [slug(h) for h in re.findall(r"^#{1,6} (.+)$", prose(dest.read_text(encoding="utf-8")), re.M)]
            assert frag in heads, f"{page.name}: no heading #{frag} in {dest.name}"
        if dest.suffix in (".png", ".webp"):
            assert dest.stat().st_size > 1000


def merge(kind: str, data: dict) -> dict:
    cfg = json.loads(json.dumps(BASE))
    if kind == "top":
        cfg.update(data)
    elif kind == "project":
        cfg["projects"][0].update(data)
    elif kind == "niche":
        cfg["projects"][0]["niches"][0].update(data)
    else:
        raise AssertionError(kind)
    return cfg


def yaml_blocks():
    for page in PAGES:
        for b in blocks(page.read_text(encoding="utf-8")):
            if b.lang == "yaml" and "compose-override" not in b.tags:
                yield pytest.param(page, b, id=f"{page.stem}:{b.line}")


@pytest.mark.parametrize("page,block", list(yaml_blocks()))
def test_yaml_snippets_validate(page, block):
    data = yaml.safe_load(block.text)
    assert isinstance(data, dict)
    if "projects" in data:
        cfg = {"identity": BASE["identity"], **data}
    else:
        kinds = [t for t in block.tags if t in ("top", "project", "niche")]
        assert kinds, f"{page.name}:{block.line}: a yaml fragment needs a tag: top, project or niche"
        cfg = merge(kinds[0], data)
    conf = config_from_dict(cfg)
    assert validate_config(conf, require_identity=True) == []
    if "notify" in cfg["projects"][0]:
        from nicheclock.notify import validate_channels
        assert validate_channels(conf.projects[0]) == []


def test_compose_override_is_valid_yaml_with_services():
    for page in PAGES:
        for b in blocks(page.read_text(encoding="utf-8")):
            if "compose-override" in b.tags:
                assert "services" in yaml.safe_load(b.text)


def json_blocks():
    for page in PAGES:
        for b in blocks(page.read_text(encoding="utf-8")):
            if b.lang == "json":
                yield pytest.param(b, id=f"{page.stem}:{b.line}")


@pytest.mark.parametrize("block", list(json_blocks()))
def test_json_snippets_parse(block):
    json.loads(block.text)


def bash_blocks():
    for page in PAGES:
        for b in blocks(page.read_text(encoding="utf-8")):
            if b.lang == "bash":
                yield pytest.param(b, id=f"{page.stem}:{b.line}")


@pytest.mark.parametrize("block", list(bash_blocks()))
def test_bash_snippets_have_valid_syntax(block):
    r = subprocess.run(["bash", "-n"], input=block.text, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr


def test_every_cli_command_in_docs_exists():
    from nicheclock.cli import build_parser
    parser = build_parser()
    sub = next(a for a in parser._actions if a.dest == "cmd")
    known = set(sub.choices)
    for b in (b for p in PAGES for b in blocks(p.read_text(encoding="utf-8")) if b.lang == "bash"):
        for m in re.finditer(r"^[ \t]*nicheclock[ \t]+(?:-c[ \t]+\S+[ \t]+)?([a-z]+)", b.text, re.M):
            assert m.group(1) in known, f"unknown command: nicheclock {m.group(1)}"
