# MarkItDown-Omarchy — instrucciones para agentes

## Misión

Construir una integración segura y mantenible entre Microsoft MarkItDown y
Omarchy. El flujo objetivo es:

1. El usuario pulsa `SUPER + SPACE` y abre el menú de Omarchy.
2. Selecciona `MarkItDown`.
3. Puede escribir o pegar una ruta local, elegir un archivo o arrastrarlo a
   una zona de recepción.
4. El documento se convierte a Markdown.
5. El resultado se guarda en `~/Downloads` sin sobrescribir archivos.
6. El contenido Markdown se copia al portapapeles.
7. Una notificación informa del éxito o explica el error.

## Instrucciones obligatorias

- Lee primero `skills/omarchy/SKILL.md` y las referencias que correspondan.
- Verifica la versión y la documentación vigente de Omarchy, MarkItDown,
  Quickshell y Hyprland antes de utilizar interfaces sensibles a versiones.
- Prefiere comandos públicos `omarchy <grupo> <acción>` cuando existan.
- Nunca edites `/usr/share/omarchy/` ni `~/.local/share/omarchy/`; solo pueden
  leerse como referencia.
- Durante el desarrollo, mantén el código fuente dentro de este repositorio.
- La instalación para el usuario solo puede escribir configuración en
  `~/.config/`, especialmente:
  - `~/.config/omarchy/extensions/omarchy-menu.jsonc`
  - `~/.config/omarchy/scripts/`
  - `~/.config/omarchy/plugins/`, únicamente si se adopta un plugin QML real.
- Inspecciona y conserva las personalizaciones existentes. No reemplaces
  archivos completos cuando baste con añadir o modificar una entrada.
- Antes de instalar paquetes, descargar runtimes, usar privilegios, eliminar
  datos, restablecer configuración o sobrescribir archivos, explica el impacto
  y solicita confirmación explícita.
- No añadas telemetría ni servicios en segundo plano.
- No incluyas claves, tokens ni endpoints privados en el repositorio.

## Decisiones arquitectónicas iniciales

### Integración con el menú

La primera versión debe usar una extensión JSONC del menú y un lanzador local.
No debe reasignar `SUPER + SPACE`: Omarchy ya abre su menú con esa combinación.

Entrada conceptual:

```jsonc
"trigger.markitdown": {
  "icon": "󰈙",
  "label": "MarkItDown",
  "action": "$HOME/.config/omarchy/scripts/markitdown-convert"
}
```

Trata este fragmento como ejemplo: antes de implementarlo, comprueba el formato
actual del archivo predeterminado de Omarchy y del archivo del usuario.

### Conversión

- Usa el paquete oficial `markitdown` de Microsoft.
- Aísla sus dependencias con `uv`; no instales con `pip` en el Python global.
- Prefiere una versión de Python soportada oficialmente por la versión actual
  de MarkItDown.
- Para documentos locales utiliza la API más restrictiva disponible,
  preferentemente `convert_local()`, en lugar de la API permisiva `convert()`.
- La primera versión debe admitir, como mínimo, PDF, DOCX, PPTX y XLSX.
- No habilites plugins de terceros de MarkItDown de forma predeterminada.
- URLs y servicios Azure quedan fuera del MVP. Si se añaden, deben ser un flujo
  separado y explícito.

### Interfaz

La interfaz inicial puede implementarse con GTK/PyGObject y debe ofrecer:

- campo de ruta local;
- selector de archivo;
- recepción drag-and-drop de un solo archivo;
- estado de conversión y errores comprensibles;
- cancelación sin crear archivos de salida.

No presupongas que el menú genérico de Omarchy acepta drag-and-drop. Si se
necesita esa función, impleméntala en la ventana GTK propia sin modificar el
plugin de menú incluido por Omarchy.

### Salida y portapapeles

- Resuelve `~/Downloads` mediante la configuración XDG cuando sea posible y
  usa `~/Downloads` como respaldo.
- Conserva un nombre reconocible: `<nombre-original>.md`.
- Nunca sobrescribas silenciosamente. Genera `-2`, `-3`, etc.
- Escribe primero en un archivo temporal y publica el resultado con una
  operación atómica solo después de una conversión correcta.
- Copia por defecto el contenido Markdown como `text/plain` usando una
  herramienta Wayland compatible, actualmente `wl-copy`.
- No anuncies éxito si falla el guardado o el portapapeles. Si solo falla el
  portapapeles, conserva el archivo y notifícalo como éxito parcial.
- Usa las notificaciones de Omarchy cuando exista un comando público adecuado.

## Seguridad

- Acepta únicamente archivos locales regulares en el MVP.
- Normaliza y valida la ruta; rechaza directorios, rutas inexistentes, URI
  remotas y entradas con bytes nulos.
- Pasa argumentos como listas; no construyas comandos mediante interpolación
  de shell con datos suministrados por el usuario.
- No uses `eval`.
- Admite espacios, comillas, Unicode y saltos de línea en nombres de archivo.
- Considera límites razonables de tamaño y presenta una advertencia para
  documentos excepcionalmente grandes.
- Evita dejar documentos o Markdown sensible en `/tmp`; usa archivos privados
  con permisos restrictivos y limpia los temporales.

## Organización sugerida del repositorio

```text
MarkItDown-Omarchy/
├── AGENTS.md
├── README.md
├── skills/
│   └── omarchy/
├── src/
│   ├── markitdown-convert
│   └── markitdown_drop.py
├── config/
│   └── omarchy-menu.fragment.jsonc
├── tests/
└── install.sh
```

El instalador debe ser idempotente, mostrar qué modificará, crear respaldos de
los archivos existentes y disponer de una ruta de desinstalación que elimine
solo contenido perteneciente al proyecto.

## Flujo de trabajo

1. Inspecciona el sistema y la configuración existente sin modificarla.
2. Consulta documentación oficial actual.
3. Diseña el cambio mínimo y explica cualquier dependencia nueva.
4. Solicita confirmación antes de instalar dependencias o ejecutar el
   instalador contra la configuración real del usuario.
5. Implementa primero en este repositorio.
6. Ejecuta pruebas automatizadas y pruebas con nombres de archivo difíciles.
7. Instala en `~/.config/` únicamente después de la aprobación del usuario.
8. Si cambia configuración de Hyprland, ejecuta obligatoriamente:
   - `hyprctl reload`
   - `hyprctl configerrors`
9. Si cambia Waybar, ejecuta `omarchy restart waybar`. Este proyecto no debería
   requerir cambios de Waybar.
10. Para cambios del shell o menú, usa el mecanismo oficial de recarga vigente
    y verifica que la entrada pueda abrirse desde `SUPER + SPACE`.

## Criterios de aceptación del MVP

- La entrada `MarkItDown` aparece en el menú abierto con `SUPER + SPACE`.
- Se puede seleccionar, escribir/pegar o arrastrar un archivo local.
- PDF, DOCX, PPTX y XLSX producen un `.md` válido en Descargas.
- El resultado no sobrescribe archivos existentes.
- El contenido queda en el portapapeles Wayland.
- Cancelaciones y errores no dejan salidas vacías.
- El usuario recibe una notificación clara.
- Rutas con espacios, Unicode y caracteres de shell funcionan correctamente.
- La instalación y desinstalación conservan toda configuración ajena.
- No se modifica contenido administrado por Omarchy.

## Estado del entorno observado al iniciar el proyecto

Comprobado el 2026-08-20; vuelve a verificar antes de depender de estos datos:

- Omarchy: `4.0.0-1`.
- `SUPER + SPACE`: menú principal de Omarchy.
- MarkItDown no estaba instalado.
- `uv`, `zenity` y `wl-copy` estaban disponibles.
- PyPI publicaba MarkItDown `0.1.7`, con Python `>=3.10` y clasificadores hasta
  Python 3.13.
- El Python global era 3.14; por ello se recomendó un entorno aislado con una
  versión de Python soportada en lugar de usar el Python global.
