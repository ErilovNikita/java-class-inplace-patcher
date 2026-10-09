"""Safe JAR rewrite mode.

ZIP members are decompressed and recompressed by zipfile; this is intentionally
not described as an in-place ZIP edit. Signed JARs are refused because changing
class bytes invalidates their signatures.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import os
from pathlib import Path
import tempfile
import zipfile

from .patcher import AppliedPatch, PatchError, choose_version, load_config, plan_patches


@dataclass(frozen=True)
class JarAppliedPatch:
    entry: str
    patch: AppliedPatch


def _signature_entries(names: list[str]) -> list[str]:
    result = []
    for name in names:
        upper = name.upper()
        if not upper.startswith("META-INF/"):
            continue
        base = upper.rsplit("/", 1)[-1]
        if base.endswith((".SF", ".RSA", ".DSA", ".EC")) or base.startswith("SIG-"):
            result.append(name)
    return result


def patch_jar(source: Path, output: Path, config_path: Path, version_name: str | None,
              overwrite_output: bool = False) -> tuple[str, list[JarAppliedPatch]]:
    if source.resolve() == output.resolve():
        raise PatchError("source and output JAR paths must differ; use --in-place for replacement")
    try:
        original_jar = source.read_bytes()
    except OSError as exc:
        raise PatchError(f"cannot read JAR: {exc}") from exc
    config = load_config(config_path)
    selected_name, version = choose_version(config, original_jar, version_name)
    patches = version.get("patches")
    if not isinstance(patches, list) or not patches:
        raise PatchError("selected version must contain a non-empty patches list")
    if output.exists() and not overwrite_output:
        raise PatchError(f"output already exists: {output} (use --overwrite-output)")
    try:
        with zipfile.ZipFile(io.BytesIO(original_jar), "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            duplicates = {name for name in names if names.count(name) > 1}
            if duplicates:
                raise PatchError(f"JAR contains duplicate ZIP entries: {', '.join(sorted(duplicates))}")
            signatures = _signature_entries(names)
            if signatures:
                raise PatchError("refusing signed JAR: modifying it invalidates signature entries " + ", ".join(signatures))
            by_entry: dict[str, list[dict]] = {}
            for spec in patches:
                if not isinstance(spec, dict) or not isinstance(spec.get("class"), str):
                    raise PatchError("every JAR patch requires a string class field")
                entry = spec["class"] + ".class"
                by_entry.setdefault(entry, []).append(spec)
            replacement: dict[str, bytes] = {}
            applied: list[JarAppliedPatch] = []
            for entry, group in by_entry.items():
                if entry not in names:
                    raise PatchError(f"class entry is absent from JAR: {entry}")
                before = archive.read(entry)
                try:
                    plan = plan_patches(before, {"patches": group})
                except (PatchError, ValueError) as exc:
                    raise PatchError(f"{entry}: {exc}") from exc
                after = bytearray(before)
                for item in plan:
                    after[item.offset:item.offset + len(item.new)] = item.new
                replacement[entry] = bytes(after)
                applied.extend(JarAppliedPatch(entry, item) for item in plan)
            # Build only after every patch is successfully planned.
            output.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(output, "w") as rebuilt:
                for info in infos:
                    rebuilt.writestr(info, replacement.get(info.filename, archive.read(info.filename)),
                                     compress_type=info.compress_type)
    except zipfile.BadZipFile as exc:
        raise PatchError(f"invalid JAR/ZIP: {exc}") from exc
    # Reopen and verify class bytes in the completed archive.
    try:
        with zipfile.ZipFile(output, "r") as result:
            for item in applied:
                data = result.read(item.entry)
                offset = item.patch.offset
                if data[offset:offset + len(item.patch.new)] != item.patch.new:
                    raise PatchError(f"{item.entry}:{item.patch.label}: verification failed")
    except Exception:
        # Do not delete an output that may contain useful diagnostic evidence.
        raise
    return selected_name, applied


def patch_jar_in_place(source: Path, config_path: Path, version_name: str | None) -> tuple[str, list[JarAppliedPatch]]:
    """Safely replace an unsigned JAR without retaining an output or backup.

    A same-directory temporary file is necessary: ZIP contents must be
    rewritten, and os.replace only becomes atomic when both paths share a
    filesystem. The original is untouched until verification succeeds.
    """
    try:
        with tempfile.NamedTemporaryFile(prefix=source.name + ".", suffix=".tmp", dir=source.parent,
                                         delete=False) as temporary:
            temporary_path = Path(temporary.name)
    except OSError as exc:
        raise PatchError(f"cannot create temporary JAR beside source: {exc}") from exc
    try:
        version, applied = patch_jar(source, temporary_path, config_path, version_name, overwrite_output=True)
        os.replace(temporary_path, source)
        return version, applied
    except Exception:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
