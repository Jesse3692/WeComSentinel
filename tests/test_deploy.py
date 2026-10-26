"""Deployment checks without contacting a real server."""

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from io import BytesIO
import zipfile

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def load(name):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


update = load("update")
sys.modules["update"] = update
sync = load("sync")


def source_tree(root):
    for name in update.BUNDLE_PATHS:
        path = root / name
        if name in ("app", "scripts", "migrations", "frontend/dist"):
            path.mkdir(parents=True)
            (path / "index.html").write_text("new")
        else:
            path.write_text("new")
    (root / "deploy").mkdir()
    (root / "deploy/update.py").write_text("# helper")


def test_package_excludes_runtime_and_secrets(tmp_path, monkeypatch):
    root = tmp_path / "local"
    root.mkdir()
    source_tree(root)
    (root / ".env").write_text("secret")
    (root / "data").mkdir()
    (root / "data/live.db").write_text("database")
    (root / "app/__pycache__").mkdir()
    (root / "app/__pycache__/main.pyc").write_bytes(b"cached")
    monkeypatch.setattr(sync, "ROOT", root)
    archive = tmp_path / "release.zip"
    sync.make_bundle(archive)
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
    assert "frontend/dist/index.html" in names
    assert "deploy/update.py" in names
    assert ".env" not in names
    assert not any("data/" in n or "__pycache__" in n for n in names)


def test_install_removes_stale_code_but_preserves_runtime(tmp_path, monkeypatch):
    root, stage, backup = (tmp_path / n for n in ("live", "stage", "backup"))
    root.mkdir()
    stage.mkdir()
    source_tree(stage)
    (root / "app").mkdir()
    (root / "app/deleted.py").write_text("old")
    (root / ".env").write_text("server config")
    (root / "data").mkdir()
    (root / "data/live.db").write_text("keep")
    monkeypatch.setattr(update, "ROOT", root)
    update.install_bundle(stage, backup)
    assert not (root / "app/deleted.py").exists()
    assert (backup / "code/app/deleted.py").read_text() == "old"
    assert (root / ".env").read_text() == "server config"
    assert (root / "data/live.db").read_text() == "keep"
    assert (root / "frontend/dist/index.html").read_text() == "new"


def test_incomplete_release_does_not_modify_live_code(tmp_path, monkeypatch):
    root = tmp_path / "live"
    root.mkdir()
    (root / "app").mkdir()
    monkeypatch.setattr(update, "ROOT", root)
    with pytest.raises(update.UpdateError):
        update.install_bundle(tmp_path / "absent", tmp_path / "backup")
    assert (root / "app").exists()


def test_ssh_config_and_bootstrap():
    config = {"host": "server", "remote_dir": "/root/workspace/wecom-monitor", "ssh_port": 2222}
    ssh, scp, target, remote = sync.connection(config)
    assert ssh[:3] == ["ssh", "-p", "2222"]
    assert scp[:3] == ["scp", "-P", "2222"]
    assert target == "root@server"
    compile(sync.bootstrap(remote, remote + "/.deploy/release", config), "bootstrap", "exec")
    with pytest.raises(ValueError):
        sync.connection({**config, "remote_dir": "/root/../etc"})


@pytest.mark.parametrize(
    "body,success", [(b'{"status":"ready"}', True), (b'{"status":"starting"}', False)]
)
def test_ready_check_requires_ready_and_stops_failed_service(monkeypatch, body, success):
    commands = []
    monkeypatch.setattr(update, "run", lambda command: commands.append(command))
    monkeypatch.setattr(update, "listening", lambda port: False)
    clock = iter([0, 0, 100])
    monkeypatch.setattr(update.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(update.time, "sleep", lambda seconds: None)
    response = BytesIO(body)
    response.status = 200
    monkeypatch.setattr(update, "urlopen", lambda *a, **kw: response)
    args = SimpleNamespace(service="wecom-monitor", port=8000, timeout=90)
    if success:
        update.start_app(args, Path("python"))
        assert commands[-1] == ["systemctl", "is-active", "--quiet", "wecom-monitor"]
    else:
        with pytest.raises(update.UpdateError):
            update.start_app(args, Path("python"))
        assert commands[-1] == ["systemctl", "stop", "wecom-monitor"]
