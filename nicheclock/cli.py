"""``nicheclock <cmd>``: Core owns run / serve / doctor and wires the other slices' commands.

Other slices expose ``register(subparsers)`` which adds their subcommand(s) and calls
``parser.set_defaults(func=fn)`` with ``fn(args) -> int``. ``args.config`` is the global
``--config`` (use ``config.resolve_config_path(args.config)``).
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Callable

from nicheclock import __version__
from nicheclock.i18n import t
from nicheclock.config import (ConfigError, load_config, resolve_config_path, validate_config)

# (module, commands it provides, pip extra whose packages it needs). A module that is absent from
# this checkout is skipped; one whose optional packages are not installed gets placeholder
# commands that say which extra to install.
SLICE_MODULES = (
    ("nicheclock.onboard.cli", ("init", "preview", "edit"), "onboard"),
    ("nicheclock.llm.cli", ("llm", "eval"), "llm"),
    ("nicheclock.gsc", ("connect",), "gsc"),
    ("nicheclock.mcp_server", ("mcp",), "mcp"),
)


def _needs_extra(sub: argparse._SubParsersAction, cmds: tuple[str, ...], extra: str, missing: str) -> None:
    def fail(args: argparse.Namespace) -> int:
        print(t("cli.needs_extra", missing=missing, extra=extra), file=sys.stderr)
        return 2

    for c in cmds:
        p = sub.add_parser(c, help=t("cli.needs_extra_help", extra=extra), add_help=False)
        p.add_argument("rest", nargs="*")
        p.set_defaults(func=fail)


def _register_slices(sub: argparse._SubParsersAction) -> None:
    for modname, cmds, extra in SLICE_MODULES:
        try:
            mod = importlib.import_module(modname)
        except ModuleNotFoundError as e:
            if e.name and modname.startswith(e.name):   # the slice itself is absent
                continue
            _needs_extra(sub, cmds, extra, e.name or "?")
            continue
        reg = getattr(mod, "register", None)
        if reg is None:
            raise RuntimeError(f"{modname} has no register(subparsers)")
        reg(sub)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nicheclock", description=t("cli.description"))
    p.add_argument("--config", "-c", default=None,
                   help=t("cli.help.config"))
    p.add_argument("--version", action="version", version=f"nicheclock {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    from nicheclock import commands
    commands.register(sub)
    _register_slices(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Callable[[argparse.Namespace], int] | None = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    try:
        return int(func(args) or 0)
    except ConfigError as e:
        print(t("cli.error", message=e), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def load_for(args: argparse.Namespace, require_identity: bool = False):
    """Load + validate the config named by ``args.config``; raises ConfigError listing problems."""
    cfg = load_config(resolve_config_path(getattr(args, "config", None)))
    errs = validate_config(cfg, require_identity=require_identity)
    if errs:
        raise ConfigError(t("cli.invalid_config") + "\n  - " + "\n  - ".join(errs))
    return cfg


if __name__ == "__main__":
    raise SystemExit(main())
