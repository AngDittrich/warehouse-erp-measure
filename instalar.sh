#!/usr/bin/env bash
# Deja que el usuario de esta PC abra un adaptador USB-serial desde el navegador.
# Se corre una vez. No instala programas y no necesita red.

set -euo pipefail

USUARIO="${SUDO_USER:-${USER}}"
REGLA_DESTINO="/etc/udev/rules.d/99-puerto-serial-web.rules"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --usuario)
      USUARIO="${2:-}"
      shift 2
      ;;
    *)
      echo "Uso: $0 [--usuario NOMBRE]" >&2
      exit 1
      ;;
  esac
done

if [[ -z "${USUARIO}" || "${USUARIO}" == "root" ]]; then
  echo "Indica el usuario de esta PC: $0 --usuario NOMBRE" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  exec sudo -- "$0" --usuario "${USUARIO}"
fi

if ! id "${USUARIO}" >/dev/null 2>&1; then
  echo "No existe el usuario ${USUARIO}" >&2
  exit 1
fi

if ! getent group dialout >/dev/null; then
  echo "Esta PC no tiene el grupo dialout." >&2
  exit 1
fi

echo "Usuario: ${USUARIO}"
usermod -aG dialout "${USUARIO}"
echo "Grupos: $(id -nG "${USUARIO}")"

if systemctl list-unit-files ModemManager.service >/dev/null 2>&1; then
  systemctl stop ModemManager.service || true
  echo "ModemManager detenido en esta sesión. Seguirá ignorando el adaptador al reiniciar."
fi

cat >"${REGLA_DESTINO}" <<'EOF'
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6015", GROUP="dialout", MODE="0660", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6014", GROUP="dialout", MODE="0660", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6010", GROUP="dialout", MODE="0660", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", GROUP="dialout", MODE="0660", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", GROUP="dialout", MODE="0660", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="tty", ATTRS{idVendor}=="067b", ATTRS{idProduct}=="2303", GROUP="dialout", MODE="0660", ENV{ID_MM_DEVICE_IGNORE}="1"
EOF
chmod 0644 "${REGLA_DESTINO}"
udevadm control --reload-rules
udevadm trigger --subsystem-match=tty || true
udevadm settle || true
echo "Regla instalada en ${REGLA_DESTINO}"

shopt -s nullglob
PUERTOS=(/dev/ttyUSB* /dev/ttyACM*)
if [[ ${#PUERTOS[@]} -eq 0 ]]; then
  echo "No hay adaptador USB conectado ahora. Enchúfalo después de cerrar sesión."
else
  for puerto in "${PUERTOS[@]}"; do
    chmod a+rw "${puerto}" || true
    echo "${puerto}: $(ls -l "${puerto}")"
  done
  echo "Ese permiso extra se pierde al desenchufar. Tras cerrar sesión ya no hace falta."
fi

echo
echo "Falta cerrar la sesión de ${USUARIO} por completo, o reiniciar."
echo "Al volver, groups debe mostrar dialout."
