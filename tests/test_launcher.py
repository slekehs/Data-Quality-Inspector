"""Launcher behavior without opening a browser or starting real processes."""

from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import plistlib
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import launch_gui


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.runtime = self.project / "runtime"
        self.runtime.mkdir()
        self.launch_target = self.project / "dqi" / "static" / "launch-target.js"
        self.launch_target.parent.mkdir(parents=True)

    def test_health_check_requires_expected_application_and_version(self):
        for body, expected in (
            ({"app": launch_gui.APP_ID, "version": launch_gui.APP_VERSION}, True),
            ({"app": "unrelated-service", "version": launch_gui.APP_VERSION}, False),
            ({"app": launch_gui.APP_ID, "version": "0.3.0"}, False),
            ({"app": launch_gui.APP_ID, "version": "0.4.0"}, False),
            ([], False),
        ):
            with self.subTest(body=body), patch.object(launch_gui.http.client, "HTTPConnection") as factory:
                connection = factory.return_value
                response = connection.getresponse.return_value
                response.status = 200
                response.read.return_value = json.dumps(body).encode()
                self.assertEqual(launch_gui.server_is_healthy(8765), expected)
                factory.assert_called_once_with("127.0.0.1", 8765, timeout=0.4)
                connection.request.assert_called_once_with("GET", "/api/health")
                connection.close.assert_called_once()

    def test_invalid_saved_state_cannot_redirect_to_an_invalid_port(self):
        path = self.runtime / "server.json"
        for state in ({"port": True}, {"port": -1}, {"port": 70000}, {"port": "8765"}, []):
            path.write_text(json.dumps(state))
            self.assertIsNone(launch_gui.saved_port(path))
        path.write_text('{"port": 9321, "pid": 123}')
        self.assertEqual(launch_gui.saved_port(path), 9321)

    def test_existing_inspector_is_opened_without_spawning_another_process(self):
        with patch.object(launch_gui, "server_is_healthy", return_value=True), \
             patch.object(launch_gui.subprocess, "Popen") as spawn, \
             patch.object(launch_gui.webbrowser, "open", return_value=True) as browser:
            self.assertEqual(launch_gui.launch(self.project), 0)
            spawn.assert_not_called()
            browser.assert_called_once_with("http://127.0.0.1:8765/", new=2, autoraise=True)
        self.assertEqual(self.launch_target.read_text(), 'window.DQI_APP_URL = "http://127.0.0.1:8765/";\n')

    def test_launch_target_uses_only_loopback_and_a_valid_port(self):
        for port in (1, 59364, 65535):
            with self.subTest(port=port):
                launch_gui.save_launch_target(self.project, port)
                self.assertEqual(self.launch_target.read_text(), f'window.DQI_APP_URL = "http://127.0.0.1:{port}/";\n')
                self.assertFalse(self.launch_target.with_name("launch-target.js.tmp").exists())

    def test_invalid_port_cannot_write_an_executable_launch_target(self):
        original = 'window.DQI_APP_URL = "http://127.0.0.1:8765/";\n'
        self.launch_target.write_text(original)
        for port in (True, False, 0, -1, 65536, 8765.0, "59364", '1/";alert(1)//', None):
            with self.subTest(port=port), self.assertRaises(ValueError):
                launch_gui.save_launch_target(self.project, port)
            self.assertEqual(self.launch_target.read_text(), original)

    def test_reuse_corrects_stale_target_and_ignores_saved_urls(self):
        self.launch_target.write_text('window.DQI_APP_URL = "http://127.0.0.1:8765/";\n')
        (self.runtime / "server.json").write_text(json.dumps({"port": 59364, "host": "example.invalid", "url": "https://example.invalid/"}))
        with patch.object(launch_gui, "server_is_healthy", return_value=True) as healthy, \
             patch.object(launch_gui.subprocess, "Popen") as spawn, \
             patch.object(launch_gui.webbrowser, "open", return_value=True) as browser:
            self.assertEqual(launch_gui.launch(self.project), 0)
        self.assertEqual(healthy.call_args.args[0], 59364)
        spawn.assert_not_called()
        browser.assert_called_once_with("http://127.0.0.1:59364/", new=2, autoraise=True)
        self.assertEqual(self.launch_target.read_text(), 'window.DQI_APP_URL = "http://127.0.0.1:59364/";\n')

    def test_saved_alternative_port_is_checked_before_default(self):
        (self.runtime / "server.json").write_text('{"port": 9321, "pid": 123}')
        with patch.object(launch_gui, "server_is_healthy", return_value=True) as healthy, \
             patch.object(launch_gui.subprocess, "Popen") as spawn:
            port = launch_gui._start_or_reuse(self.project, self.runtime, launch_gui.time.monotonic() + 5)
        self.assertEqual(port, 9321)
        self.assertEqual(healthy.call_args.args[0], 9321)
        spawn.assert_not_called()

    def test_started_server_is_detached_and_recorded_only_after_readiness(self):
        process = Mock(pid=2468)
        process.poll.return_value = None
        with patch.object(launch_gui, "server_is_healthy", side_effect=[False, False, True]), \
             patch.object(launch_gui, "available_port", return_value=9321), \
             patch.object(launch_gui.subprocess, "Popen", return_value=process) as spawn, \
             patch.object(launch_gui.time, "sleep"):
            port = launch_gui._start_or_reuse(self.project, self.runtime, launch_gui.time.monotonic() + 5)
        self.assertEqual(port, 9321)
        args, kwargs = spawn.call_args
        self.assertEqual(args[0], [launch_gui.sys.executable, "-m", "dqi.web", "--port", "9321"])
        self.assertEqual(kwargs["cwd"], self.project)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(kwargs["start_new_session"])
        state = json.loads((self.runtime / "server.json").read_text())
        self.assertEqual((state["port"], state["pid"]), (9321, 2468))
        self.assertEqual(self.launch_target.read_text(), 'window.DQI_APP_URL = "http://127.0.0.1:9321/";\n')
        process.terminate.assert_not_called()

    def test_port_selection_race_retries_without_terminating_unrelated_server(self):
        exited, ready = Mock(pid=111), Mock(pid=222)
        exited.poll.return_value = 1
        ready.poll.return_value = None
        with patch.object(launch_gui, "server_is_healthy", side_effect=[False, True]), \
             patch.object(launch_gui, "available_port", side_effect=[8765, 9321]), \
             patch.object(launch_gui.subprocess, "Popen", side_effect=[exited, ready]):
            port = launch_gui._start_or_reuse(self.project, self.runtime, launch_gui.time.monotonic() + 5)
        self.assertEqual(port, 9321)
        exited.terminate.assert_not_called()
        ready.terminate.assert_not_called()

    def test_failed_state_write_stops_only_the_process_from_this_attempt(self):
        process = Mock(pid=2468)
        process.poll.return_value = None
        (self.runtime / "server.json").write_text('{"port": 9321, "pid": 999}')
        with patch.object(launch_gui, "server_is_healthy", side_effect=[False, False, True]), \
             patch.object(launch_gui, "available_port", return_value=9321), \
             patch.object(launch_gui.subprocess, "Popen", return_value=process), \
             patch.object(launch_gui, "_save_state", side_effect=OSError("Cannot write state")):
            with self.assertRaises(OSError):
                launch_gui._start_or_reuse(self.project, self.runtime, launch_gui.time.monotonic() + 5)
        process.terminate.assert_called_once()
        self.assertEqual(json.loads((self.runtime / "server.json").read_text())["pid"], 999)

    def test_expired_startup_deadline_does_not_spawn_process(self):
        with patch.object(launch_gui.time, "monotonic", return_value=10), \
             patch.object(launch_gui.subprocess, "Popen") as spawn:
            with self.assertRaises(RuntimeError):
                launch_gui._start_or_reuse(self.project, self.runtime, deadline=9)
        spawn.assert_not_called()

    def test_browser_failure_is_logged_without_stopping_healthy_server(self):
        with patch.object(launch_gui, "server_is_healthy", return_value=True), \
             patch.object(launch_gui.webbrowser, "open", return_value=False), \
             patch.object(launch_gui.subprocess, "Popen") as spawn, \
             redirect_stderr(StringIO()) as stderr:
            self.assertEqual(launch_gui.launch(self.project), 1)
        self.assertIn("http://127.0.0.1:8765/", stderr.getvalue())
        self.assertIn("browser could not open", (self.runtime / "server.log").read_text())
        spawn.assert_not_called()

    def test_bundle_points_at_executable_launcher(self):
        project = Path(launch_gui.__file__).resolve().parent
        bundle = project / "Data Quality Inspector.app" / "Contents"
        with (bundle / "Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
        self.assertEqual(info["CFBundleExecutable"], "launch")
        executable = bundle / "MacOS" / info["CFBundleExecutable"]
        self.assertTrue(executable.stat().st_mode & 0o111)
        content = executable.read_text()
        self.assertIn('"$PROJECT_DIR/launch_gui.py"', content)
        self.assertIn("#!/bin/sh", content)


if __name__ == "__main__":
    unittest.main()
