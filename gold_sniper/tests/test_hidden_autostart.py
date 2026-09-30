from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_scheduled_task_targets_hidden_guard() -> None:
    script = _read("scripts/register_gold_sniper_task.ps1")
    assert '$taskName = "GoldSniper_Guard"' in script
    assert "gold_sniper_guard.py" in script
    assert "pythonw.exe" in script
    assert "-RestartCount 999" in script
    assert "-RestartInterval" in script
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in script
    assert "-Hidden" in script


def test_vbs_launcher_is_windowless_and_targets_guard() -> None:
    script = _read("scripts/start_gold_sniper_hidden.vbs")
    assert "gold_sniper_guard.py" in script
    assert "pythonw.exe" in script
    assert ", 0, False" in script


def test_guard_is_single_instance_and_self_healing() -> None:
    script = _read("scripts/gold_sniper_guard.py")
    assert "guard.lock" in script
    assert "BOOT_DELAY_SECONDS = 180.0" in script
    assert "gold_sniper.pc_manager" in script
    assert "CREATE_NO_WINDOW" in script
    assert "MIN_RELAUNCH_INTERVAL_SECONDS" in script


def test_runtime_console_children_are_hidden_on_windows() -> None:
    runtime_sources = [
        "gold_sniper/pc_manager.py",
        "gold_sniper/watchdog.py",
        "gold_sniper/utils/single_instance.py",
        "gold_sniper/utils/cloudflared_manager.py",
        "gold_sniper/safety/research_branch_guard.py",
        "gold_sniper/web/dashboard_server.py",
    ]
    for relative in runtime_sources:
        source = _read(relative)
        assert "CREATE_NO_WINDOW" in source or "creationflags=0x08000000" in source, relative


def test_pc_manager_lock_validates_process_identity() -> None:
    source = _read("gold_sniper/pc_manager.py")
    assert "def _pid_is_pc_manager" in source
    assert source.count("_pid_is_pc_manager(old_pid)") >= 2
