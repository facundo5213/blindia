#!/bin/sh
# Agrega un segundo puerto CDC-ACM (acm.GS1) al gadget USB compuesto que ya
# arma adbd-usb-gadget (script de Arduino, fuera de este repo) -- sin editar
# ese script. Se engancha vía un drop-in de systemd
# (scripts/systemd/adbd-usb-vinculacion.conf, instalado en
# /etc/systemd/system/adbd.service.d/) que lo corre como ExecStartPost,
# intercalado ANTES del "activate" original.
#
# Por qué el orden importa: "activate" bindea el UDC (lo que hace que el
# gadget compuesto aparezca en el bus USB para el host). Una vez bindeado,
# Linux no deja agregar funciones nuevas al gadget en caliente -- así que
# esto tiene que correr DESPUÉS del "setup" (que ya creó
# configs/c.1 y strings/0x409) pero ANTES del "activate".
#
# Ver docs/VINCULACION-USB.md para la especificación completa y cómo revertir.
set -e

CONFIGFS_DIR="/sys/kernel/config/usb_gadget/g1"

# Si "setup" no corrió (no hay UDC disponible, ej. sin cable conectado),
# no hay nada que agregar -- mismo criterio que usa adbd-usb-gadget.
[ -d "${CONFIGFS_DIR}/configs/c.1" ] || exit 0

mkdir -p "${CONFIGFS_DIR}/functions/acm.GS1"
ln -sf "${CONFIGFS_DIR}/functions/acm.GS1" "${CONFIGFS_DIR}/configs/c.1/acm.GS1"
