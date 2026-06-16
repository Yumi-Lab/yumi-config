# mcu_uid — expose l'ID unique matériel du MCU (STM32 UID 96 bits) au host.
#
# AUCUNE modification firmware : on lit la mémoire du MCU via la commande
# `debug_read` déjà présente dans tout firmware Klipper (src/debugcmds.c,
# HF_IN_SHUTDOWN → lisible même MCU en shutdown). On lit 3 mots de 32 bits à
# l'adresse de l'UID usine et on les concatène en hex.
#
# Sur STM32F4 (SMART_MAKER / C235/C335/C435) l'UID 96 bits est à 0x1FFF7A10.
# Adresse surchargeable par `addr:` si une autre famille MCU est utilisée.
#
#   [mcu_uid]
#   #mcu: mcu          # nom du MCU à interroger (défaut: mcu)
#   #addr: 0x1FFF7A10  # base UID (STM32F4 par défaut)
#   #words: 3          # nombre de mots 32 bits (96 bits = 3)
#
# La requête MCU est BLOQUANTE : elle doit se faire dans un contexte gcode
# (le reactor n'autorise pas la pause depuis un handler d'évènement). On expose
# donc la commande gcode QUERY_MCU_UID qui lit, met en cache et répond
# "MCU_UID=<hex>". Lecture ensuite via printer['mcu_uid'].uid.
import logging
import struct

import mcu as mcu_mod

STM32F4_UID_BASE = 0x1FFF7A10


class McuUid:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.mcu_name = config.get('mcu', 'mcu')
        self.addr = config.getint('addr', STM32F4_UID_BASE)
        self.words = config.getint('words', 3, minval=1, maxval=8)
        self.uid = ""
        self._query = None
        self._mcu = mcu_mod.get_printer_mcu(self.printer, self.mcu_name)
        self._mcu.register_config_callback(self._build_config)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command(
            'QUERY_MCU_UID', self.cmd_QUERY_MCU_UID,
            desc="Lit l'ID materiel unique du MCU (STM32 UID) via debug_read")

    def _build_config(self):
        # `debug_read order=%c addr=%u` -> `debug_result val=%u` (order 2 = 32 bits)
        # Défensif : ne JAMAIS empêcher Klipper de démarrer.
        try:
            self._query = self._mcu.lookup_query_command(
                "debug_read order=%c addr=%u", "debug_result val=%u")
        except Exception as e:
            logging.warning("mcu_uid: commande debug_read indisponible: %s", e)
            self._query = None

    def _read(self):
        if self._query is None:
            raise self.printer.command_error("debug_read indisponible")
        vals = []
        for i in range(self.words):
            params = self._query.send([2, self.addr + i * 4])
            vals.append(params['val'] & 0xffffffff)
        return b"".join(struct.pack("<I", v) for v in vals).hex().upper()

    def cmd_QUERY_MCU_UID(self, gcmd):
        try:
            self.uid = self._read()
        except Exception as e:
            self.uid = ""
            logging.warning("mcu_uid: lecture echouee (%s): %s", self.mcu_name, e)
            gcmd.respond_info("MCU_UID_ERROR: %s" % (e,))
            return
        logging.info("mcu_uid: %s = %s", self.mcu_name, self.uid)
        gcmd.respond_info("MCU_UID=%s" % (self.uid,))

    def get_status(self, eventtime):
        return {'uid': self.uid}


def load_config(config):
    return McuUid(config)
