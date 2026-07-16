MKS Robin Nano V3.2 (USART3 / CH340) - Klipper firmware (Yumi-Lab fork)
=======================================================================
Version: v0.13.0-693-g5ac7c301
Built:   2026-07-16 (Yumi-Lab internal builder)
SHA256:  8cabb5932382f1ca2fa400936a9cea0845e797dde53b1adefbf6f77289125115

FLASH
-----
Copy Robin_nano_v3.bin to the root of a FAT32 SD card (max 32GB),
insert it in the BOARD SD slot, power-cycle, wait ~30s.
Same F407/48KiB family as V3.0/V3.1 - the ONLY difference is communication:
USART3 via onboard CH340 (/dev/serial/by-id/usb-1a86_USB_Serial-*, ttyUSB).

BUILD DETAILS
-------------
Source:    github.com/Yumi-Lab/klipper
           commit 5ac7c3013fd4154461f948d694bdbc09f95ec35d (v0.13.0-693-g5ac7c301)
Toolchain: gcc-arm-none-eabi 12.2.1 20221205, binutils 2.40
Command:   make clean && make -j1

make menuconfig:
  Micro-controller: STM32  ->  STM32F407 (CONFIG_MCU="stm32f407xx")
  Bootloader offset: 48KiB bootloader  (CONFIG_FLASH_APPLICATION_ADDRESS=0x800C000)
  Clock reference: 8 MHz crystal, CPU 168 MHz
  Communication: Serial on USART3 PB11/PB10, baud 250000 (NO native USB)

Post-process: NONE (no MKS encryption on this bootloader - never run
scripts/update_mks_robin.py on this file).

Engraved constants (read back with the DEVICE macro / mcu_constants):
  YUMI_CONFIG  = board=ROBIN_NANO_V3_2;brand=WANHAO;cpu=STM32F407;uid=B1D114
  YUMI_COMMENT = -

Post-build verification (automatic on the builder): bootloader offset proven
by the VTOR literal 0x0800C000, plaintext verified on the code window,
MCU/USART3/baud read back from the embedded dictionary, descriptor engraved.
