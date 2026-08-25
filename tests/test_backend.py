from __future__ import annotations

import concurrent.futures
import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("backend", ROOT / "src/markitdown_backend.py")
backend = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(backend)


class ValidationTests(unittest.TestCase):
    def test_rejections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = ["", str(root / "missing.pdf"), str(root), "https://example.test/a.pdf", "file:///a.pdf", "bad\x00.pdf"]
            wrong = root / "note.txt"
            wrong.write_text("x")
            cases.append(str(wrong))
            for value in cases:
                with self.subTest(value=value), self.assertRaises(backend.ValidationError):
                    backend.validate_input(value)

    def test_special_file_and_supported_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fifo = root / "pipe.pdf"
            os.mkfifo(fifo)
            with self.assertRaises(backend.ValidationError):
                backend.validate_input(str(fifo))
            for extension in ("pdf", "DOCX", "pptx", "XLSX"):
                path = root / f"archivo {extension} ü ' $() ; -\n.{extension}"
                path.write_bytes(b"x")
                self.assertEqual(backend.validate_input(str(path))[0], path.resolve())

    def test_large_file_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.pdf"
            path.write_bytes(b"x")
            with mock.patch.object(Path, "stat") as patched:
                patched.return_value = os.stat_result((stat.S_IFREG | 0o600, 0, 0, 0, 0, 0, backend.LARGE_FILE_BYTES + 1, 0, 0, 0))
                self.assertTrue(backend.validate_input(str(path))[1])

    def test_rejects_schemes_only_at_start(self):
        for value in ("https://example.test/a.pdf", "file:///a.pdf", "data:text/plain,pdf", "custom+remote:a.pdf"):
            with self.subTest(value=value), self.assertRaises(backend.ValidationError):
                backend.validate_input(value)

        with tempfile.TemporaryDirectory() as directory:
            nested = Path(directory) / "looks-like-a-scheme:"
            nested.mkdir()
            path = nested / "legal.pdf"
            path.write_bytes(b"x")
            raw_path = f"{nested}//legal.pdf"
            resolved, _warnings = backend.validate_input(raw_path)
            self.assertEqual(resolved, path.resolve())

    def test_existing_relative_name_with_colon_is_local(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "informe:final.pdf"
            path.write_bytes(b"x")
            # Path.stat() resolves relative paths through the process cwd, so
            # exercise the public behavior from the temporary directory.
            old_cwd = os.getcwd()
            try:
                os.chdir(root)
                resolved, _warnings = backend.validate_input(path.name)
            finally:
                os.chdir(old_cwd)
            self.assertEqual(resolved, path.resolve())

    def test_large_file_preview_is_best_effort(self):
        with mock.patch.object(Path, "stat") as patched:
            patched.return_value = os.stat_result((stat.S_IFREG | 0o600, 0, 0, 0, 0, 0, backend.LARGE_FILE_BYTES + 1, 0, 0, 0))
            self.assertIn("Archivo grande", backend.large_file_warning("document.pdf"))
            patched.side_effect = PermissionError("no access")
            self.assertIsNone(backend.large_file_warning("document.pdf"))
        self.assertIsNone(backend.large_file_warning("bad\x00.pdf"))


class OutputTests(unittest.TestCase):
    def test_snapshot_is_private_fsynced_and_rejects_last_moment_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.PDF"
            source.write_bytes(b"secret")
            snapshot = backend.snapshot_local(source, root)
            try:
                self.assertEqual(snapshot.suffix, ".pdf")
                self.assertEqual(snapshot.read_bytes(), b"secret")
                self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)
            finally:
                snapshot.unlink()

            replacement = root / "replacement.pdf"
            replacement.write_bytes(b"other")
            source.unlink()
            source.symlink_to(replacement)
            with self.assertRaises(backend.ValidationError):
                backend.snapshot_local(source, root)
            self.assertFalse(list(root.glob(".markitdown-source-*")))

    def test_snapshot_rejects_regular_inode_replacement_after_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            source.write_bytes(b"original")
            path, _warnings, identity = backend._validate_input_details(str(source))
            replacement = root / "replacement.pdf"
            replacement.write_bytes(b"replacement")
            os.replace(replacement, source)
            with self.assertRaisesRegex(backend.ValidationError, "sustituido"):
                backend.snapshot_local(path, root, identity)
            self.assertFalse(list(root.glob(".markitdown-source-*")))

    def test_sequential_and_concurrent_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "informe ü ' $();-.pdf"
            source.write_bytes(b"pdf")
            first = backend.save_markdown("one", source, root)
            second = backend.save_markdown("two", source, root)
            self.assertEqual(first.name, "informe ü ' $();-.md")
            self.assertEqual(second.name, "informe ü ' $();--2.md")
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                paths = list(pool.map(lambda i: backend.save_markdown(str(i), source, root), range(16)))
            self.assertEqual(len(set(paths)), 16)
            self.assertTrue(all(path.read_text() != "" for path in paths))
            self.assertFalse(list(root.glob(".markitdown-*.tmp")))

    def test_temporary_removed_on_publish_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "x.pdf"
            source.write_bytes(b"x")
            with mock.patch.object(backend, "_publish_noreplace", side_effect=PermissionError("no")):
                with self.assertRaises(PermissionError):
                    backend.save_markdown("text", source, root)
            self.assertFalse(list(root.glob(".markitdown-*.tmp")))

    def test_hardlink_fallback_preserves_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "x.pdf"
            source.write_bytes(b"x")
            (root / "x.md").write_text("existing")
            with mock.patch.object(backend, "_rename_noreplace", return_value=False):
                output = backend.save_markdown("new", source, root)
            self.assertEqual((root / "x.md").read_text(), "existing")
            self.assertEqual(output.name, "x-2.md")
            self.assertEqual(output.read_text(), "new")

    def test_directory_is_fsynced_after_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "x.pdf"
            source.write_bytes(b"x")
            with mock.patch.object(backend, "_fsync_directory") as sync_dir:
                output = backend.save_markdown("new", source, root)
            sync_dir.assert_called_once_with(root)
            self.assertEqual(output.read_text(), "new")

    def test_downloads_xdg_and_fallback(self):
        ok = subprocess.CompletedProcess([], 0, stdout="/custom/Descargas\n", stderr="")
        self.assertEqual(backend.downloads_directory(lambda *a, **k: ok), Path("/custom/Descargas"))
        bad = subprocess.CompletedProcess([], 1, stdout="", stderr="")
        with mock.patch.object(Path, "home", return_value=Path("/home/test")):
            self.assertEqual(backend.downloads_directory(lambda *a, **k: bad), Path("/home/test/Downloads"))


class IntegrationUnitTests(unittest.TestCase):
    def test_markitdown_uses_local_api_and_disables_plugins(self):
        calls = []
        class FakeMarkItDown:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))
            def convert_local(self, path):
                calls.append(("convert_local", path))
                return type("Result", (), {"text_content": "ok"})()
        fake_module = type("Module", (), {"MarkItDown": FakeMarkItDown})()
        with mock.patch.dict(sys.modules, {"markitdown": fake_module}):
            self.assertEqual(backend.convert_local(Path("/tmp/example.pdf")), "ok")
        self.assertEqual(calls[0], ("init", {"enable_plugins": False}))
        self.assertEqual(calls[1], ("convert_local", "/tmp/example.pdf"))

    def test_clipboard_argv_and_stdin(self):
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0)

        self.assertEqual(backend.copy_clipboard("a;$()\n", run), (True, ""))
        self.assertEqual(calls[0][0], ["wl-copy", "--type", "text/plain"])
        self.assertNotIn("capture_output", calls[0][1])
        self.assertIs(calls[0][1]["stdout"], subprocess.DEVNULL)
        self.assertIs(calls[0][1]["stderr"], subprocess.DEVNULL)
        self.assertEqual(calls[0][1]["input"], "a;$()\n")
        self.assertTrue(calls[0][1]["text"])
        self.assertFalse(calls[0][1]["check"])
        self.assertEqual(calls[0][1]["timeout"], 30)

    def test_clipboard_nonzero_reports_return_code(self):
        result = subprocess.CompletedProcess(["wl-copy"], 7)
        self.assertEqual(
            backend.copy_clipboard("text", lambda *_a, **_k: result),
            (False, "wl-copy falló (código 7)"),
        )

    def test_notification_argv(self):
        seen = []

        def run(argv, **kwargs):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        self.assertTrue(backend.notify("t;$()", "d\n'", run=run))
        self.assertEqual(seen[0][-2:], ["t;$()", "d\n'"])

    def test_partial_clipboard_preserves_output_and_notification_failure_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "a.pdf"
            source.write_bytes(b"x")
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                code = backend.run_conversion(
                    str(source), destination_dir=root,
                    converter=lambda _p: "markdown",
                    clipboard=lambda _t: (False, "sin compositor"),
                    notifier=lambda *_a: False,
                )
            self.assertEqual(code, backend.EXIT_CLIPBOARD)
            self.assertEqual((root / "a.md").read_text(), "markdown")
            self.assertIn('"status": "partial"', output.getvalue())

    def test_empty_markdown_is_conversion_error_without_output_or_clipboard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "a.pdf"
            source.write_bytes(b"x")
            clipboard = mock.Mock(return_value=(True, ""))
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                for text in ("", " \n\t "):
                    with self.subTest(text=repr(text)):
                        code = backend.run_conversion(
                            str(source), destination_dir=root,
                            converter=lambda _p, value=text: value,
                            clipboard=clipboard, notifier=lambda *_a: True,
                        )
                        self.assertEqual(code, backend.EXIT_CONVERSION)
            self.assertFalse((root / "a.md").exists())
            self.assertFalse(list(root.glob(".markitdown-source-*")))
            clipboard.assert_not_called()
            self.assertIn("vacío", output.getvalue())

    def test_conversion_error_and_cancel_protocol_leave_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "a.pdf"
            source.write_bytes(b"x")
            quiet = lambda *_a: True
            with mock.patch("sys.stdout", io.StringIO()):
                failed = backend.run_conversion(str(source), destination_dir=root,
                    converter=lambda _p: (_ for _ in ()).throw(RuntimeError("bad")), notifier=quiet)
                cancelled = backend.run_conversion(str(source), destination_dir=root,
                    converter=lambda _p: (_ for _ in ()).throw(backend.CancelledError()), notifier=quiet)
            self.assertEqual(failed, backend.EXIT_CONVERSION)
            self.assertEqual(cancelled, backend.EXIT_CANCELLED)
            self.assertFalse((root / "a.md").exists())
            self.assertFalse(list(root.glob(".markitdown-source-*")))

    def test_signal_during_finalization_after_publish_returns_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "a.pdf"
            source.write_bytes(b"x")
            cancellation = backend.Cancellation()
            real_sync = backend._fsync_directory

            def signal_while_finalizing(path):
                self.assertTrue(cancellation.published)
                cancellation.signal()
                real_sync(path)

            with mock.patch.object(backend, "_fsync_directory", side_effect=signal_while_finalizing), \
                    mock.patch("sys.stdout", io.StringIO()) as output:
                code = backend.run_conversion(
                    str(source), destination_dir=root,
                    converter=lambda snapshot: snapshot.read_text(),
                    clipboard=lambda _text: (True, ""),
                    notifier=lambda *_args: True,
                    cancellation=cancellation,
                )
            self.assertEqual(code, 0)
            self.assertIn('"status": "success"', output.getvalue())
            self.assertEqual((root / "a.md").read_text(), "x")
            self.assertFalse(list(root.glob(".markitdown-source-*")))


if __name__ == "__main__":
    unittest.main()
