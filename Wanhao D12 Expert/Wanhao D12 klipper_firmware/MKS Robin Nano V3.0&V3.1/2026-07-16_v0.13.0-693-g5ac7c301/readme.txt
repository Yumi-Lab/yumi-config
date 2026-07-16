MKS Robin Nano V3.0 / V3.1 - Klipper firmware (Yumi-Lab fork)
=============================================================
Version: v0.13.0-693-g5ac7c301
Built:   2026-07-16 (Yumi-Lab internal builder)
SHA256:  d120d6b3ecd3b488138fe11714c29e90b8a0ea6cfff5fac487cce98f19fe623e

FLASH
-----
Copy Robin_nano_v3.bin to the root of a FAT32 SD card (max 32GB),
insert it in the BOARD SD slot, power-cycle, wait ~30s.
Serial: /dev/serial/by-id/usb-Klipper_stm32f407xx_* (native USB, ttyACM).

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
  Communication: USB (on PA11/PA12), serial number from chip id
                 (CONFIG_USB_SERIAL_NUMBER_CHIPID=y)

Post-process: NONE (no MKS encryption on this bootloader - never run
scripts/update_mks_robin.py on this file).

Engraved constants (read back with the DEVICE macro / mcu_constants):
  YUMI_CONFIG  = board=ROBIN_NANO_V3;brand=WANHAO;cpu=STM32F407;uid=4095ED
  YUMI_COMMENT = -

Post-build verification (automatic on the builder): bootloader offset proven
by the VTOR literal 0x0800C000, plaintext verified on the code window,
MCU/native-USB PA11,PA12 read back from the embedded dictionary, descriptor
engraved.
