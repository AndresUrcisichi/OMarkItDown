# Hyprland: instrucciones condicionales

Este proyecto no debería necesitar cambios de Hyprland porque Omarchy ya abre
el menú con `SUPER + SPACE`. Lee y aplica esta referencia solo si el alcance
cambia y se solicita un atajo adicional o una regla de ventana.

## Configuración del usuario

```text
~/.config/hypr/bindings.lua
~/.config/hypr/hyprland.lua
```

Nunca edites los valores predeterminados administrados por Omarchy.

## Atajos

Antes de añadir o reemplazar un atajo:

```bash
omarchy menu keybindings --print
```

Si la combinación ya existe, llama primero al mecanismo actual de `unbind` y
documenta qué función se sustituyó. Usa la sintaxis Lua vigente de Omarchy,
verificada contra sus archivos predeterminados de solo lectura.

## Reglas de ventana

La sintaxis cambia con frecuencia. Consulta siempre la documentación oficial
actual de Hyprland antes de escribir una regla:

https://wiki.hypr.land/Configuring/Basics/Window-Rules/

Prefiere los helpers actuales de Omarchy cuando estén disponibles.

## Verificación obligatoria

Después de cualquier modificación de configuración Hyprland:

```bash
hyprctl reload
hyprctl configerrors
```

No declares terminado el cambio mientras `hyprctl configerrors` muestre errores
causados por el proyecto.
