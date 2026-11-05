"""Copy one private branch's ski-conditions changes into the public repo clone.

The site deploys from the public repo (lpfitzgerald98-star/ski-conditions), whose
root mirrors projects/ski-conditions/ here. Every change lands in private first,
then the files it touched are copied across -- plain file copies, never patches
(a patch sync once left config.py half-applied and broke every import).

What it does:
  1. Lists the files the branch changed under projects/ski-conditions/
     (`git diff BASE...REF`).
  2. Checks each one's starting point: the public main copy must match the
     private BASE copy (line endings ignored). A mismatch means the repos drifted,
     and copying would silently overwrite the public side's change -- so it stops.
  3. With --apply, writes REF's version of each file into the public clone (and
     deletes files the branch deleted). It never commits, pushes, cleans or resets.

Run from anywhere:
  python projects/ski-conditions/scripts/sync_public.py            # dry run
  python projects/ski-conditions/scripts/sync_public.py --apply
Then in the public clone: branch, pytest, commit, push, open the PR.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PREFIX = "projects/ski-conditions/"
DEFAULT_PUBLIC = Path.home() / "ski-conditions-public"
PRIVATE_ONLY = {"CLAUDE.md"}   # names local paths and the private repo; never published


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=check)


def show(repo: Path, ref: str, path: str) -> bytes | None:
    """File contents at `ref`, or None when it doesn't exist there."""
    out = git(repo, "show", f"{ref}:{path}", check=False)
    return out.stdout if out.returncode == 0 else None


def same(a: bytes | None, b: bytes | None) -> bool:
    if a is None or b is None:
        return a is b
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


def changed_files(private: Path, base: str, ref: str) -> list[tuple[str, str]]:
    out = git(private, "diff", "--name-status", "--no-renames", f"{base}...{ref}", "--", PREFIX)
    rows = []
    for line in out.stdout.decode().splitlines():
        status, path = line.split("\t", 1)
        if path[len(PREFIX):] not in PRIVATE_ONLY:
            rows.append((status[0], path))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--public", type=Path, default=DEFAULT_PUBLIC, help="public repo clone")
    ap.add_argument("--ref", default="HEAD", help="private branch or commit to copy from")
    ap.add_argument("--base", default="origin/main", help="private ref the branch started from")
    ap.add_argument("--apply", action="store_true", help="write the files (default: dry run)")
    ap.add_argument("--force", action="store_true", help="copy even where the repos drifted")
    args = ap.parse_args(argv)

    private = Path(git(Path(__file__).resolve().parent, "rev-parse", "--show-toplevel")
                   .stdout.decode().strip())
    public = args.public.resolve()
    if not (public / ".git").exists():
        print(f"No public clone at {public}")
        return 2
    git(public, "fetch", "--quiet", "origin")

    rows = changed_files(private, args.base, args.ref)
    if not rows:
        print(f"No changes under {PREFIX} between {args.base} and {args.ref}.")
        return 0

    drifted = []
    for status, path in rows:
        rel = path[len(PREFIX):]
        if not same(show(private, args.base, path), show(public, "origin/main", rel)):
            drifted.append(rel)
        print(f"  {status} {rel}{'   <-- public main differs from private base' if rel in drifted else ''}")

    if drifted and not args.force:
        print(f"\n{len(drifted)} file(s) drifted between the repos. Reconcile them first "
              "(or pass --force to overwrite the public side).")
        return 1
    if not args.apply:
        print(f"\nDry run: {len(rows)} file(s) would be copied. Re-run with --apply.")
        return 0

    dirty = git(public, "status", "--porcelain").stdout.decode().strip()
    if dirty:
        print(f"Public clone has uncommitted changes; commit or stash them first:\n{dirty}")
        return 1

    for status, path in rows:
        dest = public / path[len(PREFIX):]
        if status == "D":
            dest.unlink(missing_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(show(private, args.ref, path))
    print(f"\nCopied {len(rows)} file(s) into {public}. Next: branch, pytest, commit, push, PR.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
