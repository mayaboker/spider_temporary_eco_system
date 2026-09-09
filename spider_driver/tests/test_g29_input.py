import json
import tempfile
import unittest

from g29_input import G29Input


class G29NormalizationTests(unittest.TestCase):
    def setUp(self):
        config = {
            "device": "/does/not/exist",
            "steering": {"axis": 0, "minimum": -1.0, "maximum": 1.0, "deadzone": 0.1},
            "clutch": {"axis": 1, "released": 1.0, "pressed": -1.0},
            "accelerator": {"axis": 2, "released": 1.0, "pressed": -1.0},
            "brake": {"axis": 3, "released": 1.0, "pressed": -1.0},
        }
        self.file = tempfile.NamedTemporaryFile(mode="w", suffix=".json")
        json.dump(config, self.file)
        self.file.flush()
        self.wheel = G29Input(self.file.name)

    def tearDown(self):
        self.wheel.close()
        self.file.close()

    def test_pedals_normalize_released_and_pressed(self):
        self.wheel.axes.update({1: 1.0, 2: -1.0, 3: 0.0})
        self.assertEqual(self.wheel.clutch, 0.0)
        self.assertEqual(self.wheel.accelerator, 1.0)
        self.assertEqual(self.wheel.brake, 0.5)

    def test_steering_deadzone_and_rescale(self):
        self.wheel.axes[0] = 0.05
        self.assertEqual(self.wheel.steering, 0.0)
        self.wheel.axes[0] = 1.0
        self.assertEqual(self.wheel.steering, 1.0)


if __name__ == "__main__":
    unittest.main()
