MKS Robin Nano V1.3 (STM32F407, 32KiB) - Klipper firmware (Yumi-Lab fork)
=========================================================================
Version: v0.13.0-693-g5ac7c301
Built:   2026-07-16 (Yumi-Lab internal builder)
SHA256:  ee04b9e35bf99c44719d2d805eb59e101c6a389fbf4d0bb8f2b462b3bd2d6cf6

FLASH
-----
1. Download MKS1.3.bin (named after the board so you cannot mix up builds).
2. Rename it to Robin_nano35.bin.
3. Copy it to the root of a FAT32 SD card (max 32GB), insert it in the
   BOARD SD slot, power-cycle, wait ~30s.
WARNING: despite the V1.x name, V1.3 is STM32F407 with a UNIQUE 32KiB offset -
not the F103 build (V1.2/V2), not the 48KiB V3 build.
Serial: /dev/serial/by-id/usb-1a86_USB_Serial-* (CH340 ttyUSB, baud 250000).

BUILD DETAILS
-------------
Source:    github.com/Yumi-Lab/klipper
           commit 5ac7c3013fd4154461f948d694bdbc09f95ec35d (v0.13.0-693-g5ac7c301)
Toolchain: gcc-arm-none-eabi 12.2.1 20221205, binutils 2.40
Command:   make clean && make -j1

make menuconfig:
  Micro-controller: STM32  ->  STM32F407 (CONFIG_MCU="stm32f407xx")
  Bootloader offset: 32KiB bootloader  (CONFIG_FLASH_APPLICATION_ADDRESS=0x8008000)
  Clock reference: 8 MHz crystal, CPU 168 MHz
  Communication: Serial on USART3 PB11/PB10, baud 250000

Post-process: NONE (no MKS encryption on this bootloader - never run
scripts/update_mks_robin.py on this file).

Engraved constants (read back with the DEVICE macro / mcu_constants):
  YUMI_CONFIG  = board=ROBIN_NANO_V1_3;brand=WANHAO;cpu=STM32F407;uid=923531
  YUMI_COMMENT = -

Post-build verification (automatic on the builder): bootloader offset proven
by the VTOR literal 0x08008000, plaintext verified on the code window,
MCU/USART3/baud read back from the embedded dictionary, descriptor engraved.
