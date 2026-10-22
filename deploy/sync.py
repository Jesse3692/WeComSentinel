"""Build locally, upload a release over SSH, restart and verify the Linux service."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import uuid
import zipfile

from update import BUNDLE_PATHS

ROOT = Path(__file__).resolve().parents[1]


def run(command):
    subprocess.run([str(item) for item in command], cwd=ROOT, check=True)


def make_bundle(output):
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in (*BUNDLE_PATHS, "deploy/update.py"):
            source = ROOT / name
            if not source.exists():
                raise ValueError(f"Missing release file: {name}")
            paths = source.rglob("*") if source.is_dir() else [source]
            for path in paths:
                relative = path.relative_to(ROOT)
                if "__pycache__" in relative.parts or path.suffix == ".pyc":
                    continue
                if path.is_symlink():
                    raise ValueError(f"Symlinks are not supported in releases: {relative}")
                if path.is_file():
                    archive.write(path, relative.as_posix())


def connection(config):
    host = config["host"]
    user = config.get("user", "root")
    remote = config["remote_dir"].rstrip("/")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", host) or host.startswith("-"):
        raise ValueError("host must be an IPv4 address, hostname or SSH config alias")
    if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_-]*", user):
        raise ValueError("Invalid SSH user")
    if not re.fullmatch(r"/[a-zA-Z0-9_./-]+", remote) or ".." in remote.split("/"):
        raise ValueError("remote_dir must be an absolute Linux path without spaces or '..'")
    port = int(config.get("ssh_port", 22))
    if not 1 <= port <= 65535:
        raise ValueError("Invalid SSH port")
    common = ["-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15"]
    if config.get("identity_file"):
        common += ["-i", str(Path(config["identity_file"]).expanduser().resolve())]
    return ["ssh", "-p", str(port), *common], ["scp", "-P", str(port), *common], f"{user}@{host}", remote


def bootstrap(remote, stage, config):
    # Runs with the server's existing venv Python, before touching deployed source.
    return f'''
import os, pathlib, subprocess, zipfile
root = pathlib.Path({remote!r})
stage = pathlib.Path({stage!r})
os.environ['PATH'] = str(pathlib.Path.home() / '.local/bin') + ':' + os.environ.get('PATH', '')
with zipfile.ZipFile(stage / 'release.zip') as archive:
    for item in archive.infolist():
        target = (stage / 'source' / item.filename).resolve()
        if not target.is_relative_to((stage / 'source').resolve()):
            raise RuntimeError('Invalid archive path')
    archive.extractall(stage / 'source')
command = [str(root / '.venv/bin/python'), str(stage / 'source/deploy/update.py'),
           '--project-root', str(root), '--bundle', str(stage / 'source'),
           '--port', {str(int(config.get('app_port', 8000)))!r},
           '--timeout', {str(int(config.get('timeout', 90)))!r}]
service = {config.get('service', '')!r}
if service:
    command += ['--service', service]
raise SystemExit(subprocess.call(command, cwd=root))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "deploy/sync.local.json")
    parser.add_argument("--build-only", action="store_true", help="Build/package locally without SSH")
    args = parser.parse_args()
    try:
        config = {} if args.build_only else json.loads(args.config.read_text(encoding="utf-8-sig"))
        if not args.build_only:
            ssh, scp, target, remote = connection(config)
            for tool in ("ssh", "scp"):
                if not shutil.which(tool):
                    raise ValueError(f"Missing tool: {tool}")
        pnpm = shutil.which("pnpm.cmd" if os.name == "nt" else "pnpm")
        if not pnpm:
            raise ValueError("Install Node.js and pnpm before deploying")
        print("[1/4] Build frontend locally", flush=True)
        run([pnpm, "--dir", "frontend", "install", "--frozen-lockfile"])
        run([pnpm, "--dir", "frontend", "run", "build"])
        output = ROOT / ".deploy"
        output.mkdir(exist_ok=True)
        release_id = uuid.uuid4().hex
        archive = output / f"release-{release_id}.zip"
        make_bundle(archive)
        print(f"[2/4] Release package: {archive}", flush=True)
        if args.build_only:
            return 0
        stage = f"{remote}/.deploy/releases/{release_id}"
        preflight = f"test -f {shlex.quote(remote + '/.env')} && test -x {shlex.quote(remote + '/.venv/bin/python')} && mkdir -p {shlex.quote(stage)}"
        run([*ssh, target, preflight])
        print("[3/4] Upload source and frontend/dist", flush=True)
        run([*scp, archive, f"{target}:{stage}/release.zip"])
        encoded = base64.b64encode(bootstrap(remote, stage, config).encode()).decode()
        code = f"import base64; exec(base64.b64decode({encoded!r}))"
        print("[4/4] Back up, install, restart and check /readyz", flush=True)
        command = f"{shlex.quote(remote + '/.venv/bin/python')} -c {shlex.quote(code)}"
        run([*ssh, target, command])
        print("Deployment succeeded: remote /readyz returned status=ready.")
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
