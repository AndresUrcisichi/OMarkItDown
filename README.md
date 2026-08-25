# MarkItDown-Omarchy

MVP para abrir desde el menú de `SUPER+SPACE` una ventana GTK4 que convierte
PDF, DOCX, PPTX y XLSX locales a Markdown. No reasigna el atajo ni modifica
archivos administrados en `/usr/share/omarchy`.

## Arquitectura y protocolo

- `config/omarchy-menu.fragment.jsonc`: entrada buscable `trigger.markitdown`.
- `src/markitdown-convert`: lanzador instalado; usa `/usr/bin/python` para GTK4.
- `src/markitdown_drop.py`: ruta editable, `Gtk.FileDialog`, DnD de exactamente
  un archivo local mediante `Gdk.FileList` (con fallback `Gio.File`) y
  subprocess cancelable fuera del hilo GTK.
- `src/markitdown_backend.py`: proceso Python 3.12 aislado con MarkItDown.
- `install.sh`: edición marcada y atómica del overlay, copias con manifiesto,
  rollback transaccional y retirada selectiva. El modo predeterminado es
  `--dry-run`.

El backend emite una línea JSON por stdout. Estados: `success` (código 0),
`error` de validación (2), conversión (3) o guardado (4), `partial` por fallo de
portapapeles (5) y `cancelled` (130). Los errores de notificación se reflejan en
`notification_sent`, pero nunca eliminan un resultado guardado.

## Seguridad

Solo se aceptan archivos regulares locales con extensión permitida. Se
rechazan URI, NUL, directorios y especiales; todos los procesos externos usan
listas argv, sin shell ni `eval`. Los nombres pueden contener espacios,
Unicode, comillas, `$()`, `;`, guiones y saltos de línea.

La salida se escribe con modo privado en un temporal del directorio Downloads,
se sincroniza y publica sin reemplazo mediante `renameat2(RENAME_NOREPLACE)`.
Si el kernel no lo ofrece, `link()` en el mismo filesystem conserva la misma
garantía atómica de no reemplazo. Se prueban colisiones concurrentes. Solo tras
guardar se llama `wl-copy --type text/plain`; un fallo conserva el archivo y se
notifica como éxito parcial. Archivos mayores de 100 MiB generan advertencia.

Antes de convertir, el backend abre el origen con flags restrictivos, comprueba
el descriptor con `fstat` y crea en Downloads una instantánea privada que
conserva la extensión. MarkItDown solo recibe esa instantánea, que se elimina en
éxito, error o cancelación. La identidad dispositivo/inode validada se compara
con la del descriptor abierto, por lo que una sustitución de ruta se rechaza.
Esto evita usar `/tmp`, pero no puede impedir que otro proceso modifique concurrentemente el
mismo inode mientras se está copiando; en ese caso la instantánea podría
contener una mezcla temporal de estados del archivo.

Cancelar o cerrar envía `SIGTERM` una sola vez y la ventana espera a que el
backend termine para que sus bloques de limpieza retiren instantáneas y
temporales. No se escala automáticamente a `SIGKILL`: una conversión nativa
bloqueada puede tardar en cancelarse, trade-off deliberado para no abandonar
copias sensibles en disco.

## Dependencias y aprobaciones necesarias

El repositorio no incluye `uv.lock`: no debe afirmarse reproducibilidad cerrada
hasta generarlo tras una resolución aprobada. **Esta entrega no descarga ni
instala nada.** Antes del uso real hacen falta dos aprobaciones separadas:

1. Aprobar que `uv` obtenga Python 3.12 y `markitdown[pdf,docx,pptx,xlsx]==0.1.7`.
2. Aprobar que el instalador escriba bajo `~/.config/omarchy/`.

Tras aprobar la escritura, revisar primero:

```sh
./install.sh --dry-run
./install.sh --apply
```

Después de aprobar también las descargas, crear el runtime instalado (este
comando puede descargar Python y paquetes):

```sh
uv sync --project "$HOME/.config/omarchy/markitdown-omarchy" --python 3.12
```

Finalmente puede ejecutarse `omarchy menu refresh`, abrir `SUPER+SPACE`, buscar
“MarkItDown” y validar selector, ruta pegada, DnD, cancelación y portapapeles.
El overlay normalmente se vigila automáticamente. No se toca Hyprland ni
Waybar.

Para desinstalar, inspeccionar y luego aprobar:

```sh
./install.sh --dry-run --uninstall
./install.sh --apply --uninstall
```

Se validan todos los conflictos antes de escribir y se crean respaldos solo
antes de cambios. Si falla cualquier publicación, se restauran menú, archivos
administrados y manifiesto; los respaldos no se borran. Se aborta ante un
`trigger.markitdown` ajeno. Una desinstalación con archivos propios modificados
se cancela completa para conservar tanto esos archivos como la entrada de menú
y evitar un estado incoherente.

La desinstalación no borra directorios: pueden quedar directorios vacíos para
evitar retirar contenedores que también sean usados por terceros. Tampoco se
promete restauración byte-a-byte de directorios. Si el overlay de menú no
existía antes, se conserva el objeto vacío resultante porque la versión actual
no persiste evidencia suficiente para atribuir con seguridad el archivo entero
al proyecto.

El destino normal respeta `XDG_CONFIG_HOME` (con respaldo en `~/.config`). Para
pruebas, `--config-root RUTA` dirige todo a un sandbox y la acción del menú se
genera para la ruta efectiva del launcher, con quoting de shell y escaping JSON
seguros incluso si la ruta contiene espacios o comillas. La raíz de
configuración debe ser absoluta.

## Pruebas

Sin dependencias adicionales ni acceso a la configuración real:

```sh
python -m unittest discover -s tests -v
bash -n install.sh src/markitdown-convert
```

Las pruebas reales de contenido PDF/DOCX/PPTX/XLSX, integración GTK/Wayland,
notificación y aparición desde `SUPER+SPACE` quedan pendientes hasta aprobar el
runtime y la instalación. El selector depende de GTK 4 suficientemente reciente
para `Gtk.FileDialog` (confirmado para el Omarchy 4.0.0-1 investigado).
