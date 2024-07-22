Plexus P/20

Intro
=====

This is the buildroot basic board support for the Plexus P/20

The Plexus P/20 is is a m68010 based unix machine. More info at https://github.com/misterblack1/plexus-p20

Limitations
===========

Requires 4MB of DRAM installed. In theory 2MB might be possible, but I
couldn't make it fit. Support a max of 4MB of DRAM - more requires
kernel HIGHMEM support, which isn't implemented yet

Not binary-compatible with other m68k-linux systems (these requires a 68020 +
FPU).

How to build it
===============

To build a basic image:

  $ make plexus_p20_defconfig
  $ make

this will output a bootable disk imake in output/images/plexus.img

Running in QEMU

Build QEMU from https://github.com/pbrook/qemu/tree/plexus-p20
You will also need the ROM images from https://github.com/misterblack1/plexus-p20

Run the emulator with:

  qemu-system-m68k -m 4M -M p20 -L ~/plexus-p20/ROMs \
    -icount 7,sleep=off,align=off \
    -drive file=output/images/plexus.img,format=raw,if=scsi \
    -serial stdio -display none \
    -global p20-rtc.file=plexus-rtc-ram.dat

Press enter at the PLEXUS PRIMARY BOOT prompt to load linux
Running `/ccal` from the boot prompt allows modification of the bootrom
configuration (including enabling auto-boot)

Standalone binaries or alternate kernel images can be loaded via the tape drive
by adding the following commandline options:

    -device scsi-tape,scsi-id=7,drive=p20tape \
    -blockdev file,node-name=p20tape,filename=output/images/vmlinux.coff

Then run `mt(,)` from the boot prompt
