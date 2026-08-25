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
  `--dry-run`; `--with-runtime` es una provisión opcional del entorno aislado.

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
notifica como éxito parcial. Archivos mayores de 100 MiB requieren confirmación explícita en la ventana antes de iniciar el backend; al rechazarla no se crea salida.

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
temporales. Tras 15 segundos la ventana habilita una acción explícita de «Forzar detención»; nunca se escala automáticamente a `SIGKILL` y dicha acción solo señala el PID del backend propiedad de la ventana. Forzar puede impedir su limpieza de instantáneas temporales, por lo que la interfaz lo advierte y pide revisar Downloads antes de reintentar.

## Dependencias y aprobaciones necesarias

El repositorio incluye `uv.lock`, que se copia junto con el proyecto instalado.
La instalación sigue siendo solo de configuración por defecto y no requiere que
`uv` esté disponible:

```sh
./install.sh --dry-run
./install.sh --apply
```

Para aprobar explícitamente la provisión del runtime aislado, revisar primero el
plan y después aplicarlo:

```sh
./install.sh --dry-run --with-runtime
./install.sh --apply --with-runtime
```

El plan indica la ruta instalada exacta, Python 3.12, el lock actual y que puede
haber descargas, sin ejecutar nada. Al aplicar, el instalador comprueba `uv`
antes de escribir archivos y usa exactamente `uv sync --project RUTA --python
3.12 --locked`. No usa `pip`, Python global ni habilita plugins de terceros de
MarkItDown. `--locked` impide que uv vuelva a resolver o modifique el lock. Si
falta `uv`, aborta antes de cualquier publicación; si `uv sync` falla, se revierte
la configuración transaccional y se conserva cualquier `.venv` parcial para no
borrar contenido de procedencia incierta.

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
evitar retirar contenedores que también sean usados por terceros. La excepción
conservadora es una `.venv` creada por `--with-runtime`: el instalador guarda en
ella un marcador de propiedad y un manifiesto con hashes de cada archivo regular
y enlace simbólico. Solo la elimina si el directorio no es un enlace, los dos
metadatos son válidos, no contiene tipos especiales y su listado actual coincide
exactamente con el manifiesto. Un archivo añadido, modificado, ambiguo o un
runtime parcial sin manifiesto bloquea toda la desinstalación y se conserva; no
se hace borrado recursivo de datos inciertos. Tampoco se promete restauración
byte-a-byte de directorios. Si el overlay de menú no existía antes, se conserva
el objeto vacío resultante porque la versión actual no persiste evidencia
suficiente para atribuir con seguridad el archivo entero al proyecto.

El destino normal respeta `XDG_CONFIG_HOME` (con respaldo en `~/.config`). Para
pruebas, `--config-root RUTA` dirige todo a un sandbox y la acción del menú se
genera para la ruta efectiva del launcher, con quoting de shell y escaping JSON
seguros incluso si la ruta contiene espacios o comillas. La raíz de
configuración debe ser absoluta.

## Pruebas

Las comprobaciones unitarias rápidas no requieren MarkItDown; las pruebas de
conversión real se omiten con un motivo explícito si se usa un Python sin esa
dependencia:

```sh
python -m unittest discover -s tests -v
bash -n install.sh src/markitdown-convert
```

Tras aprobar y preparar el runtime bloqueado local, ejecutar la cobertura de
integración real (sin descargar ni sincronizar dependencias):

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Esta última genera temporalmente documentos PDF, DOCX, PPTX y XLSX y comprueba
la ruta real de MarkItDown, publicación, portapapeles simulado, limpieza y una
colisión `-2`. No cubre integración GTK/Wayland, notificación de escritorio ni
aparición desde `SUPER+SPACE`. El selector depende de GTK 4 suficientemente
reciente para `Gtk.FileDialog` (confirmado para el Omarchy 4.0.0-1 investigado).
