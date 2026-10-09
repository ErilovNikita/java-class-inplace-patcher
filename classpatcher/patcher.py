from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
from typing import Any

import yaml

from .classfile import ClassFormatError, parse_class


class PatchError(ValueError):
    pass


@dataclass(frozen=True)
class AppliedPatch:
    label: str
    offset: int
    old: bytes
    new: bytes


def parse_hex(value: Any, field: str) -> bytes:
    if not isinstance(value, str):
        raise PatchError(f"{field} must be a hex string")
    compact = "".join(value.split()).replace("_", "")
    try:
        result = bytes.fromhex(compact)
    except ValueError as exc:
        raise PatchError(f"{field} is not valid hexadecimal") from exc
    if not result:
        raise PatchError(f"{field} must not be empty")
    return result


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PatchError(f"cannot read config: {exc}") from exc
    except yaml.YAMLError as exc:
        raise PatchError(f"invalid YAML: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("versions"), dict):
        raise PatchError("YAML must contain a mapping named versions")
    return config


def choose_version(config: dict[str, Any], artifact_bytes: bytes | None, requested: str | None) -> tuple[str, dict[str, Any]]:
    versions = config["versions"]
    if requested:
        item = versions.get(requested)
        if not isinstance(item, dict):
            raise PatchError(f"unknown version: {requested}")
        expected = item.get("sha256")
        if expected:
            if artifact_bytes is None:
                raise PatchError("selected version has sha256; provide --source-jar to verify the original JAR")
            if expected.lower() != hashlib.sha256(artifact_bytes).hexdigest():
                raise PatchError(f"SHA-256 of source JAR does not match requested version {requested}")
        return requested, item
    if artifact_bytes is None:
        raise PatchError("version auto-selection requires --source-jar")
    digest = hashlib.sha256(artifact_bytes).hexdigest()
    matches = [(name, value) for name, value in versions.items()
               if isinstance(value, dict) and str(value.get("sha256", "")).lower() == digest]
    if len(matches) != 1:
        raise PatchError("could not select exactly one version by SHA-256; use --version")
    return matches[0]


def _find_all(haystack: bytes, needle: bytes) -> list[int]:
    found, start = [], 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return found
        found.append(index)
        start = index + 1


def plan_patches(class_bytes: bytes, version: dict[str, Any]) -> list[AppliedPatch]:
    parsed = parse_class(class_bytes)
    patches = version.get("patches")
    if not isinstance(patches, list) or not patches:
        raise PatchError("selected version must contain a non-empty patches list")
    staged = bytearray(class_bytes)
    ranges: list[range] = []
    applied: list[AppliedPatch] = []
    for index, spec in enumerate(patches, 1):
        if not isinstance(spec, dict):
            raise PatchError(f"patch #{index} must be a mapping")
        label = str(spec.get("id", f"patch-{index}"))
        expected_class = spec.get("class")
        if expected_class is not None and expected_class != parsed.internal_name:
            raise PatchError(f"{label}: expected class {expected_class}, got {parsed.internal_name}")
        method_spec = spec.get("method")
        if not isinstance(method_spec, dict):
            raise PatchError(f"{label}: method must be a mapping")
        name, descriptor = method_spec.get("name"), method_spec.get("descriptor")
        if not isinstance(name, str) or not isinstance(descriptor, str):
            raise PatchError(f"{label}: method name and descriptor are required")
        methods = [m for m in parsed.methods if m.name == name and m.descriptor == descriptor]
        if len(methods) != 1:
            raise PatchError(f"{label}: expected exactly one Code method {name}{descriptor}, found {len(methods)}")
        old = parse_hex(spec.get("old"), f"{label}.old")
        # Omitting `new` is concise YAML for neutralising an equal-length
        # instruction sequence. An explicitly supplied new value remains
        # validated as hex and must still have the same length as old.
        new = bytes(len(old)) if "new" not in spec else parse_hex(spec.get("new"), f"{label}.new")
        if len(old) != len(new):
            raise PatchError(f"{label}: old and new must have equal length")
        method = methods[0]
        code = bytes(staged[method.code_offset:method.code_offset + method.code_length])
        matches = _find_all(code, old)
        if len(matches) != 1:
            raise PatchError(f"{label}: pattern must occur exactly once inside {name}{descriptor}; found {len(matches)}")
        offset = method.code_offset + matches[0]
        patch_range = range(offset, offset + len(old))
        if any(set(patch_range).intersection(previous) for previous in ranges):
            raise PatchError(f"{label}: overlaps an earlier patch")
        if bytes(staged[offset:offset + len(old)]) != old:
            raise PatchError(f"{label}: old bytes changed while planning")
        staged[offset:offset + len(new)] = new
        ranges.append(patch_range)
        applied.append(AppliedPatch(label, offset, old, new))
    return applied


def patch_file(target: Path, config_path: Path, version_name: str | None, dry_run: bool,
               backup: bool, overwrite_backup: bool, source_jar: Path | None = None) -> tuple[str, list[AppliedPatch]]:
    try:
        original = target.read_bytes()
    except OSError as exc:
        raise PatchError(f"cannot read class file: {exc}") from exc
    artifact_bytes = None
    if source_jar is not None:
        try:
            artifact_bytes = source_jar.read_bytes()
        except OSError as exc:
            raise PatchError(f"cannot read source JAR: {exc}") from exc
    config = load_config(config_path)
    selected_name, selected = choose_version(config, artifact_bytes, version_name)
    plan = plan_patches(original, selected)
    if dry_run:
        return selected_name, plan
    backup_path = Path(str(target) + ".bak")
    if backup:
        if backup_path.exists() and not overwrite_backup:
            raise PatchError(f"backup already exists: {backup_path} (use --overwrite-backup)")
        try:
            shutil.copy2(target, backup_path)
        except OSError as exc:
            raise PatchError(f"cannot create backup: {exc}") from exc
    try:
        with target.open("r+b") as stream:
            for item in plan:
                stream.seek(item.offset)
                if stream.read(len(item.old)) != item.old:
                    raise PatchError(f"{item.label}: on-disk bytes changed before write")
                stream.seek(item.offset)
                stream.write(item.new)
            stream.flush()
        result = target.read_bytes()
        parse_class(result)  # format-level verification after mutation
        for item in plan:
            if result[item.offset:item.offset + len(item.new)] != item.new:
                raise PatchError(f"{item.label}: verification failed")
    except Exception:
        # Best-effort rollback protects against partial writes; the backup remains too.
        try:
            target.write_bytes(original)
        except OSError:
            pass
        raise
    return selected_name, plan
