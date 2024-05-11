Attention :

#Ender 3 Pro 4.2.7
# Pour utiliser cette configuration, lors du "make menuconfig" sélectionner le
# STM32F103 avec un "28KiB bootloader" et une communication série (sur USART1 PA10/PA9)
# communication.

Si vous préférez une connexion série directe, dans "make menuconfig" # sélectionnez "Enable extra low-level connection".
# sélectionnez "Enable extra low-level configuration options" et sélectionnez
# série (sur USART3 PB11/PB10), qui est répartie sur le câble IDC à 10 broches
# 10 broches utilisé pour le module LCD comme suit :
# 3 : Tx, 4 : Rx, 9 : GND, 10 : VCC

# Flashez ce firmware en copiant "out/klipper.bin" sur une carte SD et en allumant l'imprimante avec la carte.
# en allumant l'imprimante avec la carte insérée. Le nom de fichier du firmware
# doit se terminer par ".bin" et ne doit pas correspondre au dernier nom de fichier
# qui a été flashé.


Warning:

#Ender 3 Pro 4.2.7
# To use this config, during "make menuconfig" select the
# STM32F103 with a "28KiB bootloader" and serial (on USART1 PA10/PA9)
# communication.

# If you prefer a direct serial connection, in "make menuconfig"
# select "Enable extra low-level configuration options" and select
# serial (on USART3 PB11/PB10), which is broken out on the 10 pin IDC
# cable used for the LCD module as follows:
# 3: Tx, 4: Rx, 9: GND, 10: VCC

# Flash this firmware by copying "out/klipper.bin" to a SD card and
# turning on the printer with the card inserted. The firmware
# filename must end in ".bin" and must not match the last filename
# that was flashed.
