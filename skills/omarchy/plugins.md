# Plugins de Omarchy para este proyecto

## Cuándo usar un plugin

Los plugins de Omarchy son adecuados para componentes del shell implementados
en QML, como widgets de barra, paneles y superficies persistentes. Para una
acción estática que abre un conversor, empieza con una extensión del menú y un
programa independiente.

Considera un plugin solo si el proyecto necesita alguna de estas funciones:

- panel QML completamente integrado;
- historial persistente visible en el shell;
- progreso o cola de conversiones dentro del shell;
- widget de barra;
- IPC propio con `omarchy-shell`.

## Comandos de descubrimiento

```bash
omarchy plugin --help
omarchy plugin list --json
omarchy plugin validate <carpeta-del-plugin>
```

Utiliza `omarchy plugin add` o `omarchy plugin clone` únicamente según la
documentación actual. No edites plugins incluidos en `/usr/share/omarchy/`.

## Ubicación de plugins del usuario

```text
~/.config/omarchy/plugins/<id-del-plugin>/
```

Cada plugin debe tener un manifiesto válido, identificador único y puntos de
entrada compatibles con la versión instalada de Omarchy. Valídalo antes de
instalarlo.

## Decisión del MVP

MarkItDown-Omarchy no debe comenzar como plugin QML. La ventana GTK propia
resuelve selector, campo de ruta y drag-and-drop con menor acoplamiento al
shell. Un plugin puede añadirse después como frontend alternativo sin cambiar
el núcleo seguro de conversión.
