"""Named profiles - saved snapshots of the whole settings + cities set
that the user can switch between with one click.

The design is deliberately thin. Each profile is exactly one JSON file
under ``~/.config/earthwall/profiles/<name>.json`` in the same format as
the Export bundle, so:

- What you can save as a profile you can also export/share as a file.
- Old ``settings-backup-*.json`` files, imported bundles, and profiles
  are all the same schema and interchangeable.
- Rebuilding this feature (or writing a scratch tool that pokes profile
  files from the shell) doesn't need any code path other than the plain
  ``export_bundle`` / ``import_bundle`` helpers already in settings.py.

The one bit of state that lives outside the bundle format is which
profile is currently loaded - that gets tracked in the top-level
settings under ``active_profile`` (empty string when no profile is
loaded, so the ordinary ``settings.json`` acts as an unnamed working
draft).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import settings as settings_module


PROFILES_DIR = settings_module.CONFIG_DIR / "profiles"

# Profile names double as filenames, so restrict them the same way any
# sensible filesystem does. This also keeps the export-target name in
# the file-open dialogs predictable.
_VALID_NAME_RE = re.compile(r"^[\w\-. ]{1,64}$")


def is_valid_name(name: str) -> bool:
    """Whether `name` is safe to use as a profile filename. Rejects
    empty, over-long, and path-traversal-flavoured strings so a badly
    behaved profile name can never write outside PROFILES_DIR."""
    if not name or not isinstance(name, str):
        return False
    name = name.strip()
    if not name:
        return False
    return bool(_VALID_NAME_RE.match(name))


def _profile_path(name: str) -> Path:
    """Where the file for a given profile name lives. Callers should
    validate the name via ``is_valid_name`` first."""
    return PROFILES_DIR / f"{name}.json"


def list_profiles() -> list[str]:
    """Names (without .json) of every saved profile, sorted case-
    insensitively so the dropdown reads naturally."""
    if not PROFILES_DIR.exists():
        return []
    names = []
    for p in PROFILES_DIR.iterdir():
        if p.is_file() and p.suffix == ".json":
            names.append(p.stem)
    names.sort(key=str.lower)
    return names


def load_profile(name: str) -> tuple[dict, list[dict]]:
    """Return (settings, cities) from the named profile. Raises
    FileNotFoundError if the profile doesn't exist, or ValueError if the
    file exists but isn't a valid EarthWall bundle."""
    if not is_valid_name(name):
        raise ValueError(f"Invalid profile name: {name!r}")
    path = _profile_path(name)
    if not path.exists():
        raise FileNotFoundError(f"No profile named {name!r}")
    with open(path) as f:
        bundle = json.load(f)
    return settings_module.import_bundle(bundle)


def save_profile(name: str, settings: dict, cities: list[dict]) -> None:
    """Write the current settings + cities as the named profile,
    overwriting any existing profile with that name."""
    if not is_valid_name(name):
        raise ValueError(f"Invalid profile name: {name!r}")
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    bundle = settings_module.export_bundle(settings, cities)
    with open(_profile_path(name), "w") as f:
        json.dump(bundle, f, indent=2)


def delete_profile(name: str) -> None:
    """Remove the named profile file. No-op if it doesn't exist, so
    delete-after-load is safe."""
    if not is_valid_name(name):
        raise ValueError(f"Invalid profile name: {name!r}")
    path = _profile_path(name)
    if path.exists():
        path.unlink()


def rename_profile(old_name: str, new_name: str) -> None:
    """Rename a profile file. Raises FileNotFoundError if `old_name`
    doesn't exist, FileExistsError if `new_name` already does."""
    if not is_valid_name(old_name) or not is_valid_name(new_name):
        raise ValueError("Invalid profile name.")
    src = _profile_path(old_name)
    dst = _profile_path(new_name)
    if not src.exists():
        raise FileNotFoundError(f"No profile named {old_name!r}")
    if dst.exists():
        raise FileExistsError(f"A profile named {new_name!r} already exists")
    src.rename(dst)
