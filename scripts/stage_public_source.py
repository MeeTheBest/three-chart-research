"""Create a fresh allowlisted deployment tree; never publish the working tree."""
from pathlib import Path
from shutil import copy2
from tempfile import mkdtemp
import re

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ".dockerignore", ".gitignore", "Dockerfile", "render.yaml", "LICENSE", "NOTICE.md", "README.md",
    "package.json", "requirements-bazi.txt", "requirements-validation.txt", "requirements-web.txt",
    "scripts/prepare_deploy_runtime.py", "scripts/stage_public_source.py",
    "src/research/__init__.py", "src/research/validator.py", "src/ziwei/iztro-adapter.js",
    "src/vedic/__init__.py", "src/vedic/adapter.py", "skill-packs/README.md",
    "mvp-web/package.json", "mvp-web/package-lock.json", "mvp-web/vite.config.ts",
    "mvp-web/next.config.ts", "mvp-web/tsconfig.json", "mvp-web/next-env.d.ts",
    "mvp-web/postcss.config.mjs", "mvp-web/.gitignore",
    "test/mvp/test_web_api.py", "mvp-web/tests/rendered-html.test.mjs",
]
DIRECTORIES = ["src/mvp", "src/bazi", "schemas", "prompts", "skill-packs/v1", "mvp-web/app", "mvp-web/public"]
SKIP_PARTS = {"__pycache__", "vendor", "node_modules", ".git", ".DS_Store"}
SECRET = re.compile(rb"(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----)")

paths = {ROOT / value for value in FILES}
for directory in DIRECTORIES:
    paths.update(path for path in (ROOT / directory).rglob("*") if path.is_file())
selected = []
for path in sorted(paths):
    relative = path.relative_to(ROOT)
    if any(part in SKIP_PARTS or part.startswith(".env") for part in relative.parts):
        continue
    if path.suffix in {".pyc", ".so", ".sqlite3", ".se1"} or path.name.startswith("build_contest_"):
        continue
    if path.is_symlink():
        raise SystemExit(f"Refusing symlink: {relative}")
    if SECRET.search(path.read_bytes()):
        raise SystemExit(f"Possible secret: {relative}; refusing publication")
    selected.append(path)

staging = ROOT / ".deploy-source"
staging.mkdir(exist_ok=True)
destination = Path(mkdtemp(prefix="release-", dir=staging))
for path in selected:
    target = destination / path.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    copy2(path, target)
print(destination)
print(f"Selected {len(selected)} files; no private reports, answers, credentials or runtime environments included.")
