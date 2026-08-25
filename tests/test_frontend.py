from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class FakeGLib:
    SOURCE_REMOVE = False


fake_glib = FakeGLib()
fake_gtk = types.SimpleNamespace(ApplicationWindow=object, Application=object)
fake_repository = types.ModuleType("gi.repository")
fake_repository.Gdk = types.SimpleNamespace()
fake_repository.Gio = types.SimpleNamespace()
fake_repository.GLib = fake_glib
fake_repository.Gtk = fake_gtk
fake_gi = types.ModuleType("gi")
fake_gi.require_version = lambda *_args: None
fake_gi.repository = fake_repository

BACKEND_SPEC = importlib.util.spec_from_file_location(
    "markitdown_backend", ROOT / "src/markitdown_backend.py"
)
backend = importlib.util.module_from_spec(BACKEND_SPEC)
assert BACKEND_SPEC.loader
BACKEND_SPEC.loader.exec_module(backend)

with mock.patch.dict(sys.modules, {
    "gi": fake_gi,
    "gi.repository": fake_repository,
    "markitdown_backend": backend,
}):
    FRONTEND_SPEC = importlib.util.spec_from_file_location(
        "markitdown_drop", ROOT / "src/markitdown_drop.py"
    )
    frontend = importlib.util.module_from_spec(FRONTEND_SPEC)
    assert FRONTEND_SPEC.loader
    FRONTEND_SPEC.loader.exec_module(frontend)


class FrontendCancellationTests(unittest.TestCase):
    def setUp(self):
        self.window = object.__new__(frontend.Window)
        self.window.cancel_requested = False
        self.window.closing = False
        self.window.status = mock.Mock()
        self.window.convert_button = mock.Mock()
        self.window.cancel_button = mock.Mock()

    def test_close_cancels_once_and_waits_for_backend(self):
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        self.window.process = process

        with mock.patch.object(frontend.os, "killpg") as killpg:
            self.assertTrue(self.window.on_close_request(self.window))
            self.assertTrue(self.window.on_close_request(self.window))

        killpg.assert_called_once_with(123, frontend.signal.SIGTERM)
        self.window.status.set_text.assert_called_with("Cancelación solicitada; esperando al backend…")
        self.assertTrue(self.window.closing)

    def test_close_after_cancel_does_not_duplicate_signal(self):
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        self.window.process = process

        with mock.patch.object(frontend.os, "killpg") as killpg:
            self.window.cancel()
            self.assertTrue(self.window.on_close_request(self.window))

        killpg.assert_called_once_with(123, frontend.signal.SIGTERM)

    def test_backend_completion_closes_after_wait(self):
        process = mock.Mock()
        self.window.process = process
        self.window.cancel_requested = True
        self.window.closing = True
        self.window.finish_ui = mock.Mock()
        self.window.close = mock.Mock()

        self.assertFalse(self.window.backend_finished(process, "Cancelada"))

        self.assertIsNone(self.window.process)
        self.assertFalse(self.window.cancel_requested)
        self.window.close.assert_called_once_with()


class DropParsingTests(unittest.TestCase):
    class File:
        def __init__(self, path):
            self.path = path

        def get_path(self):
            return self.path

    class FileList:
        def __init__(self, files):
            self.files = files

        def get_files(self):
            return self.files

    def test_gdk_file_list_requires_exactly_one_local_file(self):
        one = self.FileList([self.File("/home/user/informe.pdf")])
        self.assertEqual(frontend.local_drop_path(one), ("/home/user/informe.pdf", None))
        for files in ([], [self.File("/a.pdf"), self.File("/b.pdf")]):
            with self.subTest(count=len(files)):
                path, error = frontend.local_drop_path(self.FileList(files))
                self.assertIsNone(path)
                self.assertIn("exactamente un archivo", error)

    def test_remote_file_and_gio_file_fallback(self):
        path, error = frontend.local_drop_path(self.File(None))
        self.assertIsNone(path)
        self.assertIn("no es un archivo local", error)
        self.assertEqual(frontend.local_drop_path(self.File("/tmp/a.pdf")), ("/tmp/a.pdf", None))


if __name__ == "__main__":
    unittest.main()
