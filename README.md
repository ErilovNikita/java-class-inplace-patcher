# Java `.class` in-place patcher

This is a deliberately narrow binary patcher for **already unpacked Java `.class` files**. It does not use ASM or reserialize a class: it parses enough of the class-file format to locate a method's `Code` attribute, then replaces equal-length byte ranges at their original file offsets.

It is intended for controlled test and maintenance workflows where you own and are authorized to modify the binary. Keep the original artifact and validate the application normally after patching.

## Guarantees and checks

- Finds a method structurally by exact JVM method name and descriptor.
- Searches `old` only in that method's `Code` byte array—not in the constant pool, metadata, or another method.
- Requires exactly one pattern occurrence, equal `old`/`new` sizes, one matching method with `Code`, and no overlapping patches.
- Selects an exact version by the SHA-256 of the original JAR, or accepts an explicit ID. An explicit ID still enforces its configured JAR hash.
- Creates `<file>.bak` before writing by default, verifies every new range after writing, parses the result, and attempts rollback on a failed write.
- `--dry-run` performs all selection and matching checks without creating a backup or changing bytes.

## Install and run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# First validate the exact target. No bytes are written.
.venv/bin/python -m classpatcher --class-file /path/to/unpacked/Foo.class \
  --source-jar /path/to/original-app.jar \
  --config examples/patches.yaml --version production-1.2.3 --dry-run

# Then patch it. A Foo.class.bak backup is created.
.venv/bin/python -m classpatcher --class-file /path/to/unpacked/Foo.class \
  --source-jar /path/to/original-app.jar \
  --config examples/patches.yaml --version production-1.2.3
```

`--source-jar` is used only to identify and verify the original artifact; it is never modified. Omit `--version` only when the configuration contains a unique JAR hash match. A pre-existing backup causes a safe refusal; explicitly use `--overwrite-backup` only when it is acceptable to replace it. `--no-backup` exists for temporary test copies.

## Install as a system command

For a convenient `classpatch` command available from any directory, install this project with [pipx](https://pipx.pypa.io/). It creates an isolated environment for the command and its dependencies.

On macOS with Homebrew:

```bash
brew install pipx
pipx ensurepath
```

Open a new terminal, then install the local project (replace the path with the directory containing this README):

```bash
pipx install /absolute/path/to/java-class-inplace-patcher
```

The tool can then be run without `python -m` or activating a virtual environment:

```bash
classpatch --jar /path/to/app.jar \
  --output /path/to/app-patched.jar \
  --config /path/to/patches.yaml
```

After changing the project source, refresh the installed command:

```bash
pipx reinstall classpatch
```

## YAML format

`old` and `new` are hexadecimal bytes separated by spaces or not. Their lengths must match. If `new` is omitted entirely, the patcher substitutes the same number of `00` bytes. JVM descriptors use the usual format: `()Z` is a no-argument boolean method; `(Ljava/lang/String;)V` accepts a string and returns void.

```yaml
versions:
  app-2.4.1:
    sha256: "64-lowercase-hex-characters-for-the-original-JAR"
    patches:
      - id: force-debug
        class: com/example/Configuration
        method:
          name: isDebugEnabled
          descriptor: "()Z"
        old: "03 ac" # iconst_0, ireturn
        new: "04 ac" # iconst_1, ireturn
```

For an equal-length zero replacement, omit `new`:

```yaml
        old: "aa bb cc"
        # becomes: 00 00 00
```

One version can contain many patches, including multiple methods. The hash applies to the original JAR before any patches in that version are made. Compute it with `shasum -a 256 /path/to/app.jar` and pass that same file as `--source-jar`.

## JAR/ZIP limitation

This project has a JAR rewrite mode for **unsigned JARs**:

```bash
.venv/bin/python -m classpatcher --jar /path/to/app.jar \
  --output /path/to/app-patched.jar \
  --config examples/patches.yaml
```

It reads the source archive, validates the JAR SHA-256 from YAML, changes only configured class entries in memory, writes a separate output JAR, then verifies the changed bytes. It deliberately refuses to overwrite an existing output unless `--overwrite-output` is given.

This is still a ZIP rewrite, **not** a direct in-place edit of a compressed member. Changing compressed entry contents can change compressed size, CRC-32, local headers, and central-directory offsets.

Signed JARs are refused before output is written. A class change invalidates the signed entry digest; rebuilding cannot preserve the original valid signature. Obtain a properly signed replacement or re-sign with an authorized key outside this tool.

## Tests

The test suite compiles a small fixture with `javac` when available, then tests dry-run, backup creation, fixed length, class parsing, and CLI guardrails.

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

Tests that require Java are skipped when `javac` is unavailable.
