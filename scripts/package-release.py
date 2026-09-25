"""Build a reproducible source release from the current files, including uncommitted work."""

import gzip
import hashlib
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "syncflow-v1.0.0"


def main():
    # Explicit inputs keep local credentials, uploads, dependencies and Git out.
    inputs = [
        ".dockerignore",
        ".env.example",
        ".gitignore",
        "README.md",
        "compose.yaml",
        "compose.test.yaml",
        "backend/app",
        "backend/tests",
        "backend/pyproject.toml",
        "docker",
        "frontend/src",
        "frontend/index.html",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/tsconfig.json",
        "frontend/vite.config.ts",
        "scripts",
        "docs",
        "samples",
    ]
    inputs.extend(str(p.relative_to(ROOT)) for p in ROOT.glob("backend/requirements*"))
    files = set()
    for name in inputs:
        path = ROOT / name
        for item in path.rglob("*") if path.is_dir() else [path]:
            relative = item.relative_to(ROOT)
            if any(
                part in {"__pycache__", ".ruff_cache", ".DS_Store", "sources"}
                for part in relative.parts
            ):
                continue
            if item.is_symlink():
                raise RuntimeError(f"Release input must not be a symlink: {relative}")
            if item.is_file() and item.suffix not in {".pyc", ".pdf"}:
                files.add(relative)
    contents = {str(name): (ROOT / name).read_bytes() for name in sorted(files)}
    contents["MANIFEST.sha256"] = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in contents.items()
    ).encode()
    destination = ROOT / "output/releases" / f"{NAME}.tar.gz"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        destination.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for name, data in contents.items():
            info = tarfile.TarInfo(f"{NAME}/{name}")
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    checksum = destination.with_name(destination.name + ".sha256")
    checksum.write_text(f"{digest}  {destination.name}\n")
    print(f"{destination}\n{checksum}\n{len(files)} files; SHA256 {digest}")


if __name__ == "__main__":
    main()
