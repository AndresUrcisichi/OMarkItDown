#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
MODE=dry-run
ACTION=install
WITH_RUNTIME=0
EXPLICIT_CONFIG_ROOT=0
CONFIG_ROOT=${XDG_CONFIG_HOME:-"${HOME:?HOME no definido}/.config"}

usage() {
  cat <<'EOF'
Uso: ./install.sh [--dry-run|--apply] [--with-runtime] [--uninstall] [--config-root RUTA]
Por seguridad el modo predeterminado es --dry-run. --with-runtime aprovisiona opcionalmente el runtime bloqueado con uv; --config-root permite tests aislados.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) MODE=dry-run ;;
    --apply) MODE=apply ;;
    --uninstall) ACTION=uninstall ;;
    --with-runtime) WITH_RUNTIME=1 ;;
    --config-root)
      shift
      [ "$#" -gt 0 ] || { usage >&2; exit 2; }
      CONFIG_ROOT=$1
      EXPLICIT_CONFIG_ROOT=1
      ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

case ${MARKITDOWN_OMARCHY_TESTING:-0} in
  0|'') ;;
  1) [ "$EXPLICIT_CONFIG_ROOT" -eq 1 ] || { printf '%s\n' 'modo de test requiere --config-root' >&2; exit 2; } ;;
  *) printf '%s\n' 'MARKITDOWN_OMARCHY_TESTING inválido' >&2; exit 2 ;;
esac

case "$CONFIG_ROOT" in
  /*) ;;
  *) printf '%s\n' '--config-root/XDG_CONFIG_HOME debe ser una ruta absoluta' >&2; exit 2 ;;
esac

OMARCHY_DIR=$CONFIG_ROOT/omarchy
SCRIPTS_DIR=$OMARCHY_DIR/scripts
APP_DIR=$OMARCHY_DIR/markitdown-omarchy
MENU=$OMARCHY_DIR/extensions/omarchy-menu.jsonc
MANIFEST=$APP_DIR/install-manifest.tsv
RUNTIME_DIR=$APP_DIR/.venv
RUNTIME_OWNER=$RUNTIME_DIR/.markitdown-omarchy-runtime-owner
RUNTIME_MANIFEST=$RUNTIME_DIR/.markitdown-omarchy-runtime-manifest
LAUNCHER=$SCRIPTS_DIR/markitdown-convert
STAMP=$(date +%Y%m%d-%H%M%S)-$$
FILES='scripts/markitdown-convert|src/markitdown-convert
markitdown-omarchy/markitdown_backend.py|src/markitdown_backend.py
markitdown-omarchy/markitdown_drop.py|src/markitdown_drop.py
markitdown-omarchy/pyproject.toml|pyproject.toml
markitdown-omarchy/.python-version|.python-version
markitdown-omarchy/uv.lock|uv.lock'

hash_file() { sha256sum "$1" | cut -d ' ' -f 1; }
manifest_hash() {
  [ -f "$MANIFEST" ] || return 1
  while IFS='|' read -r saved_hash saved_path; do
    [ "$saved_path" = "$1" ] && { printf '%s\n' "$saved_hash"; return 0; }
  done < "$MANIFEST"
  return 1
}
validate_manifest() {
  [ -e "$MANIFEST" ] || return 0
  [ -f "$MANIFEST" ] || { printf 'conflicto: manifiesto no regular: %s\n' "$MANIFEST" >&2; return 1; }
  while IFS='|' read -r saved_hash saved_path; do
    case "$saved_hash" in
      *[!0-9a-f]*|'') printf 'conflicto: manifiesto inválido: %s\n' "$MANIFEST" >&2; return 1 ;;
    esac
    [ "${#saved_hash}" -eq 64 ] || { printf 'conflicto: manifiesto inválido: %s\n' "$MANIFEST" >&2; return 1; }
    case "$saved_path" in
      scripts/markitdown-convert|markitdown-omarchy/markitdown_backend.py|markitdown-omarchy/markitdown_drop.py|markitdown-omarchy/pyproject.toml|markitdown-omarchy/.python-version|markitdown-omarchy/uv.lock) ;;
      *) printf 'conflicto: ruta ajena en manifiesto: %s\n' "$saved_path" >&2; return 1 ;;
    esac
  done < "$MANIFEST"
}
backup() {
  [ -e "$1" ] || return 0
  cp -p -- "$1" "$1.bak.$STAMP"
  printf 'respaldo: %s\n' "$1.bak.$STAMP"
}
maybe_fail() {
  [ "${MARKITDOWN_OMARCHY_TESTING:-0}" = 1 ] || return 0
  [ "${MARKITDOWN_OMARCHY_TEST_FAIL:-}" != "$1" ] || {
    printf 'fallo inyectado de test: %s\n' "$1" >&2
    return 97
  }
}
runtime_listing() {
  runtime=$1
  find "$runtime" -mindepth 1 ! -path "$RUNTIME_MANIFEST" \( -type f -o -type l \) -print | LC_ALL=C sort | while IFS= read -r item; do
    relative=${item#"$runtime"/}
    if [ -L "$item" ]; then
      printf 'l|%s|%s\n' "$(readlink -- "$item" | sha256sum | cut -d ' ' -f 1)" "$relative"
    else
      printf 'f|%s|%s\n' "$(hash_file "$item")" "$relative"
    fi
  done
}
runtime_is_owned() {
  [ -e "$RUNTIME_DIR" ] || return 0
  [ -d "$RUNTIME_DIR" ] && [ ! -L "$RUNTIME_DIR" ] || { printf 'conflicto: runtime .venv ambiguo; se conserva: %s\n' "$RUNTIME_DIR" >&2; return 1; }
  [ -f "$RUNTIME_OWNER" ] && [ -f "$RUNTIME_MANIFEST" ] || { printf 'conflicto: runtime .venv sin prueba de propiedad; se conserva: %s\n' "$RUNTIME_DIR" >&2; return 1; }
  [ "$(cat "$RUNTIME_OWNER")" = 'markitdown-omarchy-runtime-v1' ] || { printf 'conflicto: marcador de runtime inválido; se conserva: %s\n' "$RUNTIME_DIR" >&2; return 1; }
  find "$RUNTIME_DIR" -mindepth 1 ! -type d ! -type f ! -type l -print | grep -q . && { printf 'conflicto: runtime .venv contiene tipo ambiguo; se conserva: %s\n' "$RUNTIME_DIR" >&2; return 1; }
  runtime_listing "$RUNTIME_DIR" | cmp -s "$RUNTIME_MANIFEST" - || { printf 'conflicto: runtime .venv modificado; se conserva: %s\n' "$RUNTIME_DIR" >&2; return 1; }
}
runtime_record_ownership() {
  printf '%s\n' 'markitdown-omarchy-runtime-v1' > "$RUNTIME_OWNER"
  chmod 0600 "$RUNTIME_OWNER"
  runtime_listing "$RUNTIME_DIR" > "$RUNTIME_MANIFEST"
  chmod 0600 "$RUNTIME_MANIFEST"
}
test_replace_before_remove() {
  [ "${MARKITDOWN_OMARCHY_TESTING:-0}" = 1 ] || return 0
  [ "${MARKITDOWN_OMARCHY_TEST_REPLACE_BEFORE_REMOVE:-}" = "$1" ] || return 0
  printf '%s\n' '# modificación concurrente inyectada' >> "$2"
}

validate_install_file() {
  relative=$1
  source=$2
  target=$OMARCHY_DIR/$relative
  [ ! -e "$target" ] && return 0
  [ -f "$target" ] || { printf 'conflicto: %s no es un archivo regular\n' "$target" >&2; return 1; }
  cmp -s -- "$PROJECT_DIR/$source" "$target" && return 0
  expected=$(manifest_hash "$relative" || true)
  [ -n "$expected" ] && [ "$expected" = "$(hash_file "$target")" ] && return 0
  printf 'conflicto: %s existe y no es una copia administrada\n' "$target" >&2
  return 1
}

validate_manifest
menu_failed=0
if [ "$ACTION" = install ]; then
  validation_failed=0
  printf '%s\n' "$FILES" | (
    failed=0
    while IFS='|' read -r relative source; do
      validate_install_file "$relative" "$source" || failed=1
    done
    exit "$failed"
  ) || validation_failed=1
  /usr/bin/python "$PROJECT_DIR/src/menu_patch.py" add "$MENU" --action-path "$LAUNCHER" --dry-run || menu_failed=1
  [ "$validation_failed" -eq 0 ] && [ "$menu_failed" -eq 0 ] || exit 3
  if [ "$WITH_RUNTIME" -eq 1 ] && [ -e "$RUNTIME_DIR" ]; then
    runtime_is_owned || exit 3
  fi
else
  uninstall_conflict=0
  if [ -f "$MANIFEST" ]; then
    printf '%s\n' "$FILES" | (
      failed=0
      while IFS='|' read -r relative source; do
        target=$OMARCHY_DIR/$relative
        expected=$(manifest_hash "$relative" || true)
        if [ -e "$target" ] && { [ -z "$expected" ] || [ ! -f "$target" ] || [ "$(hash_file "$target")" != "$expected" ]; }; then
          printf 'conflicto de desinstalación; se conservaría: %s\n' "$target" >&2
          failed=1
        fi
      done
      exit "$failed"
    ) || uninstall_conflict=1
  fi
  /usr/bin/python "$PROJECT_DIR/src/menu_patch.py" remove "$MENU" --action-path "$LAUNCHER" --dry-run || menu_failed=1
  if [ "$uninstall_conflict" -ne 0 ]; then
    printf '%s\n' 'menú: se conservaría para evitar una desinstalación incoherente' >&2
    exit 3
  fi
  [ "$menu_failed" -eq 0 ] || exit 3
  if [ -e "$RUNTIME_DIR" ]; then
    runtime_is_owned || exit 3
  fi
fi

if [ "$ACTION" = install ] && [ "$WITH_RUNTIME" -eq 1 ] && [ "$MODE" = apply ]; then
  command -v uv >/dev/null 2>&1 || { printf '%s\n' 'uv no está disponible; no se instaló ningún archivo' >&2; exit 4; }
fi

if [ "$MODE" = dry-run ]; then
  if [ "$ACTION" = install ]; then
    printf '%s\n' "$FILES" | while IFS='|' read -r relative source; do
      target=$OMARCHY_DIR/$relative
      if [ -f "$target" ] && cmp -s -- "$PROJECT_DIR/$source" "$target"; then
        printf 'sin cambios: %s\n' "$target"
      elif [ -e "$target" ]; then
        printf 'se respaldaría y actualizaría: %s\n' "$target"
      else
        printf 'se instalaría: %s\n' "$target"
      fi
    done
    printf 'se publicaría manifiesto: %s\n' "$MANIFEST"
    if [ "$WITH_RUNTIME" -eq 1 ]; then
      printf 'runtime opcional: uv sync --project %s --python 3.12 --locked; usa el proyecto instalado, Python 3.12 y el lock actual; pueden ocurrir descargas\n' "$APP_DIR"
    else
      printf '%s\n' 'no se descargará ni instalará el runtime uv'
    fi
    exit 0
  fi

  if [ -f "$MANIFEST" ]; then
    printf '%s\n' "$FILES" | while IFS='|' read -r relative source; do
      target=$OMARCHY_DIR/$relative
      expected=$(manifest_hash "$relative" || true)
      if [ ! -e "$target" ]; then
        printf 'ya ausente: %s\n' "$target"
      elif [ -n "$expected" ] && [ -f "$target" ] && [ "$(hash_file "$target")" = "$expected" ]; then
        printf 'se retiraría: %s\n' "$target"
      else
        printf 'se conservaría: %s\n' "$target"
      fi
    done
  else
    printf 'sin manifiesto: no se retirarían archivos de aplicación\n'
  fi
  printf 'se retiraría el manifiesto: %s\n' "$MANIFEST"
  exit 0
fi

# El rollback guarda copias privadas dentro del sandbox/configuración, nunca en /tmp.
mkdir -p -- "$CONFIG_ROOT"
TXN=$(mktemp -d "$CONFIG_ROOT/.markitdown-omarchy.transaction.XXXXXX")
chmod 0700 "$TXN"
TRANSACTION_ACTIVE=0
capture() {
  slot=$1
  path=$2
  if [ -e "$path" ]; then
    [ -f "$path" ] || { printf 'conflicto: %s no es regular\n' "$path" >&2; return 1; }
    cp -p -- "$path" "$TXN/$slot"
    : > "$TXN/$slot.exists"
  fi
}
restore() {
  slot=$1
  path=$2
  [ ! -e "$TXN/$slot.preserve" ] || return 0
  if [ -e "$TXN/$slot.exists" ]; then
    restore_directory=$(dirname -- "$path")
    mkdir -p -- "$restore_directory"
    restore_temporary=$(mktemp "$restore_directory/.markitdown-restore.XXXXXX")
    if cp -p -- "$TXN/$slot" "$restore_temporary"; then
      mv -f -- "$restore_temporary" "$path"
    else
      rm -f -- "$restore_temporary"
      return 1
    fi
  else
    rm -f -- "$path"
  fi
}
cleanup_capture() { rm -rf -- "$TXN"; }
rollback() {
  [ "$TRANSACTION_ACTIVE" -eq 1 ] || return 0
  TRANSACTION_ACTIVE=0
  trap - EXIT HUP INT TERM
  restore 0 "$MENU" || true
  restore 1 "$OMARCHY_DIR/scripts/markitdown-convert" || true
  restore 2 "$OMARCHY_DIR/markitdown-omarchy/markitdown_backend.py" || true
  restore 3 "$OMARCHY_DIR/markitdown-omarchy/markitdown_drop.py" || true
  restore 4 "$OMARCHY_DIR/markitdown-omarchy/pyproject.toml" || true
  restore 5 "$OMARCHY_DIR/markitdown-omarchy/.python-version" || true
  restore 6 "$OMARCHY_DIR/markitdown-omarchy/uv.lock" || true
  restore 7 "$MANIFEST" || true
  rm -rf -- "$TXN"
  printf '%s\n' 'operación fallida; se revirtieron los archivos capturados y se conservaron cambios concurrentes detectados' >&2
}
trap cleanup_capture EXIT HUP INT TERM
capture 0 "$MENU"
capture 1 "$OMARCHY_DIR/scripts/markitdown-convert"
capture 2 "$OMARCHY_DIR/markitdown-omarchy/markitdown_backend.py"
capture 3 "$OMARCHY_DIR/markitdown-omarchy/markitdown_drop.py"
capture 4 "$OMARCHY_DIR/markitdown-omarchy/pyproject.toml"
capture 5 "$OMARCHY_DIR/markitdown-omarchy/.python-version"
capture 6 "$OMARCHY_DIR/markitdown-omarchy/uv.lock"
capture 7 "$MANIFEST"
TRANSACTION_ACTIVE=1
trap rollback EXIT HUP INT TERM

if [ "$ACTION" = install ]; then
  NEW_MANIFEST=$TXN/new-manifest
  : > "$NEW_MANIFEST"
  chmod 0600 "$NEW_MANIFEST"
  file_number=0
  printf '%s\n' "$FILES" | while IFS='|' read -r relative source; do
    target=$OMARCHY_DIR/$relative
    src=$PROJECT_DIR/$source
    if [ -e "$target" ] && ! cmp -s -- "$src" "$target"; then
      backup "$target"
    fi
    directory=$(dirname -- "$target")
    mkdir -p -- "$directory"
    temporary=$(mktemp "$directory/.markitdown-install.XXXXXX")
    publish_failed=0
    cp -p -- "$src" "$temporary" || publish_failed=1
    if [ "$publish_failed" -eq 0 ]; then
      case "$relative" in scripts/*|*.py) chmod 0755 "$temporary" || publish_failed=1 ;; *) chmod 0644 "$temporary" || publish_failed=1 ;; esac
    fi
    [ "$publish_failed" -ne 0 ] || mv -f -- "$temporary" "$target" || publish_failed=1
    if [ "$publish_failed" -ne 0 ]; then
      rm -f -- "$temporary"
      exit 1
    fi
    printf '%s|%s\n' "$(hash_file "$target")" "$relative" >> "$NEW_MANIFEST"
    printf 'instalado: %s\n' "$target"
    file_number=$((file_number + 1))
    maybe_fail "after-file-$file_number"
  done
  maybe_fail after-files
  mkdir -p -- "$(dirname -- "$MENU")"
  /usr/bin/python "$PROJECT_DIR/src/menu_patch.py" add "$MENU" --action-path "$LAUNCHER" --backup-suffix ".bak.$STAMP"
  maybe_fail after-menu
  mkdir -p -- "$APP_DIR"
  mv -f -- "$NEW_MANIFEST" "$MANIFEST"
  maybe_fail after-manifest
  if [ "$WITH_RUNTIME" -eq 1 ]; then
    if [ -e "$RUNTIME_DIR" ]; then
      printf '%s\n' 'Runtime .venv administrado ya está presente; se conserva sin volver a sincronizar.'
    else
      if uv sync --project "$APP_DIR" --python 3.12 --locked; then
        runtime_record_ownership
        printf 'Runtime .venv aprovisionado con uv y lock actual: %s\n' "$RUNTIME_DIR"
      else
        status=$?
        printf 'uv sync falló; cualquier .venv parcial se conserva por seguridad: %s\n' "$RUNTIME_DIR" >&2
        exit "$status"
      fi
    fi
  else
    printf '%s\n' 'Instalación de archivos completa. El runtime .venv aún requiere aprobación y uv.'
  fi
else
  maybe_fail before-uninstall
  /usr/bin/python "$PROJECT_DIR/src/menu_patch.py" remove "$MENU" --action-path "$LAUNCHER" --backup-suffix ".bak.$STAMP"
  maybe_fail after-menu
  if [ -f "$MANIFEST" ]; then
    file_number=0
    printf '%s\n' "$FILES" | while IFS='|' read -r relative source; do
      file_number=$((file_number + 1))
      target=$OMARCHY_DIR/$relative
      expected=$(manifest_hash "$relative" || true)
      if [ -n "$expected" ] && [ -f "$target" ]; then
        test_replace_before_remove "$file_number" "$target"
        actual=$(hash_file "$target")
        if [ "$actual" != "$expected" ]; then
          : > "$TXN/$file_number.preserve"
          printf 'conflicto de desinstalación en el momento de retirar; se conserva: %s\n' "$target" >&2
          exit 3
        fi
        rm -- "$target"
        printf 'retirado: %s\n' "$target"
      fi
    done
    current_manifest_hash=$(hash_file "$MANIFEST")
    captured_manifest_hash=$(hash_file "$TXN/7")
    if [ "$current_manifest_hash" != "$captured_manifest_hash" ]; then
      : > "$TXN/7.preserve"
      printf 'conflicto de desinstalación en el manifiesto; se conserva: %s\n' "$MANIFEST" >&2
      exit 3
    fi
    rm -- "$MANIFEST"
  fi
  maybe_fail after-files
  if [ -e "$RUNTIME_DIR" ]; then
    runtime_is_owned || exit 3
    rm -rf -- "$RUNTIME_DIR"
    printf 'runtime retirado: %s\n' "$RUNTIME_DIR"
  fi
  printf '%s\n' 'Desinstalación selectiva completa; pueden quedar directorios vacíos.'
fi

TRANSACTION_ACTIVE=0
trap - EXIT HUP INT TERM
rm -rf -- "$TXN"
