"""Create a fixed Desktop shortcut to this project's macOS application."""

from __future__ import annotations

import os
from pathlib import Path
import sys


APP_NAME = "Data Quality Inspector.app"


class DesktopShortcutError(RuntimeError):
    """A shortcut could not be created without changing another Desktop item."""

    def __init__(self, message: str, *, code: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _app_bundle() -> Path:
    return Path(__file__).resolve().parent.parent / APP_NAME


def _desktop_shortcut() -> Path:
    return Path.home() / "Desktop" / APP_NAME


def _status(path, *, supported=True, installed=False, can_create=False, message, reason=None):
    return {
        "supported": supported,
        "installed": installed,
        "path": str(path),
        "can_create": can_create,
        "message": message,
        "reason": reason,
    }


def desktop_shortcut_status() -> dict:
    """Inspect the fixed Desktop destination without creating or modifying it."""
    shortcut = _desktop_shortcut()
    if sys.platform != "darwin":
        return _status(shortcut, supported=False, message="Desktop shortcuts are available on macOS.", reason="unsupported")

    app = _app_bundle()
    try:
        launcher = app / "Contents" / "MacOS" / "launch"
        if not app.is_dir() or not launcher.is_file() or not os.access(launcher, os.X_OK) or not (app.parent / "launch_gui.py").is_file():
            return _status(shortcut, message="The original Data Quality Inspector app is missing or incomplete. Keep it inside the project folder.", reason="missing_app")
        try:
            shortcut.lstat()  # Also detects dangling symlinks; they must not be replaced.
        except FileNotFoundError:
            if not shortcut.parent.is_dir():
                return _status(shortcut, message="The Desktop folder is unavailable. No shortcut was created.", reason="desktop_unavailable")
            return _status(shortcut, can_create=True, message="Create a Desktop shortcut to open the inspector without finding its project folder.")
        if shortcut.is_symlink():
            try:
                matches = shortcut.resolve(strict=True) == app.resolve(strict=True)
            except (OSError, RuntimeError):
                matches = False
            if matches:
                return _status(shortcut, installed=True, message="The Desktop shortcut is ready. Double-click Data Quality Inspector to open it.")
        return _status(shortcut, message="Another item named Data Quality Inspector.app is already on your Desktop. Rename or move that item, then try again. It has not been changed.", reason="conflict")
    except PermissionError:
        return _status(shortcut, message="The inspector cannot access your Desktop. Allow Desktop access in macOS settings, then try again.", reason="permission_denied")
    except OSError as error:
        return _status(shortcut, message=f"The Desktop shortcut could not be checked: {error}", reason="desktop_unavailable")


def _raise_status_error(status):
    code = status["reason"] or "desktop_unavailable"
    status_code = {"unsupported": 501, "permission_denied": 403, "conflict": 409, "missing_app": 409, "desktop_unavailable": 409}.get(code, 500)
    raise DesktopShortcutError(status["message"], code=code, status_code=status_code)


def create_desktop_shortcut() -> dict:
    """Create only ~/Desktop/Data Quality Inspector.app, without replacing items."""
    status = desktop_shortcut_status()
    if status["installed"]:
        return status
    if not status["can_create"]:
        _raise_status_error(status)

    shortcut = _desktop_shortcut()
    try:
        # symlink_to fails atomically if anything already occupies this name.
        shortcut.symlink_to(_app_bundle().resolve(strict=True), target_is_directory=True)
    except FileExistsError:
        status = desktop_shortcut_status()
        if status["installed"]:  # A simultaneous request created the same shortcut.
            return status
        _raise_status_error(status)
    except PermissionError as error:
        raise DesktopShortcutError("The inspector cannot create a shortcut on your Desktop. Allow Desktop access in macOS settings, then try again.", code="permission_denied", status_code=403) from error
    except OSError as error:
        raise DesktopShortcutError(f"The Desktop shortcut could not be created: {error}", code="desktop_unavailable", status_code=409) from error
    status = desktop_shortcut_status()
    if not status["installed"]:
        _raise_status_error(status)
    return status
