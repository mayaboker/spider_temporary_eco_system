import unittest
from types import SimpleNamespace

from home_process import (
    direction_visual,
    drive_values,
    next_direction,
    requested_flipper_velocity,
)


class HomeControlTests(unittest.TestCase):
    def wheel(self, **overrides):
        values = {
            "connected": True,
            "accelerator": 0.8,
            "brake": 0.25,
            "steering": -0.3,
            "buttons": {},
            "config": {"flipper_velocity": 20.0},
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_drive_does_not_depend_on_clutch(self):
        linear, angular = drive_values(self.wheel(), 1.0)
        self.assertAlmostEqual(linear, 0.6)
        self.assertEqual(angular, -0.3)
        linear, angular = drive_values(self.wheel(), -1.0)
        self.assertAlmostEqual(linear, -0.6)
        self.assertEqual(angular, -0.3)

    def test_disconnected_wheel_is_safe(self):
        self.assertEqual(drive_values(self.wheel(connected=False), -1.0), (0.0, 0.0))

    def test_button_23_toggles_only_on_press_edge(self):
        self.assertEqual(next_direction(1.0, True, False), -1.0)
        self.assertEqual(next_direction(-1.0, True, True), -1.0)
        self.assertEqual(next_direction(-1.0, False, True), -1.0)

    def test_direction_visual_reflects_current_state(self):
        self.assertEqual(direction_visual(1.0), ("Forward", "#2ecc71"))
        self.assertEqual(direction_visual(-1.0), ("Reverse", "#ff7043"))

    def test_buttons_19_and_20_move_all_flippers(self):
        self.assertEqual(
            requested_flipper_velocity(self.wheel(buttons={19: True}), True),
            (20.0, 20.0, 20.0, 20.0),
        )
        self.assertEqual(
            requested_flipper_velocity(self.wheel(buttons={20: True}), True),
            (-20.0, -20.0, -20.0, -20.0),
        )

    def test_clutch_gate_disables_flipper_request(self):
        wheel = self.wheel(buttons={19: True})
        self.assertIsNone(requested_flipper_velocity(wheel, False))
        self.assertIsNone(requested_flipper_velocity(self.wheel(buttons={19: True, 20: True}), True))


if __name__ == "__main__":
    unittest.main()
