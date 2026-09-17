from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    hook = (
        Path(
            subprocess.run(
                [
                    "git",
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-path",
                    "hooks",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        / "pre-push"
    )
    source = root / "scripts/pre-push"
    if (hook.exists() or hook.is_symlink()) and (
        not hook.is_symlink() or hook.resolve() != source
    ):
        raise SystemExit(
            f"Preserving existing hook {hook}; review it before installation"
        )
    subprocess.run(
        [str(root / ".venv/bin/pre-commit"), "install", "--install-hooks"],
        cwd=root,
        check=True,
    )
    hook.parent.mkdir(parents=True, exist_ok=True)
    if not hook.is_symlink():
        hook.symlink_to(source)
    policy = tomllib.loads((root / "release/public-repository.toml").read_text())
    for relative, template in policy["local_records"]["scaffolds"].items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("x", encoding="utf-8") as stream:
                stream.write((root / template).read_text(encoding="utf-8"))
        except FileExistsError:
            pass
    print("Installed commit/message/push hooks; existing local records preserved.")


if __name__ == "__main__":
    main()
