from __future__ import annotations

from pathlib import Path

from . import bundle_paths


def default_config_dir() -> Path:
    return Path.home() / "TeacherTaskManager"


def bridge_state_dir(config_dir: Path) -> Path:
    return Path(config_dir) / "brity-bridge"


def settings_path(config_dir: Path) -> Path:
    return bridge_state_dir(config_dir) / "settings.json"


def history_path(config_dir: Path) -> Path:
    return bridge_state_dir(config_dir) / "history.json"


def logs_dir(config_dir: Path) -> Path:
    return bridge_state_dir(config_dir) / "logs"


def last_status_path(config_dir: Path) -> Path:
    return bridge_state_dir(config_dir) / "last-status.json"


def profile_path(config_dir: Path) -> Path:
    return Path(config_dir) / "profile.generated.json"


def update_state_path(config_dir: Path) -> Path:
    return Path(config_dir) / "update-state.json"


def attendance_install_record_path(config_dir: Path) -> Path:
    return Path(config_dir) / "attendance-install.generated.json"


def attendance_install_backup_path(config_dir: Path) -> Path:
    return (
        Path(config_dir)
        / "attendance-install.before-copy-switch.generated.json"
    )


def attendance_connect_backup_path(config_dir: Path) -> Path:
    return (
        Path(config_dir)
        / "attendance-install.before-connect.generated.json"
    )


def attendance_setup_status_path(config_dir: Path) -> Path:
    return Path(config_dir) / "attendance-setup-status.generated.json"


def fresh_start_flag_path(config_dir: Path) -> Path:
    return Path(config_dir) / "dev-fresh-start.flag"


def last_run_version_path(config_dir: Path) -> Path:
    return Path(config_dir) / "last-run-version.txt"


def skill_root() -> Path:
    return bundle_paths.bundle_root()
