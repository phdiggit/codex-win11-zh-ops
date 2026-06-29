from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CleanupProfile:
    name: str
    patterns: tuple[str, ...]
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    rel_path: str
    kind: str


@dataclass(frozen=True)
class CleanupPlan:
    target: Path
    profile: CleanupProfile
    candidates: tuple[CleanupCandidate, ...]


BUILTIN_GENERATED_PROFILES = {
    "markdown-exports": CleanupProfile(
        name="markdown-exports",
        patterns=("exports/markdown_views/**",),
        exclude=("**/.gitkeep",),
    ),
}


def _normalize_pattern(pattern: str) -> str:
    value = pattern.replace("\\", "/").strip()
    if not value:
        raise ValueError("cleanup pattern must not be empty")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"cleanup pattern must be relative: {pattern}")
    parts = [part for part in value.split("/") if part not in {"", "."}]
    if ".." in parts:
        raise ValueError(f"cleanup pattern must not contain '..': {pattern}")
    return "/".join(parts)


def _load_config_profiles(config_path: str | Path | None) -> dict[str, Any]:
    if config_path is None:
        return {}
    path = Path(config_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("cleanup config must contain an object at 'profiles'")
    return profiles


def _profile_from_spec(name: str, spec: Any, config_profiles: dict[str, Any]) -> CleanupProfile:
    if not isinstance(spec, dict):
        raise ValueError(f"cleanup profile must be an object: {name}")

    extends = spec.get("extends")
    if extends:
        if not isinstance(extends, str):
            raise ValueError(f"cleanup profile 'extends' must be a string: {name}")
        if extends in BUILTIN_GENERATED_PROFILES:
            base = BUILTIN_GENERATED_PROFILES[extends]
        elif extends in config_profiles and extends != name:
            base = _profile_from_spec(extends, config_profiles[extends], config_profiles)
        else:
            raise ValueError(f"unknown cleanup profile to extend: {extends}")
        patterns = [*base.patterns, *_as_string_list(spec.get("patterns", []), "patterns")]
        exclude = [*base.exclude, *_as_string_list(spec.get("exclude", spec.get("excludes", [])), "exclude")]
    else:
        patterns = _as_string_list(spec.get("patterns", []), "patterns")
        exclude = _as_string_list(spec.get("exclude", spec.get("excludes", [])), "exclude")

    if not patterns:
        raise ValueError(f"cleanup profile has no patterns: {name}")
    return CleanupProfile(
        name=name,
        patterns=tuple(_normalize_pattern(pattern) for pattern in patterns),
        exclude=tuple(_normalize_pattern(pattern) for pattern in exclude),
    )


def _as_string_list(value: Any, label: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"cleanup profile '{label}' must be a string or string list")
    return list(value)


def load_generated_profile(
    name: str,
    *,
    config_path: str | Path | None = None,
    extra_patterns: tuple[str, ...] = (),
    extra_excludes: tuple[str, ...] = (),
) -> CleanupProfile:
    config_profiles = _load_config_profiles(config_path)
    if name in config_profiles:
        profile = _profile_from_spec(name, config_profiles[name], config_profiles)
    elif name in BUILTIN_GENERATED_PROFILES:
        profile = BUILTIN_GENERATED_PROFILES[name]
    else:
        raise ValueError(f"unknown cleanup profile: {name}")

    patterns = (*profile.patterns, *(_normalize_pattern(pattern) for pattern in extra_patterns))
    exclude = (*profile.exclude, *(_normalize_pattern(pattern) for pattern in extra_excludes))
    return CleanupProfile(name=profile.name, patterns=patterns, exclude=exclude)


def _excluded(rel_path: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        if pattern.endswith("/**"):
            prefix = pattern[:-3].rstrip("/")
            if rel_path == prefix or rel_path.startswith(prefix + "/"):
                return True
        if rel_path == pattern.rstrip("/"):
            return True
    return False


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def plan_generated_cleanup(
    target: str | Path,
    *,
    profile_name: str = "markdown-exports",
    config_path: str | Path | None = None,
    extra_patterns: tuple[str, ...] = (),
    extra_excludes: tuple[str, ...] = (),
) -> CleanupPlan:
    target_path = Path(target).resolve()
    if not target_path.exists() or not target_path.is_dir():
        raise ValueError(f"cleanup target is not a directory: {target}")

    profile = load_generated_profile(
        profile_name,
        config_path=config_path,
        extra_patterns=extra_patterns,
        extra_excludes=extra_excludes,
    )
    candidates: dict[Path, CleanupCandidate] = {}
    for pattern in profile.patterns:
        for path in target_path.glob(pattern):
            if path == target_path:
                continue
            resolved = path.resolve(strict=False)
            if not _is_relative_to(resolved, target_path):
                continue
            rel_path = path.relative_to(target_path).as_posix()
            if _excluded(rel_path, profile.exclude):
                continue
            if path.is_symlink():
                kind = "symlink"
            elif path.is_dir():
                kind = "dir"
            elif path.is_file():
                kind = "file"
            else:
                continue
            candidates[path] = CleanupCandidate(path=path, rel_path=rel_path, kind=kind)

    ordered = tuple(sorted(candidates.values(), key=lambda item: item.rel_path))
    return CleanupPlan(target=target_path, profile=profile, candidates=ordered)


def apply_generated_cleanup(plan: CleanupPlan) -> dict[str, Any]:
    deleted: list[str] = []
    skipped: list[dict[str, str]] = []

    files = [candidate for candidate in plan.candidates if candidate.kind in {"file", "symlink"}]
    dirs = [candidate for candidate in plan.candidates if candidate.kind == "dir"]

    for candidate in files:
        if not candidate.path.exists() and not candidate.path.is_symlink():
            skipped.append({"path": candidate.rel_path, "reason": "missing"})
            continue
        candidate.path.unlink()
        deleted.append(candidate.rel_path)

    for candidate in sorted(dirs, key=lambda item: item.rel_path.count("/"), reverse=True):
        try:
            candidate.path.rmdir()
        except OSError:
            skipped.append({"path": candidate.rel_path, "reason": "directory not empty"})
        else:
            deleted.append(candidate.rel_path)

    return {"deleted": deleted, "skipped": skipped}


def cleanup_plan_to_dict(plan: CleanupPlan, *, apply: bool, result: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "mode": "apply" if apply else "dry-run",
        "target": str(plan.target),
        "profile": plan.profile.name,
        "patterns": list(plan.profile.patterns),
        "exclude": list(plan.profile.exclude),
        "candidates": [{"path": item.rel_path, "kind": item.kind} for item in plan.candidates],
        "candidate_count": len(plan.candidates),
    }
    if result is not None:
        data["result"] = result
    return data
