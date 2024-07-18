#! /usr/bin/env python3
import argparse
import binascii
import os.path
import shutil
import subprocess
import struct
import sys
import tempfile
import uuid

gpt_lba = 8

max_kernel_blocks = 4 * 1024

inode_table_block = 0x2
dirent_block = 0x21
indirect_start_block = 0x22
kernel_index_blocks = 1 + ((max_kernel_blocks - 10) + 0xff) >> 8;
ccal_index_block = indirect_start_block + kernel_index_blocks
ccal_start_block = ccal_index_block + 1

data_start_block = 0x400

ptg_kernel = uuid.UUID('dbb396ba-c12b-4d20-a92e-b7845152f1cb')
ptg_swap = uuid.UUID("0657FD6D-A4AB-43C4-84E5-0933C84B4F4F")
ptg_rootfs = uuid.UUID("0FC63DAF-8483-4772-8E79-3D69D8477DE4")

def parse_cmdline():
    global args
    ap = argparse.ArgumentParser(description="LED cube demo program")
    ap.add_argument('-v', '--vmlinux', type=str,
            default="output/images/vmlinux",
            help="vmlinux ELF binary (in)")
    ap.add_argument('-r', '--rootfs', type=str,
            default="output/images/rootfs.ext4",
            help="tape image (in)")
    ap.add_argument('-c', '--ccal', type=str,
            help="ccal binary (in)")
    ap.add_argument('-d', '--disk', type=str,
            default="output/images/plexus.img",
            help="disk image filename (out)")
    ap.add_argument('-t', '--tape', type=str,
            default="output/images/vmlinux.coff",
            help="tape image filename (out)")
    ap.add_argument('-s', '--swap', type=int, default=4,
            help="swap partition size (MB)")
    args = ap.parse_args()
    if args.rootfs is None and args.disk is not None:
        raise Exception("--disk requires --rootfs")

def addrtab(addrs):
    return b"".join(
        bytes([x >> 16, (x >> 8) & 0xff, x & 0xff])
        for x in addrs)

def inode(mode, filesize, data_block, index_block):
    addr = [0] * 13
    for n in range(10):
        if filesize > n * 0x400:
            addr[n] = data_block + n
    if filesize > 0x400 * 10:
        addr[10] = index_block
    if filesize > 0x400 * (10 + 0x100):
        addr[11] = index_block + 1
    if filesize > 0x400 * (10 + 0x100 + 0x10000):
        raise Exception("triple indirect blocks not implemented (file too big)")

    return struct.pack(
        ">HHHHI40sIII",
        mode,
        0, #link
        0, #uid
        0, #gid
        filesize,
        addrtab(addr),
        0, 0, 0, # access times
        )

def svr2_dent(inode, name):
    return struct.pack(
            ">H14s",
            inode, # d_ino
            name.encode(), # d_name
            );

def gpt_header():
    partition_crc = binascii.crc32(gpt_partitions())
    def build_header(crc):
        return struct.pack(
                "<8s4sIIIQQQQ16sQIII420s",
                b"EFI PART",
                bytes([0, 0, 1, 0]),
                0x5c, # Header size
                crc, # Header CRC
                0, # resrved
                1, # primary header location
                0x9fff, # backup header location
                data_start_block * 2, # First usable LBA
                rootfs_end_lba - 1, # Last usable LBA
                disk_uuid.bytes_le,
                gpt_lba, # LBA of partition entries - we need to leave room for the
                    # bootloader inode tables
                128, # Number of partition entries
                0x80, # Parittion table entry size
                partition_crc, # Partition array CRC
                b"", # pad to 512 bytes
                )
    return build_header(binascii.crc32(build_header(0)[:0x5c]))

def gpt_partitions():
    def gpt_partentry(pt, partuuid, start, end, flags, name):
        return struct.pack(
                "<16s16sQQQ72s",
                pt.bytes_le,
                partuuid.bytes_le,
                start,
                end,
                flags,
                name.encode("UTF-16LE")
                )
    swap_start = (data_start_block + max_kernel_blocks) * 2
    kernel = gpt_partentry(
            ptg_kernel, kp_uuid,
            data_start_block * 2, swap_start - 1,
            0, "kernel")
    swap = gpt_partentry(
            ptg_swap, sp_uuid,
            swap_start, rootfs_start_lba - 1,
            0, "root")
    rootfs = gpt_partentry(
            ptg_rootfs, rp_uuid,
            rootfs_start_lba, rootfs_end_lba - 1,
            0, "root")

    return b"".join([
        kernel,
        swap,
        rootfs,
        bytes(0x80 * (128 - 3)),
        ])

def write_indirect_block(f, file_block, nblocks = 0x100):
    for n in range(0x100):
        f.write(struct.pack(">I", file_block + n if n < nblocks else 0))

def write_indirect2_blocks(f, start, file_block, nblocks):
    nib = (nblocks + 0xff) >> 8
    write_indirect_block(f, start + 1, nib)
    for _ in range(nib):
        write_indirect_block(f, file_block);
        file_block += 0x100

def block_pos(n):
    return n * 0x400

def crosstool(name):
    cross_name = f"m68k-plexus-linux-musl-{name}"
    path = shutil.which(cross_name)
    if path is None:
        path = "output/host/bin/" + cross_name
        if not os.path.exists(path):
            path = cross_name;
    return path

def build_svr2img():
    """Create an SVR2 COFF binary

    Or at least close enough that the bootrom can load it
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        bin_filename = tmpdir + "/vmlinux.bin"
        subprocess.run([crosstool("objcopy"), "-O", "binary", args.vmlinux, bin_filename], check=True)
        p = subprocess.run([crosstool("nm"), args.vmlinux], check=True, capture_output=True)
        for l in p.stdout.decode().splitlines():
            ar = l.split()
            addr = int(ar[0], 16)
            if ar[2] == "_end":
                addr_end = addr
            elif ar[2] == "_stext":
                addr_stext = addr
            elif ar[2] == "__stop_fixup":
                addr_edata = addr
            elif ar[2] == "_start":
                addr_start = addr

        # print(f"stext={addr_stext:x}, start={addr_start:x}, edata={addr_edata:x}, end={addr_end:x}")
        with open(bin_filename, "rb") as f:
            bin_data = f.read()

    if len(bin_data) != addr_edata - addr_stext:
        raise Exception("image size mismatch")

    tsize = addr_edata & ~0xfff;
    dsize = addr_edata & 0xfff;
    bsize = addr_end - addr_edata

    return b"".join([
        struct.pack(">IIIIIIII", 0x108, tsize, dsize, bsize, 0, 0, 0, addr_start),
        bytes(addr_stext),
        bin_data,
        bytes(0x400 - ((addr_edata + 32) & 0x3ff)),
        ])

def load_ccal():
    if args.ccal is None:
        return None
    with open(args.ccal, "rb") as f:
        return f.read()

def write_diskimg(svr2img):
    """Create a bootable disk image

    Contains both a GPT partition table for linux and enough of a
    SVR2 UFS filesystem for the bootrom to load the kenrel
    """
    global disk_uuid, kp_uuid, sp_uuid, rp_uuid
    global rootfs_size, rootfs_start_lba, rootfs_end_lba

    rootfs_size = os.stat(args.rootfs).st_size
    rootfs_size = (rootfs_size + 0xfff) & ~0xfff
    rootfs_start_lba = (data_start_block + max_kernel_blocks) * 2 \
        + ((args.swap * 1024 * 1024) >> 9)
    rootfs_end_lba = rootfs_start_lba + (rootfs_size >> 9)

    ccal = load_ccal()
    try:
        with open(args.disk, "rb") as f:
            # Preserve the disk/partition GUIDs
            f.seek(0x238)
            disk_uuid = uuid.UUID(bytes_le=f.read(16))
            f.seek(gpt_lba * 0x200 + 0x10)
            kp_uuid = uuid.UUID(bytes_le=f.read(16))
            f.seek(gpt_lba * 0x200 + 0x90)
            sp_uuid = uuid.UUID(bytes_le=f.read(16))
            f.seek(gpt_lba * 0x200 + 0x110)
            rp_uuid = uuid.UUID(bytes_le=f.read(16))
    except:
        disk_uuid = uuid.uuid4()
        kp_uuid = uuid.uuid4()
        sp_uuid = uuid.uuid4()
        rp_uuid = uuid.uuid4()

    with open(args.disk, "wb") as f:
        # disk type
        f.seek(0x1a)
        f.write(b"\x70\x64")
        # kernel path
        f.seek(0x20)
        f.write(b"/linux\0")
        f.seek(0x190)
        f.write(struct.pack(
            ">HHI6s",
            0, #type
            0, #spec
            0, #initlen
            bytes(6), # cdb
            ))
        # GPT protective MBR
        f.seek(0x1c0)
        f.write(bytes.fromhex("""
            02 00 ee ff ff ff 01 00  00 00 ff 9f 00 00 00 00
            00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00
            00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00
            00 00 00 00 00 00 00 00  00 00 00 00 00 00 55 aa
            """))
        # GPT header
        f.seek(0x200)
        f.write(gpt_header())
        f.seek(gpt_lba * 0x200)
        f.write(gpt_partitions())

        # sysvr2 inode table
        f.seek(block_pos(inode_table_block))
        f.write(inode(0, 0, 0, 0))
        # root inode (#2)
        dirent_len = 0x10
        if ccal is not None:
            dirent_len += 0x10;
        f.write(inode(0x4000, dirent_len, dirent_block, 0))
        # lost+found
        f.write(inode(0, 0, 0, 0))

        # linux kernel (#4)
        f.write(inode(0, len(svr2img), data_start_block, indirect_start_block))
        if ccal is not None:
            # ccal (#5)
            f.write(inode(0, len(ccal), ccal_start_block, ccal_index_block))

        # / dirents
        f.seek(block_pos(dirent_block))
        f.write(svr2_dent(4, "linux"))
        if ccal is not None:
            f.write(svr2_dent(5, "ccal"))

        f.seek(block_pos(indirect_start_block))
        write_indirect_block(f, data_start_block + 10)
        write_indirect2_blocks(f, indirect_start_block + 1, data_start_block + 10 + 0x100, max_kernel_blocks - (10 + 0x100))

        if ccal is not None:
            f.seek(block_pos(ccal_index_block))
            ccal_blocks = (len(ccal) + 0x3ff) >> 10
            write_indirect_block(f, ccal_start_block + 10, ccal_blocks - 10)
            f.seek(block_pos(ccal_start_block))
            f.write(ccal)

        f.seek(block_pos(data_start_block))
        f.write(svr2img)

        # Secondary GPT
        f.seek(rootfs_end_lba * 0x200)
        f.write(bytes(40 * 0x200))

    subprocess.run(["dd", f"if={args.rootfs}", f"of={args.disk}", "bs=512",
                    f"seek={rootfs_start_lba}", "conv=sparse,notrunc",
                    "status=none"], check=True)
    print(f"Created {args.disk}")

def main():
    parse_cmdline()
    svr2img = build_svr2img()

    if args.tape is not None:
        with open(args.tape, "wb") as f:
            f.write(svr2img)
        print(f"Created {args.tape}")

    if args.disk is not None:
        write_diskimg(svr2img)

if __name__ == "__main__":
    main()
