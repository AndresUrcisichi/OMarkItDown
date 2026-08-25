# Skill: integración de aplicaciones con Omarchy

Usa esta skill al desarrollar o instalar MarkItDown-Omarchy. Está adaptada al
proyecto y no sustituye la consulta de documentación oficial vigente.

## Lectura requerida

- Para añadir la acción de `SUPER + SPACE`, lee `menu-extensions.md`.
- Antes de decidir si crear un plugin QML, lee `plugins.md`.
- Solo si se modifican atajos o archivos de Hyprland, lee `hyprland.md`.

## Reglas críticas

1. Nunca modifiques `/usr/share/omarchy/` ni `~/.local/share/omarchy/`.
2. Puedes leer `/usr/share/omarchy/` para conocer comandos y valores
   predeterminados actuales.
3. Las personalizaciones instaladas deben vivir bajo `~/.config/`.
4. Preserva las modificaciones existentes del usuario.
5. Usa comandos `omarchy <grupo> <acción>` cuando estén disponibles.
6. Comprueba comandos con `omarchy commands`, `omarchy <grupo> --help` y la
   documentación actual antes de asumir una interfaz.
7. Pide confirmación antes de operaciones privilegiadas, destructivas, de
   paquetes o de restablecimiento.
8. Tras cualquier cambio de Hyprland ejecuta `hyprctl reload` y después
   `hyprctl configerrors`.
9. Tras cualquier cambio de Waybar ejecuta `omarchy restart waybar`.
10. Mantén los cambios mínimos y comunica los resultados de verificación.

## Ubicaciones relevantes

```text
~/.config/omarchy/extensions/omarchy-menu.jsonc  # Extensiones del menú
~/.config/omarchy/scripts/                       # Scripts de esta integración
~/.config/omarchy/plugins/                       # Plugins QML del usuario
~/.config/hypr/                                  # Configuración de Hyprland
/usr/share/omarchy/                              # Solo lectura
```

## Descubrimiento seguro

```bash
omarchy version
omarchy commands
omarchy menu --help
omarchy plugin --help
omarchy menu keybindings --print
```

Para entender una función existente, localiza su binario y léelo sin editarlo.
El transcoder incluido por Omarchy es una referencia útil para selección de
archivos, notificaciones, nombres de salida y portapapeles, pero no debe
copiarse ciegamente ni modificarse en su ubicación administrada.

## Elección de arquitectura

1. **Acción estática del menú:** extensión JSONC + script en la configuración
   del usuario. Es la opción recomendada para el MVP.
2. **Interfaz drag-and-drop:** ventana GTK propia invocada por la acción.
3. **Plugin de shell:** úsalo solo si se necesita una superficie QML integrada
   y persistente. No conviertas una acción sencilla en un plugin sin beneficio.
4. **Atajo de Hyprland:** innecesario mientras `SUPER + SPACE` ya abra el menú.
