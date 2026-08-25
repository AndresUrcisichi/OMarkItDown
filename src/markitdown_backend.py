#!/usr/bin/env python3
"""Backend aislado: valida, convierte, publica, copia y notifica."""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
from typing import Callable

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx"}
LARGE_FILE_BYTES = 100 * 1024 * 1024
EXIT_VALIDATION = 2
EXIT_CONVERSION = 3
EXIT_SAVE = 4
EXIT_CLIPBOARD = 5
EXIT_CANCELLED = 130


class ValidationError(ValueError):
    pass


class CancelledError(RuntimeError):
    pass


class Cancellation:
    """La publicación es una sección crítica: una vez visible, no se cancela."""

    def __init__(self) -> None:
        self.requested = False
        self.critical = False
        self.published = False

    def signal(self, _signum: int = 0, _frame: object = None) -> None:
        self.requested = True
        if not self.critical and not self.published:
            raise CancelledError()

    def checkpoint(self) -> None:
        if self.requested and not self.published:
            raise CancelledError()


URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def large_file_warning(raw_path: str) -> str | None:
    """Best-effort GUI hint; validate_input remains authoritative."""
    try:
        size = Path(raw_path).expanduser().stat().st_size
    except (OSError, ValueError):
        return None
    if size > LARGE_FILE_BYTES:
        return f"Archivo grande ({size / 1024 / 1024:.1f} MiB)."
    return None


def _validate_input_details(raw_path: str) -> tuple[Path, list[str], tuple[int, int]]:
    if "\x00" in raw_path:
        raise ValidationError("La ruta contiene un byte NUL.")
    if not raw_path:
        raise ValidationError("Introduce una ruta local.")
    entered = Path(raw_path).expanduser()
    try:
        # Una ruta local existente con ':' gana frente a la heurística de URI.
        entered.stat()
    except FileNotFoundError as exc:
        if URI_SCHEME.match(raw_path):
            raise ValidationError("Las URI y rutas remotas no están admitidas.") from exc
        raise ValidationError("El archivo no existe.") from exc
    except OSError as exc:
        raise ValidationError(f"No se puede acceder al archivo: {exc}") from exc
    path = entered.resolve(strict=True)
    try:
        info = path.stat()
    except OSError as exc:
        raise ValidationError(f"No se puede acceder al archivo: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ValidationError("La ruta debe ser un archivo local regular.")
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValidationError("Formato no admitido; usa PDF, DOCX, PPTX o XLSX.")
    warnings = []
    if info.st_size > LARGE_FILE_BYTES:
        warnings.append(f"Archivo grande ({info.st_size / 1024 / 1024:.1f} MiB).")
    return path, warnings, (info.st_dev, info.st_ino)


def validate_input(raw_path: str) -> tuple[Path, list[str]]:
    """Valida la entrada conservando la API pública de dos elementos."""
    path, warnings, _identity = _validate_input_details(raw_path)
    return path, warnings


def downloads_directory(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> Path:
    try:
        result = run(
            ["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True,
            check=False, timeout=5,
        )
        value = result.stdout.rstrip("\n") if result.returncode == 0 else ""
        if value and "\x00" not in value:
            candidate = Path(value).expanduser()
            if candidate.is_absolute():
                return candidate
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / "Downloads"


def convert_local(path: Path) -> str:
    # Import tardío: este proceso debe ejecutarse con el Python aislado de uv.
    from markitdown import MarkItDown  # type: ignore

    result = MarkItDown(enable_plugins=False).convert_local(str(path))
    text = result.text_content
    if not isinstance(text, str):
        raise RuntimeError("MarkItDown no devolvió contenido de texto.")
    return text


def snapshot_local(path: Path, directory: Path,
                   expected_identity: tuple[int, int] | None = None) -> Path:
    """Copia desde un descriptor seguro a un temporal privado del destino."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(path, flags)
    except OSError as exc:
        raise ValidationError(f"No se pudo abrir de forma segura el archivo: {exc}") from exc
    temporary: Path | None = None
    try:
        info = os.fstat(source_fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValidationError("La ruta dejó de ser un archivo regular.")
        if expected_identity is not None and (info.st_dev, info.st_ino) != expected_identity:
            raise ValidationError("El archivo fue sustituido después de validarlo.")
        target_fd, name = tempfile.mkstemp(
            prefix=".markitdown-source-", suffix=path.suffix.lower(), dir=directory
        )
        temporary = Path(name)
        try:
            os.fchmod(target_fd, 0o600)
            with os.fdopen(source_fd, "rb", closefd=False) as source, os.fdopen(target_fd, "wb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
        except BaseException:
            try:
                os.close(target_fd)
            except OSError:
                pass
            raise
        return temporary
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    finally:
        os.close(source_fd)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_noreplace(source: Path, destination: Path) -> bool:
    """Usa renameat2 si está disponible; False solicita fallback por hard-link."""
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameat2", None)
    if function is None:
        return False
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    rc = function(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if rc == 0:
        return True
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(destination)
    if error in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
        return False
    raise OSError(error, os.strerror(error), destination)


def _publish_noreplace(source: Path, destination: Path) -> None:
    if _rename_noreplace(source, destination):
        return
    # Mismo filesystem/directorio: link() crea el nombre de forma atómica y falla
    # con EEXIST; después se retira el nombre temporal.
    os.link(source, destination)
    source.unlink()


def save_markdown(text: str, source: Path, destination_dir: Path,
                  cancellation: Cancellation | None = None) -> Path:
    destination_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".markitdown-", suffix=".tmp", dir=destination_dir)
    temporary = Path(temporary_name)
    os.chmod(temporary, 0o600)
    published = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        stem = source.stem
        index = 1
        while True:
            name = f"{stem}.md" if index == 1 else f"{stem}-{index}.md"
            candidate = destination_dir / name
            try:
                if cancellation is not None:
                    cancellation.checkpoint()
                    cancellation.critical = True
                _publish_noreplace(temporary, candidate)
                published = True
                if cancellation is not None:
                    # Desde que el nombre es visible una señal ya no puede
                    # convertir un resultado publicado en "cancelled".
                    cancellation.published = True
                _fsync_directory(destination_dir)
                return candidate
            except FileExistsError:
                index += 1
            finally:
                if cancellation is not None:
                    cancellation.critical = False
    finally:
        if not published:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def copy_clipboard(text: str, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> tuple[bool, str]:
    try:
        result = run(
            ["wl-copy", "--type", "text/plain"], input=text, text=True,
            capture_output=True, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return (True, "") if result.returncode == 0 else (False, result.stderr.strip() or "wl-copy falló")


def notify(headline: str, description: str, urgency: str = "normal",
           run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> bool:
    try:
        result = run(
            ["omarchy", "notification", "send", "--app-name", "MarkItDown",
             "-g", "󰈙", "-u", urgency, headline, description],
            capture_output=True, text=True, check=False, timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def run_conversion(raw_path: str, *, destination_dir: Path | None = None,
                   converter: Callable[[Path], str] = convert_local,
                   clipboard: Callable[[str], tuple[bool, str]] = copy_clipboard,
                    notifier: Callable[[str, str, str], bool] = notify,
                    cancellation: Cancellation | None = None) -> int:
    cancellation = cancellation or Cancellation()
    try:
        path, warnings, source_identity = _validate_input_details(raw_path)
    except ValidationError as exc:
        message = str(exc)
        notifier("No se pudo convertir", message, "critical")
        emit({"status": "error", "stage": "validation", "message": message})
        return EXIT_VALIDATION
    target_dir = destination_dir or downloads_directory()
    snapshot: Path | None = None
    try:
        cancellation.checkpoint()
        snapshot = snapshot_local(path, target_dir, source_identity)
        cancellation.checkpoint()
        text = converter(snapshot)
        if not text.strip():
            raise ValueError("MarkItDown devolvió Markdown vacío o solo espacios.")
        cancellation.checkpoint()
    except CancelledError:
        emit({"status": "cancelled", "message": "Conversión cancelada."})
        return EXIT_CANCELLED
    except Exception as exc:
        message = f"Falló la conversión: {exc}"
        notifier("No se pudo convertir", message, "critical")
        emit({"status": "error", "stage": "conversion", "message": message})
        return EXIT_CONVERSION
    finally:
        # La instantánea contiene el documento original y no debe sobrevivir
        # ni siquiera a excepciones que no formen parte del protocolo normal.
        if snapshot is not None:
            snapshot.unlink(missing_ok=True)
    try:
        output = save_markdown(text, path, target_dir, cancellation)
    except CancelledError:
        emit({"status": "cancelled", "message": "Conversión cancelada."})
        return EXIT_CANCELLED
    except Exception as exc:
        message = f"No se pudo guardar: {exc}"
        notifier("No se pudo guardar", message, "critical")
        emit({"status": "error", "stage": "save", "message": message})
        return EXIT_SAVE
    copied, clipboard_error = clipboard(text)
    if not copied:
        message = f"Guardado en {output}, pero no se copió al portapapeles: {clipboard_error}"
        notified = notifier("Conversión completada parcialmente", message, "normal")
        emit({"status": "partial", "output": str(output), "message": message,
              "warnings": warnings, "notification_sent": notified})
        return EXIT_CLIPBOARD
    message = f"Guardado y copiado: {output}"
    notified = notifier("Conversión completada", message, "normal")
    emit({"status": "success", "output": str(output), "message": message,
          "warnings": warnings, "notification_sent": notified})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    args = parser.parse_args(argv)

    cancellation = Cancellation()
    signal.signal(signal.SIGTERM, cancellation.signal)
    signal.signal(signal.SIGINT, cancellation.signal)
    try:
        return run_conversion(args.path, cancellation=cancellation)
    except CancelledError:
        emit({"status": "cancelled", "message": "Conversión cancelada."})
        return EXIT_CANCELLED


if __name__ == "__main__":
    raise SystemExit(main())
