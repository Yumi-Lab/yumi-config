MKS Robin Nano V1.2 / V2.0 / V2.1 - Klipper firmware (Yumi-Lab fork)
=====================================================================
Version: v0.13.0-693-g5ac7c301
Built:   2026-07-16 (Yumi-Lab internal builder)
SHA256:  d59cc9fae017bc8dc281391a0e9d3094e2cdcec261a5e31142f4039b8b2c969a

FLASH
-----
Copy Robin_nano35.bin to the root of a FAT32 SD card (max 32GB),
insert it in the BOARD SD slot, power-cycle, wait ~30s.
If the board ignores it, rename the SAME file Robin_nano.bin, then
Robin_nano43.bin (bootloader flavor varies by factory batch).
The stock MKS touchscreen stays black under Klipper (normal).
WARNING: Robin Nano V1.3 is STM32F407 32KiB - use the V1.3 build, NOT this one.
Serial: /dev/serial/by-id/usb-1a86_USB2.0-Serial-* (CH340 ttyUSB, baud 250000).

BUILD DETAILS
-------------
Source:    github.com/Yumi-Lab/klipper
           commit 5ac7c3013fd4154461f948d694bdbc09f95ec35d (v0.13.0-693-g5ac7c301)
Toolchain: gcc-arm-none-eabi 12.2.1 20221205, binutils 2.40
Command:   make clean && make -j1

make menuconfig:
  Micro-controller: STM32  ->  STM32F103 (CONFIG_MCU="stm32f103xe")
  Bootloader offset: 28KiB bootloader  (CONFIG_FLASH_APPLICATION_ADDRESS=0x8007000)
  Clock reference: 8 MHz crystal, CPU 72 MHz
  Communication: Serial on USART3 PB11/PB10, baud 250000

Post-process: scripts/update_mks_robin.py (MANDATORY on this board - the stock
MKS F103 bootloader expects the XOR-obfuscated image, bytes 320..31040).
The .bin in this folder is ALREADY post-processed, flash it as-is.

Engraved constants (read back with the DEVICE macro / mcu_constants):
  YUMI_CONFIG  = board=ROBIN_NANO_V1_2_V2;brand=WANHAO;cpu=STM32F103;uid=D0314F
  YUMI_COMMENT = -

Post-build verification (automatic on the builder): bootloader offset proven
by the VTOR literal 0x08007000, MKS encryption verified on the code window,
MCU/USART3/baud read back from the embedded dictionary, descriptor engraved.
