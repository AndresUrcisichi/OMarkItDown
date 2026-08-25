from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("menu_patch", ROOT / "src/menu_patch.py")
menu_patch = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(menu_patch)


class MenuTests(unittest.TestCase):
    def test_add_idempotent_remove_preserves_unrelated(self):
        original = '{\n  // comentario ajeno\n  "other": {"label": "Otro"}\n}\n'
        added = menu_patch.add(original)
        self.assertEqual(menu_patch.add(added), added)
        self.assertIn('"other"', added)
        self.assertEqual(menu_patch.remove(added), original)

    def test_foreign_conflict_and_modified_block(self):
        with self.assertRaises(ValueError):
            menu_patch.add('{"trigger.markitdown": {"label": "Ajeno"}}')
        changed = menu_patch.add("{}\n").replace('"label": "MarkItDown"', '"label": "Personalizado"')
        with self.assertRaises(ValueError):
            menu_patch.remove(changed)

    def test_scanner_ignores_braces_and_trigger_text_in_strings_and_comments(self):
        original = '''{
  "text": "} { trigger.markitdown // not a key",
  // } "trigger.markitdown": {}
  /* { nested-looking } */
  "other": {"value": "\\\"}"}
}
'''
        added = menu_patch.add(original, "'/safe path'")
        self.assertEqual(menu_patch.remove(added, "'/safe path'"), original)

    def test_bom_is_preserved(self):
        original = '\ufeff{/* brace } */ "other": "{"}\n'
        added = menu_patch.add(original)
        self.assertTrue(added.startswith("\ufeff"))
        self.assertEqual(menu_patch.remove(added), original)

    def test_invalid_jsonc_structure_and_partial_blocks_are_rejected(self):
        invalid = (
            '{"x": "unterminated}',
            '{ /* unterminated',
            '{ "x": {}',
            '{ "x": ] }',
            '{} {}',
            '{} trailing',
            '{\n' + menu_patch.BEGIN + '\n}\n',
        )
        for content in invalid:
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    menu_patch.add(content)

    def test_missing_jsonc_separator_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "separador"):
            menu_patch.add('{"a": 1 "b": 2}')

    def test_fragment_fields_match_canonical_block(self):
        fragment = (ROOT / "config/omarchy-menu.fragment.jsonc").read_text()
        for key, value in menu_patch.FIELDS.items():
            self.assertIn(f'{json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}', fragment)

    def test_atomic_cli_preserves_bom_and_uses_private_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            menu = Path(directory) / "menu.jsonc"
            menu.write_bytes(b'\xef\xbb\xbf{\n  "other": true\n}\n')
            menu.chmod(0o644)
            result = subprocess.run(
                ["python", str(ROOT / "src/menu_patch.py"), "add", str(menu), "--action-path", "/safe path", "--backup-suffix", ".bak"],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(menu.read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertEqual(menu.stat().st_mode & 0o777, 0o600)
            self.assertTrue((menu.parent / "menu.jsonc.bak").read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertEqual((menu.parent / "menu.jsonc.bak").stat().st_mode & 0o777, 0o600)


class InstallerTests(unittest.TestCase):
    def run_install(self, config: Path, *args: str, fail: str | None = None,
                    replace_before_remove: int | None = None, uv_dir: Path | None = None,
                    uv_log: Path | None = None, uv_fail: bool = False,
                    path: str | None = None):
        env = {**os.environ, "HOME": str(config.parent / "home"), "XDG_CONFIG_HOME": str(config)}
        if uv_dir is not None:
            env["PATH"] = f"{uv_dir}:{env.get('PATH', '')}"
        if path is not None:
            env["PATH"] = path
        if uv_log is not None:
            env["MARKITDOWN_OMARCHY_TEST_UV_LOG"] = str(uv_log)
        if uv_fail:
            env["MARKITDOWN_OMARCHY_TEST_UV_FAIL"] = "1"
        if fail or replace_before_remove is not None:
            env["MARKITDOWN_OMARCHY_TESTING"] = "1"
        if fail:
            env["MARKITDOWN_OMARCHY_TEST_FAIL"] = fail
        if replace_before_remove is not None:
            env["MARKITDOWN_OMARCHY_TEST_REPLACE_BEFORE_REMOVE"] = str(replace_before_remove)
        return subprocess.run(
            ["/bin/sh", str(ROOT / "install.sh"), *args, "--config-root", str(config)],
            cwd=ROOT, env=env, text=True, capture_output=True,
        )

    def test_dry_run_apply_repeat_and_selective_uninstall(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            menu = config / "omarchy/extensions/omarchy-menu.jsonc"
            menu.parent.mkdir(parents=True)
            original = '{\n  // ajeno\n  "other": {"label": "Otro"}\n}\n'
            menu.write_text(original)
            dry = self.run_install(config)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual(menu.read_text(), original)
            first = self.run_install(config, "--apply")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("trigger.markitdown", menu.read_text())
            second = self.run_install(config, "--apply")
            self.assertEqual(second.returncode, 0, second.stderr)
            text = menu.read_text()
            action_match = re.search(r'"action"\s*:\s*("(?:\\.|[^"\\])*")', text)
            self.assertIsNotNone(action_match)
            action = json.loads(action_match.group(1))
            self.assertEqual(action, shlex.quote(str(config / "omarchy/scripts/markitdown-convert")))
            foreign = config / "omarchy/scripts/foreign"
            foreign.write_text("keep")
            own = config / "omarchy/markitdown-omarchy/markitdown_drop.py"
            managed_content = own.read_text()
            own.write_text(managed_content + "\n# cambio usuario\n")
            blocked = self.run_install(config, "--apply", "--uninstall")
            self.assertEqual(blocked.returncode, 3)
            self.assertIn("trigger.markitdown", menu.read_text())
            self.assertTrue((config / "omarchy/scripts/markitdown-convert").exists())
            own.write_text(managed_content)
            uninstall = self.run_install(config, "--apply", "--uninstall")
            self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
            self.assertEqual(menu.read_text(), original)
            self.assertEqual(foreign.read_text(), "keep")
            self.assertFalse(own.exists())
            self.assertFalse((config / "omarchy/scripts/markitdown-convert").exists())

    def test_action_quotes_config_root_and_install_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config space 'quoted'"
            menu = config / "omarchy/extensions/omarchy-menu.jsonc"
            menu.parent.mkdir(parents=True)
            original = '{\n  "other": true\n}\n'
            menu.write_text(original)
            failed = self.run_install(config, "--apply", fail="after-menu")
            self.assertEqual(failed.returncode, 97, failed.stderr)
            self.assertEqual(menu.read_text(), original)
            self.assertFalse((config / "omarchy/scripts/markitdown-convert").exists())
            self.assertFalse((config / "omarchy/markitdown-omarchy/install-manifest.tsv").exists())
            self.assertFalse(list(config.glob(".markitdown-omarchy.transaction.*")))
            applied = self.run_install(config, "--apply")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            match = re.search(r'"action"\s*:\s*("(?:\\.|[^"\\])*")', menu.read_text())
            self.assertEqual(
                json.loads(match.group(1)),
                shlex.quote(str(config / "omarchy/scripts/markitdown-convert")),
            )

    def test_uninstall_failure_rolls_back_everything(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            applied = self.run_install(config, "--apply")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            menu = config / "omarchy/extensions/omarchy-menu.jsonc"
            menu_before = menu.read_bytes()
            launcher = config / "omarchy/scripts/markitdown-convert"
            launcher_before = launcher.read_bytes()
            failed = self.run_install(config, "--apply", "--uninstall", fail="after-menu")
            self.assertEqual(failed.returncode, 97, failed.stderr)
            self.assertEqual(menu.read_bytes(), menu_before)
            self.assertEqual(launcher.read_bytes(), launcher_before)
            self.assertTrue((config / "omarchy/markitdown-omarchy/install-manifest.tsv").exists())

    def test_uninstall_rechecks_hash_immediately_before_remove(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            applied = self.run_install(config, "--apply")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            menu = config / "omarchy/extensions/omarchy-menu.jsonc"
            menu_before = menu.read_bytes()
            launcher = config / "omarchy/scripts/markitdown-convert"

            raced = self.run_install(
                config, "--apply", "--uninstall", replace_before_remove=1
            )

            self.assertEqual(raced.returncode, 3, raced.stderr)
            self.assertEqual(menu.read_bytes(), menu_before)
            self.assertIn("modificación concurrente inyectada", launcher.read_text())
            self.assertTrue((config / "omarchy/markitdown-omarchy/markitdown_backend.py").exists())
            self.assertTrue((config / "omarchy/markitdown-omarchy/install-manifest.tsv").exists())

    def test_relative_config_root_is_rejected_without_creating_it(self):
        relative = "relative-config-root-for-test"
        target = ROOT / relative
        self.assertFalse(target.exists())
        result = subprocess.run(
            ["sh", str(ROOT / "install.sh"), "--apply", "--config-root", relative],
            cwd=ROOT, env={**os.environ, "HOME": str(ROOT / "unused-home")},
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("ruta absoluta", result.stderr)
        self.assertFalse(target.exists())

    def test_dry_run_reports_conflict_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            target = config / "omarchy/scripts/markitdown-convert"
            target.parent.mkdir(parents=True)
            target.write_text("foreign")
            before = target.read_bytes()
            result = self.run_install(config)
            self.assertEqual(result.returncode, 3)
            self.assertEqual(target.read_bytes(), before)
            self.assertFalse((config / "omarchy/extensions/omarchy-menu.jsonc").exists())

    def test_failure_variable_is_ignored_without_explicit_test_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            env = {
                **os.environ,
                "HOME": str(Path(directory) / "home"),
                "XDG_CONFIG_HOME": str(config),
                "MARKITDOWN_OMARCHY_TEST_FAIL": "after-menu",
            }
            result = subprocess.run(
                ["sh", str(ROOT / "install.sh"), "--apply", "--config-root", str(config)],
                cwd=ROOT, env=env, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_ambiguous_runtime_blocks_uninstall_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            bin_dir = Path(directory) / "bin"
            bin_dir.mkdir()
            fake_uv = bin_dir / "uv"
            fake_uv.write_text(
                "#!/bin/sh\n"
                "project=\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  [ \"$1\" = --project ] && { shift; project=$1; }\n"
                "  shift\n"
                "done\n"
                "mkdir -p \"$project/.venv/bin\"\n"
                "printf runtime > \"$project/.venv/bin/python\"\n"
            )
            fake_uv.chmod(0o755)
            applied = self.run_install(config, "--apply", "--with-runtime", uv_dir=bin_dir)
            self.assertEqual(applied.returncode, 0, applied.stderr)

            menu = config / "omarchy/extensions/omarchy-menu.jsonc"
            app = config / "omarchy/markitdown-omarchy"
            launcher = config / "omarchy/scripts/markitdown-convert"
            manifest = app / "install-manifest.tsv"
            runtime = app / ".venv"
            (runtime / "foreign").write_text("keep")
            snapshots = {
                "menu": menu.read_bytes(),
                "launcher": launcher.read_bytes(),
                "backend": (app / "markitdown_backend.py").read_bytes(),
                "manifest": manifest.read_bytes(),
                "runtime": sorted(
                    (path.relative_to(runtime), path.read_bytes())
                    for path in runtime.rglob("*") if path.is_file()
                ),
            }

            blocked = self.run_install(config, "--apply", "--uninstall")
            self.assertEqual(blocked.returncode, 3, blocked.stderr)
            self.assertEqual(menu.read_bytes(), snapshots["menu"])
            self.assertEqual(launcher.read_bytes(), snapshots["launcher"])
            self.assertEqual((app / "markitdown_backend.py").read_bytes(), snapshots["backend"])
            self.assertEqual(manifest.read_bytes(), snapshots["manifest"])
            self.assertEqual(
                sorted((path.relative_to(runtime), path.read_bytes()) for path in runtime.rglob("*") if path.is_file()),
                snapshots["runtime"],
            )

    def test_runtime_opt_in_uses_locked_uv_and_owned_runtime_only(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config space"
            bin_dir = Path(directory) / "bin"
            bin_dir.mkdir()
            uv_log = Path(directory) / "uv.log"
            fake_uv = bin_dir / "uv"
            fake_uv.write_text(
                "#!/bin/sh\n"
                "printf '%s' \"$#\" >> \"$MARKITDOWN_OMARCHY_TEST_UV_LOG\"\n"
                "for argument in \"$@\"; do printf '[%s]' \"$argument\" >> \"$MARKITDOWN_OMARCHY_TEST_UV_LOG\"; done\n"
                "printf '\\n' >> \"$MARKITDOWN_OMARCHY_TEST_UV_LOG\"\n"
                "[ \"${MARKITDOWN_OMARCHY_TEST_UV_FAIL:-0}\" = 1 ] && exit 42\n"
                "project=\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  [ \"$1\" = --project ] && { shift; project=$1; }\n"
                "  shift\n"
                "done\n"
                "mkdir -p \"$project/.venv/bin\"\n"
                "printf runtime > \"$project/.venv/bin/python\"\n"
            )
            fake_uv.chmod(0o755)

            default = self.run_install(config, "--apply")
            self.assertEqual(default.returncode, 0, default.stderr)
            self.assertFalse(uv_log.exists())
            dry = self.run_install(config, "--with-runtime", uv_dir=bin_dir, uv_log=uv_log)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertIn(str(config / "omarchy/markitdown-omarchy"), dry.stdout)
            self.assertIn("Python 3.12", dry.stdout)
            self.assertIn("--locked", dry.stdout)
            self.assertIn("descargas", dry.stdout)
            self.assertFalse(uv_log.exists())

            applied = self.run_install(config, "--apply", "--with-runtime", uv_dir=bin_dir, uv_log=uv_log)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertEqual(uv_log.read_text(), f"6[sync][--project][{config / 'omarchy/markitdown-omarchy'}][--python][3.12][--locked]\n")
            runtime = config / "omarchy/markitdown-omarchy/.venv"
            self.assertTrue((runtime / ".markitdown-omarchy-runtime-owner").is_file())
            self.assertTrue((runtime / ".markitdown-omarchy-runtime-manifest").is_file())
            self.assertTrue((config / "omarchy/markitdown-omarchy/uv.lock").is_file())
            repeated = self.run_install(config, "--apply", "--with-runtime", uv_dir=bin_dir, uv_log=uv_log)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(len(uv_log.read_text().splitlines()), 1)

            (runtime / "foreign").write_text("keep")
            blocked = self.run_install(config, "--apply", "--uninstall")
            self.assertEqual(blocked.returncode, 3, blocked.stderr)
            self.assertTrue(runtime.exists())
            (runtime / "foreign").unlink()
            uninstall = self.run_install(config, "--apply", "--uninstall")
            self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
            self.assertFalse(runtime.exists())

    def test_runtime_requires_uv_before_installing_files_and_preserves_failed_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            bin_dir = Path(directory) / "bin"
            bin_dir.mkdir()
            (bin_dir / "date").symlink_to("/usr/bin/date")
            (bin_dir / "dirname").symlink_to("/usr/bin/dirname")
            missing = self.run_install(config, "--apply", "--with-runtime", path=str(bin_dir))
            self.assertEqual(missing.returncode, 4)
            self.assertIn("uv no está disponible", missing.stderr)
            self.assertFalse((config / "omarchy/scripts/markitdown-convert").exists())

            fake_uv = bin_dir / "uv"
            fake_uv.write_text("#!/bin/sh\nmkdir -p \"$3/.venv\"\nexit 42\n")
            fake_uv.chmod(0o755)
            failed = self.run_install(config, "--apply", "--with-runtime", uv_dir=bin_dir, uv_fail=True)
            self.assertEqual(failed.returncode, 42, failed.stderr)
            app = config / "omarchy/markitdown-omarchy"
            self.assertFalse((config / "omarchy/scripts/markitdown-convert").exists())
            self.assertFalse((config / "omarchy/extensions/omarchy-menu.jsonc").exists())
            self.assertFalse((app / "markitdown_backend.py").exists())
            self.assertFalse((app / "markitdown_drop.py").exists())
            self.assertFalse((app / "pyproject.toml").exists())
            self.assertFalse((app / ".python-version").exists())
            self.assertFalse((app / "uv.lock").exists())
            self.assertFalse((app / "install-manifest.tsv").exists())
            self.assertTrue((app / ".venv").exists())

    def test_foreign_trigger_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            menu = config / "omarchy/extensions/omarchy-menu.jsonc"
            menu.parent.mkdir(parents=True)
            content = '{"trigger.markitdown": {"label": "Ajeno"}}\n'
            menu.write_text(content)
            result = self.run_install(config, "--apply")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(menu.read_text(), content)


if __name__ == "__main__":
    unittest.main()
