"""
QC Engine — State machine, test definitions, and report generator
for Yumi Lab factory quality control protocol.
"""
import json
import os
import logging
from datetime import datetime
from enum import Enum

logger = logging.getLogger("KlipperScreen.qc_engine")


class QCState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_GCODE = "waiting_gcode"
    WAITING_VISUAL = "waiting_visual"
    COMPLETED = "completed"
    ABORTED = "aborted"


class QCResult(Enum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


# Ordered list of all QC tests
QC_TESTS = [
    {
        "id": "home_x",
        "name": "X Axis Homing",
        "type": "automated",
        "macro": "QC_HOME_X",
        "timeout": 30,
    },
    {
        "id": "home_y",
        "name": "Y Axis Homing",
        "type": "automated",
        "macro": "QC_HOME_Y",
        "timeout": 30,
    },
    {
        "id": "home_z",
        "name": "Z Axis Homing",
        "type": "automated",
        "macro": "QC_HOME_Z",
        "timeout": 60,
    },
    {
        "id": "motor_dir_x",
        "name": "X Motor Direction",
        "type": "visual",
        "macro": "QC_MOTOR_DIR_X",
        "prompt": "L'axe X s'est-il déplacé vers la DROITE ?",
        "timeout": 30,
    },
    {
        "id": "motor_dir_y",
        "name": "Y Motor Direction",
        "type": "visual",
        "macro": "QC_MOTOR_DIR_Y",
        "prompt": "L'axe Y s'est-il déplacé vers l'ARRIÈRE ?",
        "timeout": 30,
    },
    {
        "id": "motor_dir_z",
        "name": "Z Motor Direction",
        "type": "visual",
        "macro": "QC_MOTOR_DIR_Z",
        "prompt": "L'axe Z est-il monté vers le HAUT ?",
        "timeout": 60,
    },
    {
        "id": "travel_x",
        "name": "X Full Travel",
        "type": "visual",
        "macro": "QC_TRAVEL_X",
        "prompt": "L'axe X a-t-il parcouru toute sa course sans blocage ?",
        "timeout": 60,
    },
    {
        "id": "travel_y",
        "name": "Y Full Travel",
        "type": "visual",
        "macro": "QC_TRAVEL_Y",
        "prompt": "L'axe Y a-t-il parcouru toute sa course sans blocage ?",
        "timeout": 60,
    },
    {
        "id": "travel_z",
        "name": "Z Full Travel",
        "type": "visual",
        "macro": "QC_TRAVEL_Z",
        "prompt": "L'axe Z a-t-il parcouru toute sa course sans blocage ?",
        "timeout": 120,
    },
    {
        "id": "probe_check",
        "name": "BLTouch Probe",
        "type": "visual",
        "macro": "QC_PROBE_CHECK",
        "prompt": "Le BLTouch s'est-il déployé et rétracté correctement ?",
        "timeout": 30,
    },
    {
        "id": "fan_part",
        "name": "Part Cooling Fan",
        "type": "visual",
        "macro": "QC_FAN_PART",
        "prompt": "Le ventilateur de refroidissement pièce tourne-t-il ?",
        "timeout": 15,
    },
    {
        "id": "fan_hotend",
        "name": "Hotend Fan",
        "type": "visual",
        "macro": "QC_FAN_HOTEND",
        "prompt": "Le ventilateur du hotend tourne-t-il ?",
        "timeout": 30,
    },
    {
        "id": "heat_extruder",
        "name": "Extruder Heating",
        "type": "automated",
        "macro": "QC_HEAT_EXTRUDER",
        "timeout": 180,
    },
    {
        "id": "heat_bed",
        "name": "Bed Heating",
        "type": "automated",
        "macro": "QC_HEAT_BED",
        "timeout": 300,
    },
    {
        "id": "bed_mesh",
        "name": "Bed Mesh",
        "type": "automated",
        "macro": "QC_BED_MESH",
        "timeout": 600,
    },
    {
        "id": "pid_extruder",
        "name": "PID Extruder",
        "type": "automated",
        "macro": "QC_PID_EXTRUDER",
        "timeout": 300,
    },
    {
        "id": "pid_bed",
        "name": "PID Bed",
        "type": "automated",
        "macro": "QC_PID_BED",
        "timeout": 600,
    },
    {
        "id": "save_config",
        "name": "Save Config",
        "type": "automated",
        "macro": "QC_SAVE",
        "timeout": 30,
    },
]


class QCEngine:
    def __init__(self):
        self.state = QCState.IDLE
        self.current_test_index = -1
        self.results = {}
        self.printer_id = ""
        self.technician = ""
        self.start_time = None
        self.test_start_time = None
        self._on_state_change = None
        self._on_test_complete = None
        self._on_visual_prompt = None
        self._on_qc_complete = None

    def set_callbacks(self, on_state_change=None, on_test_complete=None,
                      on_visual_prompt=None, on_qc_complete=None):
        self._on_state_change = on_state_change
        self._on_test_complete = on_test_complete
        self._on_visual_prompt = on_visual_prompt
        self._on_qc_complete = on_qc_complete

    def start(self, printer_id, technician=""):
        self.printer_id = printer_id
        self.technician = technician
        self.start_time = datetime.now()
        self.current_test_index = -1
        self.results = {}
        for test in QC_TESTS:
            self.results[test["id"]] = {
                "result": QCResult.PENDING,
                "timestamp": None,
                "details": "",
            }
        self._set_state(QCState.RUNNING)
        return self.next_test()

    def next_test(self):
        """Advance to the next test. Returns the test dict or None if done."""
        self.current_test_index += 1
        if self.current_test_index >= len(QC_TESTS):
            self._finish()
            return None
        test = QC_TESTS[self.current_test_index]
        self.test_start_time = datetime.now()
        logger.info(f"QC: Starting test {test['id']}")
        return test

    def get_current_test(self):
        if 0 <= self.current_test_index < len(QC_TESTS):
            return QC_TESTS[self.current_test_index]
        return None

    def get_progress(self):
        """Returns (current, total) tuple."""
        return (self.current_test_index + 1, len(QC_TESTS))

    def process_gcode_response(self, message):
        """Parse QC: prefixed gcode responses. Returns True if handled."""
        if not message.startswith("QC:") and not message.startswith("echo: QC:"):
            return False

        # Strip echo/RESPOND prefix if present
        msg = message.replace("echo: ", "").replace("// ", "").strip()
        parts = msg.split(":")
        if len(parts) < 3:
            return False

        test_id = parts[1]
        status = parts[2]

        test = self.get_current_test()
        if not test or test["id"].lower() != test_id.lower():
            logger.warning(f"QC: Received response for {test_id} but current test is {test['id'] if test else 'none'}")
            return True

        if status == "START":
            self._set_state(QCState.WAITING_GCODE)
            return True
        elif status == "PASS":
            self._record_result(test_id, QCResult.PASS)
            return True
        elif status == "FAIL":
            self._record_result(test_id, QCResult.FAIL, "Automated check failed")
            return True
        elif status == "VISUAL":
            self._set_state(QCState.WAITING_VISUAL)
            if self._on_visual_prompt and "prompt" in test:
                self._on_visual_prompt(test)
            return True

        return False

    def record_visual_result(self, passed):
        """Called by the UI when operator responds to a visual check."""
        test = self.get_current_test()
        if not test:
            return
        if passed:
            self._record_result(test["id"], QCResult.PASS)
        else:
            self._record_result(test["id"], QCResult.FAIL, "Visual check: operator reported failure")

    def skip_current_test(self):
        """Skip the current test."""
        test = self.get_current_test()
        if test:
            self._record_result(test["id"], QCResult.SKIPPED, "Skipped by operator")

    def abort(self):
        """Abort the entire QC process."""
        self._set_state(QCState.ABORTED)
        if self._on_qc_complete:
            self._on_qc_complete(self.generate_report())

    def _record_result(self, test_id, result, details=""):
        self.results[test_id] = {
            "result": result,
            "timestamp": datetime.now().isoformat(),
            "details": details,
        }
        logger.info(f"QC: Test {test_id} = {result.value}")
        self._set_state(QCState.RUNNING)

        # Auto-advance to next test
        next_test = self.next_test()

        if self._on_test_complete:
            self._on_test_complete(test_id, result)

        if next_test is None:
            return  # QC finished, _finish() already called

    def _finish(self):
        self._set_state(QCState.COMPLETED)
        if self._on_qc_complete:
            self._on_qc_complete(self.generate_report())

    def _set_state(self, state):
        old = self.state
        self.state = state
        if self._on_state_change:
            self._on_state_change(old, state)

    def generate_report(self):
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds() if self.start_time else 0

        failed = [tid for tid, r in self.results.items() if r["result"] == QCResult.FAIL]
        skipped = [tid for tid, r in self.results.items() if r["result"] == QCResult.SKIPPED]

        if failed:
            overall = "FAIL"
        elif skipped:
            overall = "PARTIAL"
        else:
            overall = "PASS"

        report = {
            "version": "1.0",
            "printer_id": self.printer_id,
            "technician": self.technician,
            "date": self.start_time.isoformat() if self.start_time else "",
            "date_end": end_time.isoformat(),
            "duration_seconds": int(duration),
            "tests": [],
            "overall_result": overall,
            "failed_tests": failed,
            "skipped_tests": skipped,
        }

        for test in QC_TESTS:
            r = self.results.get(test["id"], {})
            report["tests"].append({
                "id": test["id"],
                "name": test["name"],
                "type": test["type"],
                "result": r.get("result", QCResult.PENDING).value if isinstance(r.get("result"), QCResult) else str(r.get("result", "pending")),
                "timestamp": r.get("timestamp", ""),
                "details": r.get("details", ""),
            })

        return report

    def save_report(self, report=None):
        """Save report as JSON file. Returns the file path."""
        if report is None:
            report = self.generate_report()

        report_dir = os.path.expanduser("~/printer_data/config/qc_reports")
        os.makedirs(report_dir, exist_ok=True)

        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"QC_{self.printer_id}_{date_str}.json"
        filepath = os.path.join(report_dir, filename)

        with open(filepath, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"QC: Report saved to {filepath}")
        return filepath
