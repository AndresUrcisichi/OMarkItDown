# Extensiones del menú de Omarchy

## Objetivo

Añadir una entrada de MarkItDown al menú existente sin reemplazar el menú ni
modificar archivos administrados por Omarchy.

## Fuente de verdad

Antes de editar, compara:

```text
~/.config/omarchy/extensions/omarchy-menu.jsonc
/usr/share/omarchy/default/omarchy/omarchy-menu.jsonc
```

El segundo archivo es solo de lectura. El archivo del usuario es un overlay
JSONC: los identificadores con puntos forman jerarquías y una entrada con
`action` ejecuta un comando.

## Patrón recomendado

Coloca MarkItDown dentro del submenú `Trigger`, cerca de Transcode:

```jsonc
"trigger.markitdown": {
  "icon": "󰈙",
  "label": "MarkItDown",
  "description": "Convert a local document to Markdown",
  "action": "$HOME/.config/omarchy/scripts/markitdown-convert"
}
```

Valida los campos contra el formato actual antes de usar este ejemplo. Si la
acción depende de un ejecutable opcional, se puede considerar un campo `when`,
pero la ausencia de dependencias también debe producir un mensaje útil cuando
se invoque el lanzador.

## Reglas de edición

- Lee el archivo completo del usuario antes de modificarlo.
- Añade solo la propiedad necesaria y conserva comentarios y formato.
- Evita depender de que `~` se expanda dentro de JSON; usa `$HOME` o una ruta
  absoluta controlada por el instalador.
- No introduzcas datos del archivo seleccionado directamente en una cadena de
  shell.
- No reasignes `SUPER + SPACE` si ya abre el menú.

## Verificación

Después del cambio:

1. Usa el comando oficial vigente para refrescar o recargar el menú si no se
   actualiza automáticamente.
2. Abre el menú con `SUPER + SPACE`.
3. Busca `MarkItDown` y ejecuta la entrada.
4. Comprueba cancelación, error por dependencia ausente y conversión correcta.
5. Revisa los logs del shell si la entrada no aparece o la acción no se lanza.

No reinicies Waybar: el menú actual pertenece al shell de Omarchy/Quickshell,
no a Waybar. Solo usa `omarchy restart waybar` si realmente se editó Waybar.
