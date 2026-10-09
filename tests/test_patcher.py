from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from classpatcher.classfile import parse_class
from classpatcher.jarpatcher import patch_jar
from classpatcher.patcher import PatchError, patch_file


ROOT = Path(__file__).parents[1]


@pytest.fixture()
def compiled_class(tmp_path: Path) -> Path:
    javac = shutil.which("javac")
    if not javac:
        pytest.skip("javac is not available")
    source = ROOT / "fixtures" / "src" / "fixture" / "Sample.java"
    subprocess.run([javac, "-g:none", "-d", str(tmp_path), str(source)], check=True)
    return tmp_path / "fixture" / "Sample.class"


def write_config(path: Path, class_file: Path, old: str = "03 ac", new: str = "04 ac", extra: str = "") -> None:
    digest = hashlib.sha256(class_file.read_bytes()).hexdigest()
    path.write_text(f'''versions:
  known:
    sha256: "{digest}"
    patches:
      - id: enable-flag
        class: fixture/Sample
        method: {{name: enabled, descriptor: "()Z"}}
        old: "{old}"
        new: "{new}"
{extra}''', encoding="utf-8")


def source_jar(class_file: Path, tmp_path: Path) -> Path:
    jar = tmp_path / "original.jar"
    with zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(class_file, "fixture/Sample.class")
    return jar


def test_parse_and_dry_run_do_not_mutate(compiled_class: Path, tmp_path: Path) -> None:
    config = tmp_path / "patch.yaml"
    original = compiled_class.read_bytes()
    jar = source_jar(compiled_class, tmp_path)
    write_config(config, jar)
    version, plan = patch_file(compiled_class, config, None, True, True, False, jar)
    assert version == "known"
    assert len(plan) == 1
    assert compiled_class.read_bytes() == original
    assert not Path(str(compiled_class) + ".bak").exists()


def test_patch_in_place_and_backup(compiled_class: Path, tmp_path: Path) -> None:
    config = tmp_path / "patch.yaml"
    original = compiled_class.read_bytes()
    jar = source_jar(compiled_class, tmp_path)
    write_config(config, jar)
    _, plan = patch_file(compiled_class, config, None, False, True, False, jar)
    changed = compiled_class.read_bytes()
    assert len(changed) == len(original)
    assert Path(str(compiled_class) + ".bak").read_bytes() == original
    assert changed[plan[0].offset:plan[0].offset + 2] == bytes.fromhex("04 ac")
    parsed = parse_class(changed)
    assert parsed.internal_name == "fixture/Sample"
    assert any(method.name == "label" and method.descriptor == "()Ljava/lang/String;" for method in parsed.methods)


def test_rejects_nonunique_pattern_inside_method(compiled_class: Path, tmp_path: Path) -> None:
    config = tmp_path / "patch.yaml"
    jar = source_jar(compiled_class, tmp_path)
    digest = hashlib.sha256(jar.read_bytes()).hexdigest()
    config.write_text(f'''versions:
  known:
    sha256: "{digest}"
    patches:
      - id: ambiguous-zero
        class: fixture/Sample
        method: {{name: repeated, descriptor: "()I"}}
        old: "03"
        new: "04"
''', encoding="utf-8")
    with pytest.raises(PatchError, match="occur exactly once"):
        patch_file(compiled_class, config, None, True, True, False, jar)


def test_rejects_length_change(compiled_class: Path, tmp_path: Path) -> None:
    config = tmp_path / "patch.yaml"
    jar = source_jar(compiled_class, tmp_path)
    write_config(config, jar, old="03 ac", new="04 00 ac")
    with pytest.raises(PatchError, match="equal length"):
        patch_file(compiled_class, config, None, True, True, False, jar)


def test_omitted_new_means_equal_length_zero_bytes(compiled_class: Path, tmp_path: Path) -> None:
    jar = source_jar(compiled_class, tmp_path)
    digest = hashlib.sha256(jar.read_bytes()).hexdigest()
    config = tmp_path / "patch.yaml"
    config.write_text(f'''versions:
  known:
    sha256: "{digest}"
    patches:
      - id: zero-flag
        class: fixture/Sample
        method: {{name: enabled, descriptor: "()Z"}}
        old: "03 ac"
''', encoding="utf-8")
    _, plan = patch_file(compiled_class, config, None, True, True, False, jar)
    assert plan[0].new == b"\x00\x00"


def test_cli_rejects_jar_extension(tmp_path: Path) -> None:
    result = subprocess.run([sys.executable, "-m", "classpatcher", "--class-file", str(tmp_path / "x.jar"),
                             "--config", str(tmp_path / "x.yaml")], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 2
    assert "unpacked .class" in result.stderr


def test_rewrites_unsigned_jar(compiled_class: Path, tmp_path: Path) -> None:
    source = source_jar(compiled_class, tmp_path)
    config = tmp_path / "patch.yaml"
    write_config(config, source)
    output = tmp_path / "patched.jar"
    _, applied = patch_jar(source, output, config, None)
    assert len(applied) == 1
    with zipfile.ZipFile(output) as archive:
        assert b"\x04\xac" in archive.read("fixture/Sample.class")


def test_refuses_signed_jar(compiled_class: Path, tmp_path: Path) -> None:
    source = tmp_path / "signed.jar"
    with zipfile.ZipFile(source, "w") as archive:
        archive.write(compiled_class, "fixture/Sample.class")
        archive.writestr("META-INF/TEST.SF", "signature metadata")
        archive.writestr("META-INF/TEST.RSA", b"not-a-real-certificate")
    config = tmp_path / "patch.yaml"
    write_config(config, source)
    with pytest.raises(PatchError, match="signed JAR"):
        patch_jar(source, tmp_path / "out.jar", config, None)
