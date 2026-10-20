#!/bin/sh
# Inverso de usb_vinculacion_gadget_add.sh. Tiene que correr ANTES del
# "reset" original de adbd-usb-gadget (ver el drop-in
# scripts/systemd/adbd-usb-vinculacion.conf): ese reset hace rmdir del
# gadget entero (configs/c.1 y el directorio raíz del gadget), y falla si
# queda algo nuestro adentro.
#
# Ver docs/VINCULACION-USB.md.
set -e

CONFIGFS_DIR="/sys/kernel/config/usb_gadget/g1"

rm -f "${CONFIGFS_DIR}/configs/c.1/acm.GS1" 2>/dev/null || true
rmdir "${CONFIGFS_DIR}/functions/acm.GS1" 2>/dev/null || true
