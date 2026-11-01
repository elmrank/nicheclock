"""The report page: one HTML template, one stylesheet, one script, one font (``output/app/``).

``nicheclock run`` writes it next to each project's feed (data embedded, so the file also opens from disk) and
``nicheclock serve`` serves it. The hosted site's build uses the same files with a different top bar and fetches
its JSON (``hosted/site/build.py``).
"""

from __future__ import annotations

import json
import re
import shutil
from html import escape
from pathlib import Path

DIR = Path(__file__).resolve().parent / "app"
ASSET_FILES = ("app.css", "app.js", "source-sans-3.woff2", "OFL-Source-Sans-3.txt")


def template() -> str:
    return (DIR / "index.html").read_text(encoding="utf-8")


def install_assets(dest: Path) -> Path:
    """Copy css, js and font into ``dest`` (self-hosted output: ``<out>/assets/``)."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in ASSET_FILES:
        shutil.copyfile(DIR / name, dest / name)
    return dest


def data_script(data: dict) -> str:
    """The report's data as an inert JSON block. ``<`` is escaped so no text in the data can close the tag."""
    return ('<script type="application/json" id="nc-data">'
            + json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
            + "</script>")


def page(*, title: str, bar: str, foot: str, css: str, js: str, head: str = "", data: dict | None = None,
         data_src: str | None = None, stamp_fmt: str = "Generated {time}") -> str:
    """Fill the template. Give ``data`` (embedded) or ``data_src`` (a URL the page fetches)."""
    attrs = f'data-stamp="{escape(stamp_fmt, quote=True)}"'
    if data_src:
        attrs += f' data-src="{escape(data_src, quote=True)}"'
    vals = {
        "title": escape(title), "head": head, "css": escape(css, quote=True), "js": escape(js, quote=True),
        "bar": bar, "foot": foot, "app_attrs": attrs,
        "data_link": f'<a href="{escape(data_src, quote=True)}">{escape(data_src)}</a>' if data_src else "embedded in this file",
        "data": data_script(data) if data is not None else "",
    }
    return re.sub(r"\{\{(\w+)\}\}", lambda m: vals[m.group(1)], template())
