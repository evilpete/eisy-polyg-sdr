#!/usr/bin/env python3
"""
Offline tests for record classification and unit handling.

These import only the rtl433 package, which has no dependency on
udi_interface, so they run on a laptop as well as on the EISY:

    python3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rtl433 import classify, units       # noqa: E402

LACROSSE = json.loads('{"time" : "2025-08-04 19:40:59", "model" : '
                      '"LaCrosse-TX141Bv3", "id" : 237, "channel" : 1, '
                      '"battery_ok" : 1, "temperature_C" : 20.600, '
                      '"Humidity" : 40, "test" : "No"}')

EFERGY = json.loads('{"time" : "2025-07-31 19:01:36", "model" : "Efergy-e2CT",'
                    ' "id" : 16386, "battery_ok" : 0, "current" : 18.781, '
                    '"interval" : 6}')

TPMS = json.loads('{"time" : "2025-08-04 18:22:45", "model" : "Hyundai-VDO", '
                  '"type" : "TPMS", "id" : "5173f60b", "state" : 48, '
                  '"flags" : 8, "repeat" : 1, "pressure_kPa" : 248.875, '
                  '"temperature_C" : 25.000, "maybe_battery" : 1, '
                  '"mic" : "CRC"}')


class TestClassification(unittest.TestCase):

    def test_temperature_sensor(self):
        reading = classify.reading(LACROSSE)
        self.assertEqual(reading['class'], classify.CLASS_THERMO)
        self.assertAlmostEqual(reading['values']['temperature_c'], 20.6)
        # "Humidity" with a capital H must still be picked up.
        self.assertEqual(reading['values']['humidity'], 40)
        self.assertEqual(reading['values']['battery'], 100)

    def test_energy_meter(self):
        reading = classify.reading(EFERGY)
        self.assertEqual(reading['class'], classify.CLASS_ENERGY)
        self.assertAlmostEqual(reading['values']['current_a'], 18.781)
        self.assertEqual(reading['values']['battery'], 0)

    def test_tpms(self):
        reading = classify.reading(TPMS)
        self.assertEqual(reading['class'], classify.CLASS_TPMS)
        self.assertAlmostEqual(reading['values']['pressure_kpa'], 248.875)
        self.assertAlmostEqual(reading['values']['temperature_c'], 25.0)
        self.assertEqual(reading['values']['battery'], 100)

    def test_unknown_device_is_generic(self):
        reading = classify.reading({'model': 'Generic-Remote', 'id': 8801,
                                    'cmd': 12})
        self.assertEqual(reading['class'], classify.CLASS_GENERIC)
        self.assertEqual(reading['values'], {})

    def test_missing_fields_are_omitted_not_nulled(self):
        """A partial packet must not carry keys that would erase cached data."""
        reading = classify.reading({'model': 'X', 'id': 1, 'humidity': 55})
        self.assertNotIn('temperature_c', reading['values'])

    def test_fahrenheit_input_is_converted(self):
        reading = classify.reading({'model': 'X', 'id': 1,
                                    'temperature_F': 68.0})
        self.assertAlmostEqual(reading['values']['temperature_c'], 20.0)

    def test_battery_string_forms(self):
        self.assertEqual(classify.battery_percent({'battery_ok': 'LOW'}), 0)
        self.assertEqual(classify.battery_percent({'battery_ok': 'OK'}), 100)
        self.assertIsNone(classify.battery_percent({'id': 1}))


class TestIdentityAndAddress(unittest.TestCase):

    def test_addresses_are_readable_and_legal(self):
        cases = [
            (LACROSSE, 'lacr_237_1'),
            (EFERGY, 'efer_16386'),
            (TPMS, 'hyun_5173f60b'),
        ]
        for record, expected in cases:
            address = classify.make_address(classify.identity(
                classify.normalize_keys(record)))
            self.assertEqual(address, expected)
            self.assertLessEqual(len(address), classify.MAX_ADDRESS_LEN)
            self.assertRegex(address, r'^[a-z0-9_]+$')

    def test_channel_separates_devices(self):
        one = classify.identity(classify.normalize_keys(
            {'model': 'LaCrosse-TX141Bv3', 'id': 237, 'channel': 1}))
        two = classify.identity(classify.normalize_keys(
            {'model': 'LaCrosse-TX141Bv3', 'id': 237, 'channel': 2}))
        self.assertNotEqual(classify.make_address(one),
                            classify.make_address(two))

    def test_long_identity_falls_back_to_hash(self):
        ident = ('Some-Very-Long-Model-Name', 'abcdef0123456789', '3')
        address = classify.make_address(ident)
        self.assertLessEqual(len(address), classify.MAX_ADDRESS_LEN)
        self.assertRegex(address, r'^[a-z0-9_]+$')
        # Deterministic across runs, so nodes survive a plugin restart.
        self.assertEqual(address, classify.make_address(ident))

    def test_address_is_stable_for_same_device(self):
        first = classify.reading(LACROSSE)
        second = classify.reading(dict(LACROSSE, temperature_C=21.9))
        self.assertEqual(classify.make_address(first['identity']),
                         classify.make_address(second['identity']))


class TestUnits(unittest.TestCase):

    def test_temperature_output(self):
        value, uom = units.temperature_out(20.6, units.UNITS_US)
        self.assertAlmostEqual(value, 69.1)
        self.assertEqual(uom, units.UOM_F)

        value, uom = units.temperature_out(20.6, units.UNITS_METRIC)
        self.assertAlmostEqual(value, 20.6)
        self.assertEqual(uom, units.UOM_C)

    def test_pressure_output(self):
        value, uom = units.pressure_out(248.875, units.UNITS_US)
        self.assertAlmostEqual(value, 36.1)
        self.assertEqual(uom, units.UOM_PSI)

        value, uom = units.pressure_out(248.875, units.UNITS_METRIC)
        self.assertAlmostEqual(value, 248.9)
        self.assertEqual(uom, units.UOM_KPA)

    def test_units_parameter_parsing(self):
        self.assertEqual(units.normalize_units('c'), units.UNITS_METRIC)
        self.assertEqual(units.normalize_units('Celsius'), units.UNITS_METRIC)
        self.assertEqual(units.normalize_units('f'), units.UNITS_US)
        self.assertEqual(units.normalize_units(None), units.UNITS_US)
        self.assertEqual(units.normalize_units('nonsense'), units.UNITS_US)


if __name__ == '__main__':
    unittest.main()
