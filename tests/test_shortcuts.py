"""Desktop shortcut behavior using temporary folders only; no GUI is opened."""

from pathlib import Path
import re
import shlex
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dqi import shortcuts


class DesktopShortcutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "Test home"
        (self.home / "Desktop").mkdir(parents=True)
        self.project = self.root / "Inspector's project with spaces"
        self.app = self.project / shortcuts.APP_NAME
        self.launcher = self.app / "Contents" / "MacOS" / "launch"
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_text("#!/bin/sh\nexit 0\n")
        self.launcher.chmod(0o755)
        (self.project / "launch_gui.py").write_text("# Test fixture only.\n")
        self.destination = self.home / "Desktop" / shortcuts.APP_NAME
        for mocker in (patch.object(shortcuts.sys, "platform", "darwin"), patch.object(shortcuts.Path, "home", return_value=self.home), patch.object(shortcuts, "_app_bundle", return_value=self.app)):
            mocker.start()
            self.addCleanup(mocker.stop)

    def test_status_is_read_only_and_uses_the_users_desktop(self):
        status = shortcuts.desktop_shortcut_status()
        self.assertEqual(set(status), {"supported", "installed", "path", "can_create", "message", "reason"})
        self.assertTrue(status["supported"])
        self.assertTrue(status["can_create"])
        self.assertFalse(status["installed"])
        self.assertEqual(status["path"], str(self.destination))
        self.assertFalse(self.destination.exists())

    def test_creation_is_an_idempotent_app_symlink(self):
        created = shortcuts.create_desktop_shortcut()
        self.assertTrue(created["installed"])
        self.assertTrue(self.destination.is_symlink())
        self.assertEqual(self.destination.resolve(), self.app.resolve())
        original_inode = self.destination.lstat().st_ino
        with patch.object(Path, "symlink_to", side_effect=AssertionError("Existing link must not be rewritten")):
            again = shortcuts.create_desktop_shortcut()
        self.assertTrue(again["installed"])
        self.assertEqual(self.destination.lstat().st_ino, original_inode)

    def test_unrelated_file_directory_and_broken_link_are_preserved(self):
        for kind in ("file", "directory", "broken_link"):
            with self.subTest(kind=kind):
                if kind == "file":
                    self.destination.write_text("Keep this item")
                elif kind == "directory":
                    self.destination.mkdir()
                else:
                    self.destination.symlink_to(self.root / "missing.app", target_is_directory=True)
                original_inode = self.destination.lstat().st_ino
                self.assertEqual(shortcuts.desktop_shortcut_status()["reason"], "conflict")
                with self.assertRaises(shortcuts.DesktopShortcutError) as error:
                    shortcuts.create_desktop_shortcut()
                self.assertEqual((error.exception.code, error.exception.status_code), ("conflict", 409))
                self.assertEqual(self.destination.lstat().st_ino, original_inode)
                if kind == "directory":
                    self.destination.rmdir()
                else:
                    if kind == "file":
                        self.assertEqual(self.destination.read_text(), "Keep this item")
                    self.destination.unlink()

    def test_permission_failure_is_actionable_without_other_writes(self):
        with patch.object(Path, "symlink_to", side_effect=PermissionError("Desktop access denied")):
            with self.assertRaises(shortcuts.DesktopShortcutError) as error:
                shortcuts.create_desktop_shortcut()
        self.assertEqual((error.exception.code, error.exception.status_code), ("permission_denied", 403))
        self.assertIn("Allow Desktop access", str(error.exception))
        self.assertEqual(list((self.home / "Desktop").iterdir()), [])

    def test_competing_unrelated_item_is_never_overwritten(self):
        def racing_create(*args, **kwargs):
            self.destination.write_text("Created by another application")
            raise FileExistsError("Already exists")

        with patch.object(Path, "symlink_to", side_effect=racing_create):
            with self.assertRaises(shortcuts.DesktopShortcutError) as error:
                shortcuts.create_desktop_shortcut()
        self.assertEqual(error.exception.code, "conflict")
        self.assertEqual(self.destination.read_text(), "Created by another application")

    def test_competing_matching_shortcut_is_successful(self):
        real_symlink_to = Path.symlink_to

        def racing_create(*args, **kwargs):
            real_symlink_to(self.destination, self.app, target_is_directory=True)
            raise FileExistsError("Already created by another request")

        with patch.object(Path, "symlink_to", side_effect=racing_create):
            self.assertTrue(shortcuts.create_desktop_shortcut()["installed"])

    def test_missing_app_or_desktop_is_not_created(self):
        self.launcher.unlink()
        with self.assertRaises(shortcuts.DesktopShortcutError) as error:
            shortcuts.create_desktop_shortcut()
        self.assertEqual(error.exception.code, "missing_app")
        self.assertFalse(self.destination.exists())
        self.launcher.write_text("#!/bin/sh\nexit 0\n")
        self.launcher.chmod(0o755)
        self.destination.parent.rmdir()
        with self.assertRaises(shortcuts.DesktopShortcutError) as error:
            shortcuts.create_desktop_shortcut()
        self.assertEqual(error.exception.code, "desktop_unavailable")
        self.assertFalse(self.destination.parent.exists())

    def test_unsupported_platform_cannot_create_anything(self):
        with patch.object(shortcuts.sys, "platform", "linux"), patch.object(Path, "symlink_to") as create:
            status = shortcuts.desktop_shortcut_status()
            self.assertFalse(status["supported"])
            with self.assertRaises(shortcuts.DesktopShortcutError) as error:
                shortcuts.create_desktop_shortcut()
        self.assertEqual((error.exception.code, error.exception.status_code), ("unsupported", 501))
        create.assert_not_called()

    def test_desktop_app_symlink_runs_the_original_project_launcher(self):
        source = Path(__file__).resolve().parent.parent / shortcuts.APP_NAME / "Contents" / "MacOS" / "launch"
        # Substitute a harmless executable for Python; it only prints its arguments.
        fake_python = self.root / "Fake Python"
        fake_python.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@"\n')
        fake_python.chmod(0o755)
        script, replacements = re.subn(r"^PREFERRED_PYTHON=.*$", "PREFERRED_PYTHON=" + shlex.quote(str(fake_python)), source.read_text(), count=1, flags=re.MULTILINE)
        self.assertEqual(replacements, 1)
        self.launcher.write_text(script)
        self.launcher.chmod(0o755)
        shortcuts.create_desktop_shortcut()
        result = subprocess.run([str(self.destination / "Contents" / "MacOS" / "launch")], cwd=self.home, capture_output=True, text=True, check=True, timeout=5)
        self.assertEqual(result.stdout.strip(), str(self.project.resolve() / "launch_gui.py"))
        self.assertEqual(result.stderr, "")
        self.assertFalse((self.project / "runtime").exists())


if __name__ == "__main__":
    unittest.main()
