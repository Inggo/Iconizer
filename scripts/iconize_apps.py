#!/usr/bin/env python3
"""Build a white-outline icon theme for the apps you actually use.

Not part of the iconizer package: this is Linux desktop glue that happens to
call the pipeline. It imports cairosvg, so run it through the `preview` extra.

    uv run --extra preview python scripts/iconize_apps.py

Sources are resolved per app, converted with mode=outline, and written into a
personal icon theme that inherits from your current one, so anything not
generated keeps falling back to the stock icons.

If you like your existing theme and only want the apps it has no icon for,
build with --gaps-only; add --prune to clear out anything an earlier run
generated for an app your theme already draws. To hand one app back, use
--skip NAME, which deletes its generated icon and remembers the decision.

Only artwork an application ships with is traced. Your active theme and every
theme it inherits from are skipped on purpose: our theme falls back to them, so
an icon they already draw is on screen either way, and re-outlining a finished
drawing only degrades it. Expect most apps to be reported as "already drawn by
your theme" -- that is the script working, not failing.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from iconizer.config import Config
from iconizer.pipeline import iconize_file

HOME = Path.home()
THEME_NAME = "Iconizer-Noir"
THEME_DIR = HOME / ".local/share/icons" / THEME_NAME
OVERRIDES = HOME / ".config/iconize-apps/overrides.json"
ACTIVITY_DB = HOME / ".local/share/kactivitymanagerd/resources/database"

# Blank border added around source art, as a fraction of its short side.
PAD_FRACTION = 0.06

DESKTOP_DIRS = [
    HOME / ".local/share/applications",
    Path("/usr/share/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
    HOME / ".local/share/flatpak/exports/share/applications",
    Path("/var/lib/snapd/desktop/applications"),
]
ICON_ROOTS = [
    HOME / ".local/share/icons",
    Path("/usr/share/icons"),
    Path("/usr/share/pixmaps"),
    Path("/var/lib/flatpak/exports/share/icons"),
    HOME / ".local/share/flatpak/exports/share/icons",
    Path("/var/lib/snapd/desktop/icons"),
]

# Themes whose own art we never want to trace -- tracing an already-monochrome
# Breeze glyph produces a smudge, not an outline. Only app-shipped artwork
# (hicolor, pixmaps, flatpak exports) makes a good source. This is a hint for
# themes that happen to be installed; the authoritative rule is the active
# fallback chain, see blocked_roots().
SOURCE_BLOCKLIST = ("breeze", "adwaita", "oxygen", "noir", "chameleon", "vivid", "kde_classic")

# hicolor and pixmaps are where applications install their own artwork rather
# than designed themes, and every inheritance chain ends there, so they stay
# traceable no matter what the active theme inherits.
NEVER_BLOCKED = ("hicolor", "pixmaps")


def load_overrides() -> dict:
    if OVERRIDES.exists():
        return json.loads(OVERRIDES.read_text())
    return {}


def save_overrides(data: dict) -> None:
    OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def used_apps() -> list[str]:
    """Desktop ids from KDE's activity tracker: favourites plus anything used."""
    if not ACTIVITY_DB.exists():
        return []
    con = sqlite3.connect(f"file:{ACTIVITY_DB}?mode=ro", uri=True)
    found: list[str] = []
    for table, column in (
        ("ResourceLink", "targettedResource"),
        ("ResourceScoreCache", "targettedResource"),
    ):
        try:
            rows = con.execute(f"SELECT DISTINCT {column} FROM {table}").fetchall()
        except sqlite3.Error:
            continue
        for (value,) in rows:
            value = value.removeprefix("applications:")
            if value.endswith(".desktop"):
                found.append(value)
    con.close()
    return sorted(set(found))


def category_apps(category: str) -> list[str]:
    """Desktop ids in a freedesktop category, e.g. Game, Graphics, Office.

    Matched case-insensitively against whole Categories= entries, so "Game"
    selects Game but never GameStore. A user entry shadows the system one of
    the same name, the same way the desktop itself resolves them.
    """
    wanted = category.casefold()
    found: dict[str, None] = {}
    for directory in DESKTOP_DIRS:
        if not directory.exists():
            continue
        for entry in sorted(directory.glob("*.desktop")):
            if entry.name in found:
                continue
            raw = desktop_field(entry, "Categories") or ""
            if any(part.strip().casefold() == wanted for part in raw.split(";")):
                found[entry.name] = None
    return list(found)


def desktop_path(desktop_id: str) -> Path | None:
    for directory in DESKTOP_DIRS:
        candidate = directory / desktop_id
        if candidate.exists():
            return candidate
    return None


def desktop_field(desktop: Path, key: str) -> str | None:
    for line in desktop.read_text(errors="replace").splitlines():
        if line.startswith(f"{key}="):
            return line.partition("=")[2].strip()
    return None


def icon_name(desktop: Path) -> str | None:
    return desktop_field(desktop, "Icon")


def _rank(path: Path) -> tuple[int, int]:
    """Prefer scalable art, then the largest raster. Higher sorts first."""
    size = 0
    for part in path.parts:
        head = part.split("x")[0]
        if head.isdigit():
            size = max(size, int(head))
    if path.suffix.lower() == ".svg":
        # A 22px Breeze SVG is drawn for 22px: still vector, but far simpler
        # than the same icon's scalable or 64px drawing. Prefer the richest.
        return (2, 10_000 if "scalable" in path.parts else size)
    return (1, size)


def theme_inherits(name: str) -> list[str]:
    """The Inherits= line of an installed theme, or nothing if it has none."""
    for root in ICON_ROOTS:
        index = root / name / "index.theme"
        if not index.exists():
            continue
        for line in index.read_text(errors="replace").splitlines():
            if line.startswith("Inherits="):
                return [p.strip() for p in line.partition("=")[2].split(",") if p.strip()]
        return []
    return []


def base_theme() -> str:
    """The user's theme, seeing past our own when it is already applied."""
    name = current_theme()
    if name != THEME_NAME:
        return name
    index = THEME_DIR / "index.theme"
    if index.exists():
        for line in index.read_text(errors="replace").splitlines():
            if line.startswith("Inherits="):
                return line.partition("=")[2].strip() or "breeze-dark"
    return "breeze-dark"


def fallback_chain(start: str) -> list[str]:
    """Theme names already reachable by inheritance, active theme first."""
    seen: list[str] = []
    queue = [start]
    while queue:
        name = queue.pop(0)
        if name in seen or name.lower() in NEVER_BLOCKED:
            continue
        seen.append(name)
        queue.extend(theme_inherits(name))
    return seen


@functools.lru_cache(maxsize=1)
def blocked_roots() -> tuple[Path, ...]:
    """Theme directories that are never worth tracing.

    Our theme inherits from the user's, so anything these provide is already
    on screen. Tracing one re-outlines a finished drawing -- an already
    monochrome set comes back as a scribble, and outline() has nothing left to
    find -- for a result strictly worse than the fallback we get for free.
    Skipping the icon entirely lets the real theme show through instead.
    """
    found: list[Path] = [THEME_DIR]
    for name in fallback_chain(base_theme()):
        for root in ICON_ROOTS:
            candidate = root / name
            if candidate.is_dir():
                found.append(candidate)
    return tuple(found)


def theme_covering(name: str) -> str | None:
    """Which already-active theme draws NAME, when that is why we skipped it.

    An absolute Icon= path is read straight off disk and no theme can supply
    it, so the question does not apply.
    """
    if not name or name.startswith("/"):
        return None
    for directory in blocked_roots():
        if directory == THEME_DIR:
            continue
        for suffix in (".svg", ".png", ".xpm"):
            if next(directory.rglob(f"{name}{suffix}"), None):
                return directory.name
    return None


def find_source(name: str) -> Path | None:
    """Locate the app's own artwork for an Icon= name."""
    if name.startswith("/"):
        path = Path(name)
        return path if path.exists() else None

    candidates: list[Path] = []
    for root in ICON_ROOTS:
        if not root.exists():
            continue
        for suffix in (".svg", ".png", ".xpm"):
            candidates.extend(root.rglob(f"{name}{suffix}"))

    # Drop our own output and every theme already in the fallback chain. Ours
    # matters because after --rewrite-desktop an entry points at a themed name
    # we generated, so a rebuild would otherwise re-trace the last result and
    # lose a little more of the drawing on every run.
    blocked = blocked_roots()
    candidates = [
        c for c in candidates
        if not any(directory in c.parents for directory in blocked)
    ]

    own = [
        c for c in candidates
        if not any(bad in str(c).lower() for bad in SOURCE_BLOCKLIST)
    ]
    # An app's own artwork traces best. Generic freedesktop names (utilities-
    # terminal, accessories-calculator) only ever exist inside a theme, so fall
    # back to that rather than skipping the app entirely -- but only when no
    # active theme draws the name. Tracing some other theme's glyph for an icon
    # the user already sees is the smudge case twice over.
    usable = own or ([] if theme_covering(name) else candidates)
    if not usable:
        return None
    return max(usable, key=_rank)


def flatten(source: Path, target: Path) -> Path:
    """Composite transparency onto whichever backdrop the art contrasts with.

    load_image() always flattens alpha onto white, which erases the light-on-
    transparent artwork that a lot of themed icons use -- the mask comes back
    empty and the icon traces to zero paths. Deciding the backdrop from the
    art's own luminance keeps both polarities visible; ink_mask still decides
    figure/ground for itself afterwards.
    """
    import cv2
    import numpy as np

    image = cv2.imread(str(source), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"unreadable image: {source}")
    if image.ndim == 3 and image.shape[2] == 4:
        alpha = image[:, :, 3:4] / 255.0
        rgb = image[:, :, :3].astype(np.float64)
        # White is what the rest of the pipeline assumes: _border_is_inked
        # treats a dark frame as ink and flips figure/ground, so a black
        # backdrop turns the whole icon into "subject" and traces its outer
        # edge. Only near-white art, which white would erase outright, is
        # worth that risk.
        visible = alpha[:, :, 0] > 0.5
        backdrop = 255.0
        if visible.any() and rgb[visible].mean() > 200:
            backdrop = 0.0
        image = (rgb * alpha + backdrop * (1.0 - alpha)).astype(np.uint8)
    else:
        backdrop = 255.0

    # App art usually runs to the very edge of its canvas (a rounded-rect badge
    # fills the frame). outline() takes a morphological gradient, so a boundary
    # flush with the edge loses the half of its stroke that falls outside and
    # traces as a clipped L. A margin gives the outline room to close.
    margin = max(8, int(min(image.shape[:2]) * PAD_FRACTION))
    image = cv2.copyMakeBorder(
        image, margin, margin, margin, margin,
        cv2.BORDER_CONSTANT, value=(backdrop,) * 3,
    )
    cv2.imwrite(str(target), image)
    return target


def rasterise(source: Path, workdir: Path) -> Path:
    """Give the pipeline a flat RGB image it can actually threshold."""
    suffix = source.suffix.lower()
    target = workdir / f"{source.stem}.png"

    if suffix in (".svg", ".svgz"):
        import cairosvg

        # Render on transparency and let flatten() pick the backdrop.
        rendered = workdir / f"{source.stem}.raw.png"
        cairosvg.svg2png(url=str(source), write_to=str(rendered),
                         output_width=512, output_height=512)
        result = flatten(rendered, target)
        rendered.unlink(missing_ok=True)
        return result

    if suffix == ".xpm":
        rendered = workdir / f"{source.stem}.raw.png"
        subprocess.run(["magick", str(source), "-resize", "512x512", str(rendered)],
                       check=True, capture_output=True)
        result = flatten(rendered, target)
        rendered.unlink(missing_ok=True)
        return result

    return flatten(source, target)


def write_desktop_override(name: str, desktop: Path) -> Path:
    """Copy a .desktop into the user dir with Icon= pointing at our theme.

    Entries in ~/.local/share/applications shadow the system ones, so this is
    undone by deleting the copy.
    """
    target = HOME / ".local/share/applications" / desktop.name
    lines = desktop.read_text(errors="replace").splitlines()
    out, replaced = [], False
    for line in lines:
        if line.startswith("Icon=") and not replaced:
            # Keep the path to the app's real artwork: once Icon= names our
            # generated icon, it is the only remaining way to find the source
            # a later rebuild has to trace.
            out.append(f"X-Iconizer-Source={line.partition('=')[2].strip()}")
            out.append(f"Icon={name}")
            replaced = True
        elif not line.startswith("X-Iconizer-Source="):
            out.append(line)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out) + "\n")
    return target


def write_theme_index(inherits: str) -> None:
    THEME_DIR.mkdir(parents=True, exist_ok=True)
    (THEME_DIR / "index.theme").write_text(
        "[Icon Theme]\n"
        f"Name={THEME_NAME}\n"
        "Comment=White outline app icons generated by Iconizer\n"
        f"Inherits={inherits}\n"
        "Directories=apps/scalable\n"
        "\n"
        "[apps/scalable]\n"
        "Size=48\nMinSize=16\nMaxSize=512\nContext=Applications\nType=Scalable\n"
    )


def current_theme() -> str:
    for tool in ("kreadconfig6", "kreadconfig5"):
        if shutil.which(tool):
            result = subprocess.run(
                [tool, "--file", "kdeglobals", "--group", "Icons", "--key", "Theme"],
                capture_output=True, text=True,
            )
            if result.stdout.strip():
                return result.stdout.strip()
    return "breeze-dark"


def refresh() -> None:
    """Drop the icon cache and let Plasma notice."""
    cache = HOME / ".cache/icon-cache.kcache"
    cache.unlink(missing_ok=True)
    if shutil.which("kbuildsycoca6"):
        subprocess.run(["kbuildsycoca6"], capture_output=True)
    elif shutil.which("kbuildsycoca5"):
        subprocess.run(["kbuildsycoca5"], capture_output=True)


def contact_sheet(entries: list[tuple[str, Path]], destination: Path,
                  labels: dict[str, str] | None = None) -> None:
    """Tile the results so wrong figure/ground is obvious at a glance.

    Labels fall back to the icon name, which for a Steam game is an app id that
    says nothing -- pass the .desktop Name= instead so the sheet is reviewable.
    """
    labels = labels or {}
    import cairosvg
    import cv2
    import numpy as np

    cell, columns = 128, 8
    rows = (len(entries) + columns - 1) // columns
    sheet = np.full((rows * (cell + 18), columns * cell, 3), 40, np.uint8)

    for index, (name, svg) in enumerate(entries):
        png = svg.with_suffix(".sheet.png")
        cairosvg.svg2png(url=str(svg), write_to=str(png),
                         output_width=cell, output_height=cell)
        tile = cv2.imread(str(png), cv2.IMREAD_UNCHANGED)
        png.unlink(missing_ok=True)
        if tile is None:
            continue
        if tile.shape[2] == 4:
            alpha = tile[:, :, 3:] / 255.0
            tile = (tile[:, :, :3] * alpha + 40 * (1 - alpha)).astype(np.uint8)
        y, x = divmod(index, columns)
        top, left = y * (cell + 18), x * cell
        sheet[top:top + cell, left:left + cell] = tile
        cv2.putText(sheet, labels.get(name, name)[:18], (left + 2, top + cell + 13),
                    cv2.FONT_HERSHEY_PLAIN, 0.7, (200, 200, 200), 1, cv2.LINE_AA)

    cv2.imwrite(str(destination), sheet)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apps", nargs="*", help="Icon names or .desktop ids (default: your used apps).")
    parser.add_argument("--detail", type=int, default=2, help="1-5, lower is bolder (default 2).")
    parser.add_argument("--color", default="white")
    parser.add_argument("--mode", default="outline", choices=("outline", "silhouette"))
    parser.add_argument("--detail-for", metavar="NAME=N", action="append", default=[],
                        help="Per-icon detail override, remembered for later runs.")
    parser.add_argument("--invert", metavar="NAME", action="append", default=[],
                        help="Force inverted polarity for NAME; remembered for later runs.")
    parser.add_argument("--no-invert", metavar="NAME", action="append", default=[],
                        help="Force normal polarity for NAME; remembered for later runs.")
    parser.add_argument("--from", dest="source", type=Path, metavar="IMAGE",
                        help="Trace IMAGE instead of the app's installed artwork. "
                             "Needs exactly one app argument; remembered for later runs.")
    parser.add_argument("--category", metavar="NAME", action="append", default=[],
                        help="Build every app in a freedesktop category, e.g. "
                             "--category Game. Repeatable; combines with named apps.")
    parser.add_argument("--skip", metavar="NAME", action="append", default=[],
                        help="Never generate NAME; deletes any icon already generated for "
                             "it so your real theme draws it again. Remembered.")
    parser.add_argument("--unskip", metavar="NAME", action="append", default=[],
                        help="Undo --skip for NAME.")
    parser.add_argument("--gaps-only", action="store_true",
                        help="Build only icons your active theme does not already draw. "
                             "Use when you are happy with your theme and only want the "
                             "apps it has no icon for.")
    parser.add_argument("--reset", metavar="NAME", action="append", default=[],
                        help="Forget all remembered tuning for NAME, including --skip.")
    parser.add_argument("--which", metavar="QUERY",
                        help="Find the icon name to use for an app, then exit.")
    parser.add_argument("--sheet", type=Path, help="Write a contact sheet PNG here.")
    parser.add_argument("--list", action="store_true", help="Show what would be built and exit.")
    parser.add_argument("--prune", action="store_true",
                        help="Delete icons in the theme that are no longer generated, so "
                             "your real theme shows through again. Ignored when apps are named.")
    parser.add_argument("--apply", action="store_true", help="Switch the desktop to the theme when done.")
    parser.add_argument("--rewrite-desktop", action="store_true",
                        help="For apps whose Icon= is an absolute path, shadow the .desktop "
                             "in ~/.local/share/applications so the generated icon is used.")
    args = parser.parse_args()

    if args.which:
        needle = args.which.lower()
        seen: set[str] = set()
        for directory in DESKTOP_DIRS:
            if not directory.exists():
                continue
            for entry in sorted(directory.glob("*.desktop")):
                if entry.name in seen:
                    continue
                text = entry.read_text(errors="replace")
                title = next((l.partition("=")[2] for l in text.splitlines()
                              if l.startswith("Name=")), "")
                if needle not in entry.stem.lower() and needle not in title.lower():
                    continue
                seen.add(entry.name)
                icon = icon_name(entry) or "(none)"
                origin = desktop_field(entry, "X-Iconizer-Source")
                note = "  [absolute path: needs --rewrite-desktop]" if icon.startswith("/") else ""
                note = "  [already themed by us]" if origin else note
                print(f"{title or entry.stem}\n    desktop: {entry.name}\n"
                      f"    icon:    {icon}{note}\n")
        if not seen:
            print(f"nothing matching {args.which!r}")
        return 0

    overrides = load_overrides()
    for name in args.reset:
        overrides.pop(name, None)
    # --list is a dry run and must leave both the overrides file and the theme
    # directory exactly as it found them, whatever else is on the command line.
    if args.source:
        if len(args.apps) != 1:
            parser.error("--from needs exactly one app argument")
        if not args.source.exists():
            parser.error(f"no such image: {args.source}")
        overrides.setdefault(args.apps[0], {})["source"] = str(args.source.resolve())
    if not args.list and (args.reset or args.source):
        save_overrides(overrides)
    for name in args.invert:
        overrides.setdefault(name, {})["invert"] = True
    for name in args.no_invert:
        overrides.setdefault(name, {})["invert"] = False
    for item in args.detail_for:
        name, _, value = item.partition("=")
        overrides.setdefault(name, {})["detail"] = int(value)
    for name in args.skip:
        overrides.setdefault(name, {})["skip"] = True
    for name in args.unskip:
        overrides.get(name, {}).pop("skip", None)
    if not args.list and (args.invert or args.no_invert or args.detail_for
                          or args.skip or args.unskip):
        save_overrides(overrides)

    # Delete right away rather than waiting for a full run: --skip is how you
    # hand an app back to your real theme, and it should take effect the moment
    # you say so.
    if not args.list:
        for name in args.skip:
            generated = THEME_DIR / "apps/scalable" / f"{name}.svg"
            if generated.exists():
                generated.unlink()
                print(f"removed {name}.svg; your theme draws it again")
        if args.skip or args.unskip:
            refresh()
    # Skipping alone is a complete request; a full rebuild here would be slow
    # and surprising. Anything else on the line means a build was wanted too.
    if args.skip and not (args.apps or args.prune or args.apply or args.sheet or args.list):
        return 0

    # Resolve the request into (icon name -> source image) pairs.
    targets: dict[str, Path] = {}
    unthemeable: dict[str, Path] = {}
    missing: list[str] = []
    covered_names: list[str] = []
    titles: dict[str, str] = {}
    # Named apps and categories combine; only a bare run falls back to the
    # activity tracker, which never lists a game you have not launched yet.
    requested = list(args.apps)
    for category in args.category:
        selected = category_apps(category)
        if not selected:
            print(f"no desktop entries in category {category!r}")
        requested.extend(selected)
    requested = list(dict.fromkeys(requested)) or used_apps()
    for item in requested:
        name = item
        desktop = None
        if item.endswith(".desktop"):
            desktop = desktop_path(item)
            if desktop is None:
                missing.append(f"{item} (no .desktop found)")
                continue
            name = icon_name(desktop) or ""
            if not name:
                missing.append(f"{item} (no Icon= line)")
                continue
            # An absolute Icon= path is read straight off disk, so no icon
            # theme can override it. Generate under a themeable name instead
            # and record that the entry needs rewriting to pick it up.
            if name.startswith("/"):
                name = item.removesuffix(".desktop")
                unthemeable[name] = desktop
            if title := desktop_field(desktop, "Name"):
                titles[name] = title
        if overrides.get(name, {}).get("skip"):
            missing.append(f"{name} (skipped)")
            continue
        chosen = overrides.get(name, {}).get("source")
        if chosen and Path(chosen).exists():
            targets[name] = Path(chosen)
            continue
        origin = desktop_field(desktop, "X-Iconizer-Source") if desktop else None
        if origin:
            lookup = origin  # entry we rewrote on an earlier run
        elif name in unthemeable:
            lookup = icon_name(desktop)
        else:
            lookup = name
        # --gaps-only takes your theme's word over the app's own artwork: an
        # icon already on screen is one you chose, so leave it alone even when
        # there is something here we could trace. Ask about `name`, not
        # `lookup`: the theme is consulted for the name we generate under, while
        # lookup may be an absolute path to the artwork behind it, which no
        # theme can ever supply.
        if args.gaps_only and (covering := theme_covering(name)):
            missing.append(f"{name} (drawn by {covering})")
            covered_names.append(name)
            continue
        source = find_source(lookup)
        if source is None:
            covered = theme_covering(name)
            missing.append(f"{name} (drawn by {covered})" if covered
                           else f"{name} (no source artwork)")
            if covered:
                covered_names.append(name)
            continue
        targets[name] = source

    if args.list:
        for name, source in sorted(targets.items()):
            flag = "  [needs --rewrite-desktop]" if name in unthemeable else ""
            print(f"{name:44} {source}{flag}")
        for item in missing:
            print(f"  skip: {item}")
        print(f"\n{len(targets)} to build, {len(missing)} skipped")
        return 0

    output_dir = THEME_DIR / "apps/scalable"
    output_dir.mkdir(parents=True, exist_ok=True)
    workdir = output_dir / ".raster"
    workdir.mkdir(exist_ok=True)

    built: list[tuple[str, Path]] = []
    failed: list[str] = []
    for name, source in sorted(targets.items()):
        try:
            raster = rasterise(source, workdir)
            tuning = overrides.get(name, {})
            config = Config(
                detail=tuning.get("detail", args.detail),
                mode=args.mode, color=args.color,
                invert=tuning.get("invert"), size=512,
            )
            icon = iconize_file(raster, config)
            destination = icon.write(output_dir / f"{name}.svg")
            built.append((name, destination))
            print(f"  {name:44} {icon.paths:4} paths  {len(icon.svg) // 1024:3} KB")
        except Exception as exc:  # keep going; one bad icon must not stop the run
            failed.append(f"{name}: {exc}")
            print(f"  {name:44} FAILED: {exc}")

    shutil.rmtree(workdir, ignore_errors=True)

    # Icons an earlier run generated under rules we no longer apply are still
    # sitting in the output directory, and they shadow the very fallback we
    # declined them for. Only names this run actually considered and declined
    # count: used_apps() reflects a rolling activity window, so an icon merely
    # absent from today's list is not stale and must survive. Saved tuning is
    # no reason to keep one -- --gaps-only declines apps you have tuned, and
    # handing those back to your theme is the point.
    declined = {m.split(" ")[0] for m in missing}
    stale = sorted(
        existing.stem for existing in output_dir.glob("*.svg")
        if existing.stem in declined
    )
    pruned: list[str] = []
    if stale and args.prune:
        for name in stale:
            (output_dir / f"{name}.svg").unlink(missing_ok=True)
            pruned.append(name)

    rewritten: list[str] = []
    if args.rewrite_desktop:
        for name in sorted(unthemeable):
            if any(name == built_name for built_name, _ in built):
                rewritten.append(str(write_desktop_override(name, unthemeable[name])))
        if rewritten and shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database",
                            str(HOME / ".local/share/applications")], capture_output=True)

    write_theme_index(base_theme())
    refresh()

    if args.sheet and built:
        contact_sheet(built, args.sheet, titles)
        print(f"\ncontact sheet: {args.sheet}")

    print(f"\n{len(built)} icons -> {THEME_DIR}")
    if covered_names:
        print(f"{len(covered_names)} already drawn by your theme, left to it: "
              + ", ".join(sorted(covered_names)))
    unsourced = [m for m in missing if m.endswith("(no source artwork)")]
    if unsourced:
        print(f"{len(unsourced)} skipped (no source): "
              + ", ".join(m.split(" ")[0] for m in unsourced))
    if failed:
        print(f"{len(failed)} failed: " + "; ".join(failed))
    if pruned:
        print(f"pruned {len(pruned)} stale icon(s): " + ", ".join(pruned))
    elif stale:
        print(f"\n{len(stale)} icon(s) in the theme are no longer generated and now "
              f"shadow your real theme: {', '.join(stale)}")
        print("Re-run with --prune to delete them.")

    pending = [n for n in unthemeable if not args.rewrite_desktop]
    if pending:
        print(f"\n{len(pending)} app(s) hardcode an absolute Icon= path and ignore icon "
              f"themes: {', '.join(sorted(pending))}")
        print("Their icons are generated but inert. Re-run with --rewrite-desktop to use them.")
    if rewritten:
        print(f"\nrewrote {len(rewritten)} .desktop entries in ~/.local/share/applications")

    if args.apply:
        applied = False
        if shutil.which("plasma-apply-icontheme"):
            applied = subprocess.run(["plasma-apply-icontheme", THEME_NAME],
                                     capture_output=True).returncode == 0
        if not applied:
            # plasma-apply-icontheme ships with plasma-workspace and is often
            # absent; writing kdeglobals and poking KIconLoader does the same.
            for tool in ("kwriteconfig6", "kwriteconfig5"):
                if shutil.which(tool):
                    subprocess.run([tool, "--file", "kdeglobals", "--group", "Icons",
                                    "--key", "Theme", THEME_NAME], capture_output=True)
                    applied = True
                    break
            refresh()
            if shutil.which("dbus-send"):
                for group in range(6):
                    subprocess.run(
                        ["dbus-send", "--session", "--type=signal", "/KIconLoader",
                         "org.kde.KIconLoader.iconChanged", f"int32:{group}"],
                        capture_output=True,
                    )
        print(f"applied theme {THEME_NAME}" if applied
              else f"could not switch themes automatically; run: plasma-apply-icontheme {THEME_NAME}")
    else:
        print(f"\nApply with:  plasma-apply-icontheme {THEME_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
