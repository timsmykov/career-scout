"""Packaged agent skill and explicit, non-destructive installation."""

import os
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path

from career_scout.agent_state import CommandError


def bundle():
    return files("career_scout").joinpath("skills", "career-scout")


def show():
    return {"name": "career-scout", "content": bundle().joinpath("SKILL.md").read_text()}


def install(agent, directory=None):
    if agent not in {"codex", "claude"}:
        raise CommandError("unsupported_skill_agent")
    root = (
        Path(directory).expanduser()
        if directory is not None
        else (
            Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills"
            if agent == "codex"
            else Path.home() / ".claude" / "skills"
        )
    )
    target = root / "career-scout"
    source = bundle()
    expected = {
        str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    if target.is_symlink():
        raise CommandError("skill_destination_conflict")
    if target.exists():
        if not target.is_dir() or any(p.is_symlink() for p in target.rglob("*")):
            raise CommandError("skill_destination_conflict")
        actual = {
            str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()
        }
        if actual != expected:
            raise CommandError("skill_destination_conflict")
        return {
            "name": "career-scout",
            "path": str(target.absolute()),
            "installed": False,
            "already_current": True,
        }
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".career-scout-", dir=root))
    try:
        for relative, data in expected.items():
            dest = temporary / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        # rename fails for another installation's nonempty folder; no overwrite/merge.
        temporary.rename(target)
    except OSError:
        raise CommandError("skill_install_failed") from None
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "name": "career-scout",
        "path": str(target.absolute()),
        "installed": True,
        "already_current": False,
    }
