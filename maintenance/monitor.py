"""
Yumi Maintenance Monitor — Klippy Extra

Compteurs automatiques d'usure et maintenance preventive.
S'installe comme klippy extra dans ~/klipper/klippy/extras/yumi_maintenance.py

Compteurs:
  - print_hours: heures d'impression total
  - extrusion_total_mm: distance totale extrudee (tous YMS)
  - extrusion_per_yms: distance par YMS individuel
  - cuts_count: nombre de coupes filament (CUT_FILAMENT)
  - bed_heat_hours: heures de chauffe bed
  - hotend_heat_hours: heures de chauffe hotend
  - z_moves: nombre de mouvements Z total
  - homing_count: nombre de homing effectues

Detection anomalies:
  - freq_drift: variation frequence resonance (input_shaper)
  - sg_drift: variation stallguard (sensorless homing)
"""

import logging
import time
import json
import os


class YumiMaintenance:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()

        # Charger les seuils
        thresholds_path = config.get('thresholds_path',
                                     '~/printer_data/config/maintenance_thresholds.json')
        self.thresholds_path = os.path.expanduser(thresholds_path)
        self.thresholds = self._load_thresholds()

        # Interval de mise a jour (secondes)
        self.update_interval = config.getfloat('update_interval', 60.0)

        # Compteurs (charges depuis save_variables au start)
        # Temps en MINUTES (int) — conversion h/j cote frontend
        self.counters = {
            "print_minutes": 0,
            "extrusion_total_mm": 0.0,
            "bed_heat_minutes": 0,
            "hotend_heat_minutes": 0,
            "cuts_count": 0,
            "homing_count": 0,
            "z_moves": 0,
        }
        self.extrusion_per_yms = {}
        self._last_update_time = None
        self._last_position_e = None
        self._printing = False

        # Register handlers
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("print_stats:state_changed", self._handle_print_state)

        # GCode commands
        self.gcode.register_command("MAINTENANCE_STATUS", self.cmd_MAINTENANCE_STATUS,
                                    desc="Show maintenance counters and alerts")
        self.gcode.register_command("MAINTENANCE_RESET", self.cmd_MAINTENANCE_RESET,
                                    desc="Reset a specific maintenance counter")

    def _load_thresholds(self):
        """Charge les seuils depuis le fichier JSON"""
        try:
            with open(self.thresholds_path, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return self._default_thresholds()

    def _default_thresholds(self):
        """Seuils par defaut"""
        return {
            "print_hours": {
                "warning": 500,
                "critical": 1000,
                "action": "Inspection generale recommandee"
            },
            "extrusion_per_yms_mm": {
                "warning": 50000000,
                "critical": 100000000,
                "action": "Verifier gear YMS, remplacer si use"
            },
            "cuts_count": {
                "warning": 5000,
                "critical": 10000,
                "action": "Verifier lame cutter, affuter ou remplacer"
            },
            "bed_heat_hours": {
                "warning": 2000,
                "critical": 5000,
                "action": "Verifier connecteur bed, resistance thermistance"
            },
            "hotend_heat_hours": {
                "warning": 1000,
                "critical": 2000,
                "action": "Verifier heatbreak, remplacer si colmatage"
            },
            "belt_tension_drift": {
                "warning": 5.0,
                "critical": 10.0,
                "action": "Retendre courroie X/Y"
            }
        }

    def _handle_ready(self):
        """Appele quand Klipper est pret"""
        self._load_counters_from_variables()
        self._last_update_time = self.reactor.monotonic()
        # Timer periodique
        self.reactor.register_timer(self._periodic_update,
                                    self.reactor.monotonic() + self.update_interval)

    def _load_counters_from_variables(self):
        """Charge les compteurs depuis save_variables"""
        try:
            svars = self.printer.lookup_object('save_variables')
            variables = svars.allVariables
            for key in self.counters:
                var_name = f"maint_{key}"
                if var_name in variables:
                    self.counters[key] = variables[var_name]
            # Compteurs per-YMS
            for i in range(12):
                var_name = f"maint_extrusion_yms_{i}"
                if var_name in variables:
                    self.extrusion_per_yms[i] = variables[var_name]
        except Exception:
            pass

    def _save_counters(self):
        """Sauvegarde les compteurs via save_variables"""
        try:
            svars = self.printer.lookup_object('save_variables')
            for key, value in self.counters.items():
                svars.cmd_SAVE_VARIABLE(
                    self.gcode.create_gcode_command(
                        "SAVE_VARIABLE", f"VARIABLE=maint_{key} VALUE={value}", {}))
            for idx, value in self.extrusion_per_yms.items():
                svars.cmd_SAVE_VARIABLE(
                    self.gcode.create_gcode_command(
                        "SAVE_VARIABLE", f"VARIABLE=maint_extrusion_yms_{idx} VALUE={value}", {}))
        except Exception:
            pass

    def _handle_print_state(self, state):
        """Track print state changes"""
        if state == "printing":
            self._printing = True
            self._last_update_time = self.reactor.monotonic()
        elif state in ("complete", "cancelled", "error", "standby"):
            if self._printing:
                self._flush_counters()
            self._printing = False

    def _periodic_update(self, eventtime):
        """Mise a jour periodique des compteurs"""
        if self._printing:
            # Heures d'impression
            elapsed = eventtime - self._last_update_time
            self.counters["print_minutes"] += int(elapsed / 60.0)

            # Heures chauffe bed/hotend
            self._update_heater_hours(elapsed)

            # Extrusion tracking
            self._update_extrusion()

        self._last_update_time = eventtime

        # Sauvegarder toutes les 5 minutes
        if int(eventtime) % 300 < int(self.update_interval):
            self._save_counters()

        # Check alerts
        self._check_alerts()

        return eventtime + self.update_interval

    def _update_heater_hours(self, elapsed_seconds):
        """Increment heater hours si chauffes actives"""
        try:
            heater_bed = self.printer.lookup_object('heater_bed', None)
            if heater_bed and heater_bed.get_status(0).get('target', 0) > 0:
                self.counters["bed_heat_minutes"] += int(elapsed_seconds / 60.0)

            extruder = self.printer.lookup_object('extruder', None)
            if extruder and extruder.get_status(0).get('target', 0) > 0:
                self.counters["hotend_heat_minutes"] += int(elapsed_seconds / 60.0)
        except Exception:
            pass

    def _update_extrusion(self):
        """Track extrusion distance"""
        try:
            motion = self.printer.lookup_object('motion_report', None)
            if motion:
                pos = motion.get_status(0).get('live_position', [0, 0, 0, 0])
                current_e = pos[3] if len(pos) > 3 else 0
                if self._last_position_e is not None:
                    delta = abs(current_e - self._last_position_e)
                    if delta > 0 and delta < 10000:  # sane check
                        self.counters["extrusion_total_mm"] += delta
                        # Track per active YMS
                        active = self._get_active_yms()
                        if active is not None:
                            self.extrusion_per_yms.setdefault(active, 0.0)
                            self.extrusion_per_yms[active] += delta
                self._last_position_e = current_e
        except Exception:
            pass

    def _get_active_yms(self):
        """Retourne l'index du YMS actif (depuis save_variables)"""
        try:
            svars = self.printer.lookup_object('save_variables')
            return svars.allVariables.get('active_tool', None)
        except Exception:
            return None

    def _flush_counters(self):
        """Sauvegarde finale en fin d'impression"""
        self._save_counters()

    def _check_alerts(self):
        """Verifie les seuils et envoie des alertes"""
        alerts = []
        for key, value in self.counters.items():
            if key in self.thresholds:
                t = self.thresholds[key]
                if value >= t.get("critical", float("inf")):
                    alerts.append(f"CRITICAL: {key}={value:.1f} - {t['action']}")
                elif value >= t.get("warning", float("inf")):
                    alerts.append(f"WARNING: {key}={value:.1f} - {t['action']}")

        # Alertes per-YMS
        yms_threshold = self.thresholds.get("extrusion_per_yms_mm", {})
        for idx, value in self.extrusion_per_yms.items():
            if value >= yms_threshold.get("critical", float("inf")):
                alerts.append(f"CRITICAL: YMS-{idx+1} extrusion={value:.0f}mm - {yms_threshold.get('action', '')}")
            elif value >= yms_threshold.get("warning", float("inf")):
                alerts.append(f"WARNING: YMS-{idx+1} extrusion={value:.0f}mm - {yms_threshold.get('action', '')}")

        if alerts:
            for alert in alerts:
                self.gcode.respond_info(f"[MAINTENANCE] {alert}")

    def cmd_MAINTENANCE_STATUS(self, gcmd):
        """Affiche l'etat des compteurs"""
        msg = ["=== YUMI MAINTENANCE STATUS ==="]
        msg.append(f"Print time: {self.counters['print_minutes']}min")
        msg.append(f"Extrusion total: {self.counters['extrusion_total_mm']/1000:.1f}m")
        msg.append(f"Bed heat time: {self.counters['bed_heat_minutes']}min")
        msg.append(f"Hotend heat time: {self.counters['hotend_heat_minutes']}min")
        msg.append(f"Cuts count: {self.counters['cuts_count']}")
        msg.append(f"Homing count: {self.counters['homing_count']}")

        if self.extrusion_per_yms:
            msg.append("--- Per YMS ---")
            for idx in sorted(self.extrusion_per_yms.keys()):
                val = self.extrusion_per_yms[idx]
                msg.append(f"  YMS-{idx+1}: {val/1000:.1f}m")

        gcmd.respond_info("\n".join(msg))

    def cmd_MAINTENANCE_RESET(self, gcmd):
        """Reset un compteur specifique"""
        counter = gcmd.get("COUNTER", "")
        if counter in self.counters:
            self.counters[counter] = 0
            self._save_counters()
            gcmd.respond_info(f"Counter {counter} reset to 0")
        elif counter.startswith("yms_"):
            try:
                idx = int(counter.replace("yms_", "")) - 1
                self.extrusion_per_yms[idx] = 0.0
                self._save_counters()
                gcmd.respond_info(f"YMS-{idx+1} extrusion counter reset")
            except ValueError:
                gcmd.respond_info(f"Unknown counter: {counter}")
        else:
            gcmd.respond_info(f"Unknown counter: {counter}. Available: {list(self.counters.keys())}")

    def get_status(self, eventtime):
        """Status pour Moonraker/API"""
        return {
            "counters": self.counters.copy(),
            "extrusion_per_yms": self.extrusion_per_yms.copy(),
            "printing": self._printing,
        }


def load_config(config):
    return YumiMaintenance(config)
