#!/usr/bin/python
"""Frontend GTK4 para MarkItDown-Omarchy."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import threading

from markitdown_backend import large_file_warning

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402


def local_drop_path(value: object) -> tuple[str | None, str | None]:
    """Normaliza Gdk.FileList (GTK4 actual) y Gio.File (fallback)."""
    files = list(value.get_files()) if hasattr(value, "get_files") else [value]
    if len(files) != 1:
        return None, "Arrastra exactamente un archivo local."
    path = files[0].get_path() if hasattr(files[0], "get_path") else None
    if path is None:
        return None, "El elemento arrastrado no es un archivo local."
    return path, None


class Window(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, backend_python: str, backend: str) -> None:
        super().__init__(application=app, title="MarkItDown")
        self.backend_python = backend_python
        self.backend = backend
        self.process: subprocess.Popen[str] | None = None
        self.cancel_requested = False
        self.closing = False
        self.connect("close-request", self.on_close_request)
        self.set_default_size(620, 240)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(18); box.set_margin_bottom(18)
        box.set_margin_start(18); box.set_margin_end(18)
        self.set_child(box)
        title = Gtk.Label(label="Convertir documento local a Markdown", xalign=0)
        title.add_css_class("title-2")
        box.append(title)
        row = Gtk.Box(spacing=8)
        self.entry = Gtk.Entry(hexpand=True, placeholder_text="Ruta PDF, DOCX, PPTX o XLSX")
        self.entry.connect("activate", lambda *_: self.start())
        row.append(self.entry)
        choose = Gtk.Button(label="Elegir…")
        choose.connect("clicked", self.choose_file)
        row.append(choose)
        box.append(row)
        drop_label = Gtk.Label(label="Arrastra aquí un único archivo local")
        drop_label.set_size_request(-1, 64)
        drop_label.add_css_class("card")
        drop_type = getattr(Gdk, "FileList", Gio.File)
        target = Gtk.DropTarget.new(drop_type, Gdk.DragAction.COPY)
        target.connect("drop", self.on_drop)
        drop_label.add_controller(target)
        box.append(drop_label)
        self.status = Gtk.Label(label="Listo", xalign=0, wrap=True, selectable=True)
        box.append(self.status)
        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.convert_button = Gtk.Button(label="Convertir")
        self.convert_button.add_css_class("suggested-action")
        self.convert_button.connect("clicked", lambda *_: self.start())
        actions.append(self.convert_button)
        self.cancel_button = Gtk.Button(label="Cancelar", sensitive=False)
        self.cancel_button.connect("clicked", lambda *_: self.cancel())
        actions.append(self.cancel_button)
        self.force_stop_button = Gtk.Button(label="Forzar detención", sensitive=False)
        self.force_stop_button.connect("clicked", lambda *_: self.force_stop())
        actions.append(self.force_stop_button)
        box.append(actions)

    def choose_file(self, _button: Gtk.Button) -> None:
        dialog = Gtk.FileDialog(title="Selecciona un documento")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        file_filter = Gtk.FileFilter(name="Documentos admitidos")
        for pattern in ("*.pdf", "*.PDF", "*.docx", "*.DOCX", "*.pptx", "*.PPTX", "*.xlsx", "*.XLSX"):
            file_filter.add_pattern(pattern)
        filters.append(file_filter)
        dialog.set_filters(filters)
        dialog.open(self, None, self.file_chosen)

    def file_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error as exc:
            if not exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self.status.set_text(f"No se pudo abrir el selector: {exc.message}")
            return
        path = file.get_path()
        if path is None:
            self.status.set_text("Solo se admiten archivos locales.")
            return
        self.entry.set_text(path)

    def on_drop(self, _target: Gtk.DropTarget, value: object, _x: float, _y: float) -> bool:
        path, error = local_drop_path(value)
        if error:
            self.status.set_text(error)
            return False
        assert path is not None
        self.entry.set_text(path)
        return True

    def start(self, confirmed_large_path: str | None = None) -> None:
        if self.process is not None:
            return
        if not Path(self.backend_python).is_file():
            self.status.set_text("Falta el entorno uv. Consulta README.md y crea .venv con Python 3.12.")
            return
        path = self.entry.get_text()
        warning = large_file_warning(path)
        if warning and path != confirmed_large_path:
            self.confirm_large_file(path, warning)
            return
        self.status.set_text("Convirtiendo…")
        self.convert_button.set_sensitive(False)
        self.cancel_button.set_sensitive(True)
        try:
            self.process = subprocess.Popen(
                [self.backend_python, self.backend, path], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, start_new_session=True,
            )
        except OSError as exc:
            self.process = None
            self.finish_ui(f"No se pudo iniciar el backend: {exc}")
            return
        self.cancel_requested = False
        threading.Thread(target=self.wait_backend, args=(self.process,), daemon=True).start()

    def confirm_large_file(self, path: str, warning: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True, buttons=Gtk.ButtonsType.NONE,
            text="Confirmación requerida para archivo grande",
        )
        dialog.set_secondary_text(f"{warning} ¿Quieres iniciar la conversión ahora?")
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        dialog.add_button("Convertir", Gtk.ResponseType.ACCEPT)
        dialog.connect("response", self.large_file_response, path)
        dialog.present()

    def large_file_response(self, dialog: Gtk.MessageDialog, response: int, path: str) -> None:
        dialog.destroy()
        if response == Gtk.ResponseType.ACCEPT:
            self.start(path)
        else:
            self.status.set_text("Conversión cancelada antes de iniciar el backend.")

    def wait_backend(self, process: subprocess.Popen[str]) -> None:
        stdout, stderr = process.communicate()
        payload = None
        for line in reversed(stdout.splitlines()):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        if payload:
            message = str(payload.get("message", "Conversión terminada."))
            warnings = payload.get("warnings") or []
            if warnings:
                message += "\nAdvertencia: " + "; ".join(map(str, warnings))
        elif process.returncode == -15:
            message = "Conversión cancelada."
        else:
            message = stderr.strip() or f"El backend terminó con código {process.returncode}."
        GLib.idle_add(self.backend_finished, process, message)

    def backend_finished(self, process: subprocess.Popen[str], message: str) -> bool:
        if self.process is process:
            self.process = None
            self.cancel_requested = False
            self.finish_ui(message)
            if self.closing:
                self.close()
        return GLib.SOURCE_REMOVE

    def finish_ui(self, message: str) -> None:
        self.status.set_text(message)
        self.convert_button.set_sensitive(True)
        self.cancel_button.set_sensitive(False)
        self.force_stop_button.set_sensitive(False)

    def cancel(self) -> None:
        process = self.process
        if process is None:
            return
        self.status.set_text("Cancelación solicitada; esperando al backend…")
        if self.cancel_requested:
            return
        self.cancel_requested = True
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                GLib.timeout_add_seconds(15, lambda: self.offer_force_stop(process))
            except ProcessLookupError:
                pass

    def offer_force_stop(self, process: subprocess.Popen[str]) -> bool:
        if self.process is process and self.cancel_requested and process.poll() is None:
            self.status.set_text(
                "El backend no terminó tras 15 segundos. Puedes forzar su detención; "
                "SIGKILL puede impedir su limpieza de instantáneas temporales."
            )
            self.force_stop_button.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def force_stop(self) -> None:
        process = self.process
        if process is None or not self.cancel_requested or process.poll() is not None:
            return
        try:
            # Solo se señaliza el PID del backend que creó esta ventana, nunca un PID ajeno.
            os.kill(process.pid, signal.SIGKILL)
            self.status.set_text(
                "Detención forzada solicitada. El backend podría no completar la limpieza; "
                "revisa Downloads antes de reintentar."
            )
            self.force_stop_button.set_sensitive(False)
        except ProcessLookupError:
            pass

    def on_close_request(self, _window: Gtk.Window) -> bool:
        if self.process is None:
            return False
        if not self.closing:
            self.closing = True
            self.convert_button.set_sensitive(False)
            self.cancel_button.set_sensitive(False)
            self.cancel()
        return True


class Application(Gtk.Application):
    def __init__(self, backend_python: str, backend: str) -> None:
        super().__init__(application_id="org.omarchy.MarkItDown")
        self.backend_python = backend_python
        self.backend = backend

    def do_activate(self) -> None:
        window = self.props.active_window or Window(self, self.backend_python, self.backend)
        window.present()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-python", required=True)
    parser.add_argument("--backend", required=True)
    args = parser.parse_args()
    return Application(args.backend_python, args.backend).run(None)


if __name__ == "__main__":
    raise SystemExit(main())
