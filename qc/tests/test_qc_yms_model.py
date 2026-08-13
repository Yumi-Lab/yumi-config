import unittest

from qc.qc_yms import device_for_model


class TestModelMapping(unittest.TestCase):
    def test_light(self):
        qc_model, device, prefix = device_for_model("light")
        self.assertEqual(qc_model, "YMS-LIGHT")
        self.assertEqual(device, "device=YMS-LIGHT")
        self.assertEqual(prefix, "YMSL-")

    def test_pro(self):
        qc_model, device, prefix = device_for_model("pro")
        self.assertEqual(qc_model, "YMS-PRO")
        self.assertEqual(device, "device=YMS-PRO")
        self.assertEqual(prefix, "YMSP-")

    def test_default(self):
        qc_model, device, prefix = device_for_model(None)
        self.assertEqual(qc_model, "YMS-LIGHT")
        self.assertEqual(prefix, "YMSL-")


if __name__ == "__main__":
    unittest.main()
