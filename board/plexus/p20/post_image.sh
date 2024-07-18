#! /bin/sh

set -e

BOARD_DIR=$(dirname "$0")

$BOARD_DIR/build_disk_img.py \
  -v $BINARIES_DIR/vmlinux \
  -r $BINARIES_DIR/rootfs.ext4 \
  -c $BOARD_DIR/ccal \
  -d $BINARIES_DIR/plexus.img \
  -t $BINARIES_DIR/vmlinux.coff
