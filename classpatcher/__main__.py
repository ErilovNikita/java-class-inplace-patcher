from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from .jarpatcher import patch_jar
from .patcher import PatchError, patch_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Patch an unpacked Java .class file without reserializing it.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--class-file", type=Path, help="unpacked .class file to modify")
    target.add_argument("--jar", type=Path, help="unsigned JAR to rewrite into --output")
    parser.add_argument("--config", required=True, type=Path, help="YAML patch configuration")
    parser.add_argument("--source-jar", type=Path, help="original JAR whose SHA-256 selects/verifies the version")
    parser.add_argument("--output", type=Path, help="new JAR path; required with --jar")
    parser.add_argument("--overwrite-output", action="store_true", help="allow replacing an existing --output JAR")
    parser.add_argument("--version", help="explicit version ID; otherwise select by SHA-256")
    parser.add_argument("--dry-run", action="store_true", help="validate and print planned changes without writing")
    parser.add_argument("--no-backup", action="store_true", help="do not create <class-file>.bak")
    parser.add_argument("--overwrite-backup", action="store_true", help="allow replacing an existing backup")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    if args.class_file and args.class_file.suffix != ".class":
        parser.error("--class-file must name an unpacked .class file, not a JAR/ZIP")
    if args.jar and not args.output:
        parser.error("--output is required with --jar")
    if args.jar and args.dry_run:
        parser.error("--dry-run is available only with --class-file")
    try:
        if args.jar:
            version, plan = patch_jar(args.jar, args.output, args.config, args.version, args.overwrite_output)
        else:
            version, plan = patch_file(args.class_file, args.config, args.version, args.dry_run,
                                       not args.no_backup, args.overwrite_backup, args.source_jar)
    except (PatchError, OSError, ValueError) as exc:
        logging.error("no patch applied: %s", exc)
        return 2
    action = "would patch" if args.dry_run else "patched"
    target_name = args.jar if args.jar else args.class_file
    logging.info("%s %s using version %s (%d change(s))", action, target_name, version, len(plan))
    for item in plan:
        if args.jar:
            logging.info("%s:%s: file offset 0x%X: %s -> %s", item.entry, item.patch.label, item.patch.offset,
                         item.patch.old.hex(" "), item.patch.new.hex(" "))
        else:
            logging.info("%s: file offset 0x%X: %s -> %s", item.label, item.offset, item.old.hex(" "), item.new.hex(" "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
