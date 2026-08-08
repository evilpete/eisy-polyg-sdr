#!/usr/bin/env python3
"""
End-to-end tests for the controller, driven through a real socket.

A stub stands in for udi_interface (see stub_udi.py), so the whole path --
TCP line in, classify, allocate an address, create the node, cache the
reading, flush on poll -- runs on a laptop.  The stub raises on any driver
that is not declared for the node's nodedef, which also catches profile
drift between sensor.py and nodedefs.xml.
"""

import json
import os
import socket
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stub_udi                                   # noqa: E402

stub_udi.install()

from nodes import controller                      # noqa: E402
from rtl433 import units                          # noqa: E402

LACROSSE = ('{"time":"2025-08-04 19:40:59","model":"LaCrosse-TX141Bv3",'
            '"id":237,"channel":1,"battery_ok":1,"temperature_C":20.600,'
            '"Humidity":40,"test":"No"}')
EFERGY = ('{"time":"2025-07-31 19:01:36","model":"Efergy-e2CT","id":16386,'
          '"battery_ok":0,"current":18.781,"interval":6}')
TPMS = ('{"time":"2025-08-04 18:22:45","model":"Hyundai-VDO","type":"TPMS",'
        '"id":"5173f60b","state":48,"flags":8,"repeat":1,'
        '"pressure_kPa":248.875,"temperature_C":25.000,"maybe_battery":1,'
        '"mic":"CRC"}')


def free_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class ControllerHarness:

    def __init__(self, **params):
        self.poly = stub_udi.Interface([])
        self.port = free_port()
        self.ctl = controller.Controller(self.poly, 'controller', 'controller',
                                         'SDR rtl_433')
        config = {'port': self.port, 'bind': '127.0.0.1'}
        config.update(params)
        self.poly.fire(stub_udi.Interface.CUSTOMPARAMS, config)
        self.poly.fire(stub_udi.Interface.START)
        self.poly.fire(stub_udi.Interface.CONFIGDONE)

    def send(self, *lines):
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=5)
        try:
            for line in lines:
                sock.sendall(line.encode('utf-8') + b'\n')
        finally:
            sock.close()

    def wait_until(self, predicate, timeout=5.0):
        """Poll for a condition -- ingest happens on other threads."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def wait_for_nodes(self, count, timeout=5.0):
        self.wait_until(lambda: len(self.ctl.devices) >= count, timeout)
        # A node becomes visible just before its first reading is applied,
        # so settle before asserting on driver values.
        self.settle()
        return self.ctl.devices

    def wait_for_packets(self, count, timeout=5.0):
        self.wait_until(lambda: self.ctl.packets >= count, timeout)
        return self.ctl.packets

    def settle(self, timeout=1.0):
        """Give in-flight records a chance to land before asserting."""
        time.sleep(0.05)
        self.wait_until(lambda: self.ctl.pending.empty(), timeout)
        time.sleep(0.05)

    def poll(self, kind='shortPoll'):
        self.poly.fire(stub_udi.Interface.POLL, kind)

    def stop(self):
        self.poly.fire(stub_udi.Interface.STOP)


class TestIngest(unittest.TestCase):

    def setUp(self):
        self.harness = ControllerHarness()

    def tearDown(self):
        self.harness.stop()

    def test_three_example_devices_become_typed_nodes(self):
        self.harness.send(LACROSSE, EFERGY, TPMS)
        nodes = self.harness.wait_for_nodes(3)
        self.assertEqual(len(nodes), 3)

        self.assertEqual(nodes['lacr_237_1'].id, 'rtemp')
        self.assertEqual(nodes['efer_16386'].id, 'rpower')
        self.assertEqual(nodes['hyun_5173f60b'].id, 'rtpms')

    def test_values_reach_drivers_on_poll(self):
        self.harness.send(LACROSSE, EFERGY, TPMS)
        self.harness.wait_for_nodes(3)
        self.harness.poll()

        temp = self.harness.ctl.devices['lacr_237_1']
        self.assertAlmostEqual(temp.getDriver('CLITEMP'), 69.1)
        self.assertEqual(temp.getDriver('CLIHUM'), 40)
        self.assertEqual(temp.getDriver('BATLVL'), 100)
        self.assertEqual(temp.getDriver('ST'), 1)

        power = self.harness.ctl.devices['efer_16386']
        self.assertAlmostEqual(power.getDriver('CC'), 18.781)
        self.assertEqual(power.getDriver('BATLVL'), 0)

        tpms = self.harness.ctl.devices['hyun_5173f60b']
        self.assertAlmostEqual(tpms.getDriver('GV3'), 36.1)
        self.assertAlmostEqual(tpms.getDriver('CLITEMP'), 77.0)

    def test_repeat_packets_reuse_one_node(self):
        # One connection carrying repeats, the way rtl_433 actually streams.
        self.harness.send(*([LACROSSE] * 20))
        self.harness.wait_for_packets(20)
        self.harness.settle()
        self.assertEqual(len(self.harness.ctl.devices), 1)
        self.assertEqual(self.harness.ctl.devices['lacr_237_1'].packets, 20)

    def test_partial_packet_does_not_erase_previous_values(self):
        self.harness.send(LACROSSE)
        self.harness.wait_for_nodes(1)
        # A later transmission carrying only temperature must leave the
        # humidity reading from the earlier one intact.
        self.harness.send('{"model":"LaCrosse-TX141Bv3","id":237,'
                          '"channel":1,"temperature_C":22.0}')
        self.harness.wait_for_packets(2)
        self.harness.settle()
        self.harness.poll()

        node = self.harness.ctl.devices['lacr_237_1']
        self.assertAlmostEqual(node.getDriver('CLITEMP'), 71.6)
        self.assertEqual(node.getDriver('CLIHUM'), 40)

    def test_unclassified_device_lands_on_generic_node(self):
        self.harness.send('{"model":"Generic-Remote","id":8801,"cmd":12}')
        nodes = self.harness.wait_for_nodes(1)
        self.assertEqual(list(nodes.values())[0].id, 'rgen')

    def test_controller_counters(self):
        self.harness.send(LACROSSE, EFERGY)
        self.harness.wait_for_nodes(2)
        self.harness.poll()
        self.assertEqual(self.harness.ctl.getDriver('GV0'), 2)
        self.assertEqual(self.harness.ctl.getDriver('GV1'), 2)


class TestFreshness(unittest.TestCase):

    def test_node_goes_offline_after_timeout(self):
        harness = ControllerHarness(offline_timeout=1)
        try:
            harness.send(LACROSSE)
            harness.wait_for_nodes(1)
            harness.poll()
            node = harness.ctl.devices['lacr_237_1']
            self.assertEqual(node.getDriver('ST'), 1)

            time.sleep(1.2)
            harness.poll()
            self.assertEqual(node.getDriver('ST'), 0)
            self.assertGreaterEqual(node.getDriver('GV0'), 1)
            # The reading itself is retained, only the online flag drops.
            self.assertAlmostEqual(node.getDriver('CLITEMP'), 69.1)
        finally:
            harness.stop()


class TestUnitsParameter(unittest.TestCase):

    def test_celsius_mode_reports_native_values(self):
        harness = ControllerHarness(units='C')
        try:
            harness.send(LACROSSE, TPMS)
            harness.wait_for_nodes(2)
            harness.poll()

            temp = harness.ctl.devices['lacr_237_1']
            self.assertAlmostEqual(temp.getDriver('CLITEMP'), 20.6)
            self.assertEqual(
                next(d['uom'] for d in temp.drivers
                     if d['driver'] == 'CLITEMP'), units.UOM_C)

            tpms = harness.ctl.devices['hyun_5173f60b']
            self.assertAlmostEqual(tpms.getDriver('GV3'), 248.9)
            self.assertEqual(
                next(d['uom'] for d in tpms.drivers
                     if d['driver'] == 'GV3'), units.UOM_KPA)
        finally:
            harness.stop()


class TestFilters(unittest.TestCase):

    def test_ignore_models_drops_device(self):
        harness = ControllerHarness(ignore_models='LaCrosse-TX141Bv3')
        try:
            harness.send(LACROSSE, EFERGY)
            harness.wait_for_packets(2)
            harness.settle()
            self.assertEqual(list(harness.ctl.devices), ['efer_16386'])
            self.assertEqual(harness.ctl.dropped, 1)
        finally:
            harness.stop()

    def test_include_models_is_an_allow_list(self):
        harness = ControllerHarness(include_models='Efergy-e2CT')
        try:
            harness.send(LACROSSE, EFERGY, TPMS)
            harness.wait_for_packets(3)
            harness.settle()
            self.assertEqual(list(harness.ctl.devices), ['efer_16386'])
        finally:
            harness.stop()

    def test_ignore_ids_drops_device(self):
        harness = ControllerHarness(ignore_ids='237')
        try:
            harness.send(LACROSSE, EFERGY)
            harness.wait_for_packets(2)
            harness.settle()
            self.assertNotIn('lacr_237_1', harness.ctl.devices)
        finally:
            harness.stop()


class TestPersistence(unittest.TestCase):

    def test_registry_is_saved_and_restored(self):
        harness = ControllerHarness()
        try:
            harness.send(LACROSSE, TPMS)
            harness.wait_for_nodes(2)
            saved = json.loads(json.dumps(harness.ctl.Data['devices']))
            self.assertEqual(len(saved), 2)
        finally:
            harness.stop()

        # A fresh plugin start with the saved data must rebuild the same
        # nodes without waiting to hear the devices again.
        poly = stub_udi.Interface([])
        port = free_port()
        ctl = controller.Controller(poly, 'controller', 'controller', 'SDR')
        poly.fire(stub_udi.Interface.CUSTOMDATA, {'devices': saved})
        poly.fire(stub_udi.Interface.CUSTOMPARAMS,
                  {'port': port, 'bind': '127.0.0.1'})
        poly.fire(stub_udi.Interface.START)
        poly.fire(stub_udi.Interface.CONFIGDONE)
        try:
            self.assertEqual(sorted(ctl.devices),
                             ['hyun_5173f60b', 'lacr_237_1'])
            self.assertEqual(ctl.devices['hyun_5173f60b'].id, 'rtpms')
            # Restored but not yet heard from: offline until a packet lands.
            poly.fire(stub_udi.Interface.POLL, 'shortPoll')
            self.assertEqual(ctl.devices['lacr_237_1'].getDriver('ST'), 0)
        finally:
            poly.fire(stub_udi.Interface.STOP)


class TestImmediateMode(unittest.TestCase):

    def test_immediate_reports_without_waiting_for_poll(self):
        harness = ControllerHarness(immediate='true', min_interval=0)
        try:
            harness.send(LACROSSE)
            harness.wait_for_nodes(1)
            harness.settle()
            node = harness.ctl.devices['lacr_237_1']
            self.assertAlmostEqual(node.getDriver('CLITEMP'), 69.1)
        finally:
            harness.stop()


if __name__ == '__main__':
    unittest.main()
