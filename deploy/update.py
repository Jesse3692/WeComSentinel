#!/usr/bin/env python3
"""Update this Linux source deployment; invoke from any directory with venv Python."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


class UpdateError(Exception):
    pass


def run(args, *, capture=False):
    result = subprocess.run([str(arg) for arg in args], cwd=ROOT, check=False,
                            text=True, stdout=subprocess.PIPE if capture else None)
    if result.returncode:
        raise UpdateError(f"命令失败（退出码 {result.returncode}）：{args[0]}")
    return result.stdout.strip() if capture else ""


def project_processes(root, proc_dir=Path("/proc")):
    """Match cwd and the actual Uvicorn command; do not kill uv wrappers or other apps."""
    found = {}
    for entry in proc_dir.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "cwd").resolve() != root.resolve():
                continue
            argv = (entry / "cmdline").read_bytes().decode().strip("\0").split("\0")
            module = argv[1:3] == ["-m", "uvicorn"]
            executable = Path(argv[0]).name == "uvicorn" or (
                len(argv) > 1 and Path(argv[1]).name == "uvicorn")
            if "app.main:app" not in argv or not (module or executable):
                continue
            # Field 22 of /proc/PID/stat prevents signalling a reused PID.
            start = (entry / "stat").read_text().rpartition(")")[2].split()[19]
            found[int(entry.name)] = start
        except (OSError, UnicodeError, IndexError):
            continue
    return found


def listening(port):
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def git_target():
    if Path(run(["git", "rev-parse", "--show-toplevel"], capture=True)).resolve() != ROOT:
        raise UpdateError("项目根目录必须是 Git 仓库根目录。")
    if run(["git", "status", "--porcelain", "--untracked-files=no"], capture=True):
        raise UpdateError("服务器有未提交的代码改动，已停止。请先处理改动，或使用 --local 部署当前代码。")
    branch = run(["git", "symbolic-ref", "--short", "HEAD"], capture=True)
    remote = run(["git", "config", f"branch.{branch}.remote"], capture=True)
    if not remote or remote == ".":
        raise UpdateError("当前分支需要配置远程跟踪分支。")
    # Do not print the remote URL: it may contain credentials.
    print(f"获取当前分支 {branch} 的远程更新……", flush=True)
    run(["git", "fetch", remote])
    target = run(["git", "rev-parse", "@{upstream}"], capture=True)
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", target], cwd=ROOT)
    if ancestor.returncode:
        raise UpdateError("本地与远程历史无法快进合并。请先处理分支差异，脚本不会强制覆盖。")
    return target


def stop_app(service, timeout):
    if service:
        run(["systemctl", "stop", service])
    else:
        processes = project_processes(ROOT)
        for pid, started in processes.items():
            if project_processes(ROOT).get(pid) == started:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + timeout
        while project_processes(ROOT):
            if time.monotonic() >= deadline:
                raise UpdateError("原服务未能正常停止。请检查进程守护工具或退出日志；未强制结束进程。")
            time.sleep(0.5)


def start_app(args, python):
    if listening(args.port):
        raise UpdateError(f"端口 {args.port} 已被占用，未启动第二个实例。")
    process = None
    if args.service:
        run(["systemctl", "start", args.service])
    else:
        log_path = ROOT / "uvicorn.log"
        with log_path.open("ab", buffering=0) as log:
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
            process = subprocess.Popen(
                [str(python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                 "--port", str(args.port), "--workers", "1"],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True, close_fds=True,
            )
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if process and process.poll() is not None:
            raise UpdateError("新服务已退出，请查看项目目录的 uvicorn.log。")
        try:
            with urlopen(f"http://127.0.0.1:{args.port}/readyz", timeout=2) as response:
                body = json.load(response)
                ready = response.status == 200 and isinstance(body, dict) and body.get("status") == "ready"
            if ready:
                if args.service:
                    run(["systemctl", "is-active", "--quiet", args.service])
                print(f"更新完成：服务已启动，/readyz 检查通过，端口 {args.port}。", flush=True)
                return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
    elif args.service:
        run(["systemctl", "stop", args.service])
    raise UpdateError("新服务未通过就绪检查，已请求停止。请检查日志。")


def main():
    global ROOT
    parser = argparse.ArgumentParser(description="更新源码、备份、构建并重启本项目（Linux）")
    parser.add_argument("--local", action="store_true", help="代码已手工上传；不执行 Git 更新")
    parser.add_argument("--skip-frontend", action="store_true", help="使用已上传的 frontend/dist，跳过构建")
    parser.add_argument("--service", help="通过指定 systemd 服务停止/启动，例如 wecom-monitor")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--timeout", type=int, default=90, help="停止/就绪检查等待秒数")
    parser.add_argument("--project-root", type=Path, help="现有服务器项目路径")
    parser.add_argument("--bundle", type=Path, help="本地构建并上传、已解压的发布目录")
    args = parser.parse_args()
    if args.project_root:
        ROOT = args.project_root.resolve()
    if args.bundle:
        args.bundle = args.bundle.resolve()
        args.local = True
        args.skip_frontend = True
        validate_bundle(args.bundle)
    if sys.platform != "linux":
        parser.error("此脚本在 Linux 服务器上执行。")
    if not 1 <= args.port <= 65535 or not 1 <= args.timeout <= 600:
        parser.error("port 应为 1–65535，timeout 应为 1–600。")
    if args.service and (args.service.startswith("-") or "/" in args.service):
        parser.error("请传 systemd 服务名称。")
    import fcntl
    state = ROOT / ".deploy"
    state.mkdir(exist_ok=True)
    with (state / "update.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("已有更新正在执行。", file=sys.stderr)
            return 1
        backup = None
        stopped = False
        try:
            python = ROOT / ".venv/bin/python"
            if not python.is_file() or not (ROOT / ".env").is_file():
                raise UpdateError("缺少 .venv/bin/python 或 .env。请先完成初次安装。")
            for command in ["uv"] + ([] if args.local else ["git"]) + (
                    [] if args.skip_frontend else ["node", "pnpm"]):
                if not shutil.which(command):
                    raise UpdateError(f"未找到 {command}，请安装并加入当前终端的 PATH。")
            if args.skip_frontend and not ((args.bundle or ROOT) / "frontend/dist/index.html").is_file():
                raise UpdateError("--skip-frontend 需要先上传完整 frontend/dist。")
            if args.service:
                workdir = run(["systemctl", "show", args.service, "-p", "WorkingDirectory", "--value"], capture=True)
                if not workdir or Path(workdir).resolve() != ROOT:
                    raise UpdateError("指定 systemd 服务的 WorkingDirectory 与本项目不一致。")
            elif listening(args.port) and not project_processes(ROOT):
                raise UpdateError("端口被其他目录的进程占用；请确认项目路径和服务管理方式。")
            target = None if args.local else git_target()
            base_python = run([python, "-c", "import sys; print(sys._base_executable)"], capture=True)
            backup = ROOT / "backups" / ("deploy-" + datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}")
            print("备份数据库、媒体和环境配置……", flush=True)
            run([python, "-m", "app.cli", "backup", "--output", backup])
            shutil.copy2(ROOT / ".env", backup / ".env")
            (backup / ".env").chmod(0o600)
            if target:
                revision = run(["git", "rev-parse", "HEAD"], capture=True)
                (backup / "code-revision.txt").write_text(revision + "\n")
            print("停止当前项目服务……", flush=True)
            stop_app(args.service, args.timeout)
            stopped = True
            if listening(args.port):
                raise UpdateError("停止后端口仍被占用，可能另有进程守护工具在重启服务。")
            if args.bundle:
                install_bundle(args.bundle, backup)
            if target:
                run(["git", "merge", "--ff-only", target])
            print("同步 Python 依赖……", flush=True)
            run(["uv", "sync", "--locked", "--no-dev", "--python", base_python, "--no-python-downloads"])
            if not args.skip_frontend:
                print("安装并构建前端……", flush=True)
                run(["pnpm", "--dir", "frontend", "install", "--frozen-lockfile"])
                run(["pnpm", "--dir", "frontend", "run", "build"])
            run([python, "-m", "alembic", "upgrade", "head"])
            start_app(args, python)
            print(f"本次备份：{backup}")
            return 0
        except (UpdateError, OSError, KeyboardInterrupt) as exc:
            print(f"更新未完成：{exc}", file=sys.stderr)
            if backup and backup.exists():
                print(f"备份位置：{backup}", file=sys.stderr)
            if stopped:
                print("服务已进入更新阶段；修复错误后可重新执行 --local。未自动回退代码或数据库。", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
