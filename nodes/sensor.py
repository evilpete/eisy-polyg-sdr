"""
Node classes for the device classes rtl_433 feeds us.

One class per capability set rather than per model: a LaCrosse temperature
sensor and any other temperature/humidity device share ThermoNode, so a new
sensor on the same band works without touching the profile.

Values arrive on listener threads and are reported on the poll thread, so
every reading is cached under a lock and flushed on shortPoll.  This keeps a
chatty device (the Efergy transmits every 6 seconds, TPMS sends bursts of
repeats) from flooding IoX with driver updates.
"""

import threading
import time

import udi_interface

from rtl433 import classify, units

LOGGER = udi_interface.LOGGER


class RtlNode(udi_interface.Node):
    """Common behavior: freshness tracking, battery, signal, packet counts."""

    id = 'rgen'
    drivers = [
        {'driver': 'ST', 'value': 0, 'uom': units.UOM_BOOL},
        {'driver': 'GV0', 'value': 0, 'uom': units.UOM_SECONDS},
        {'driver': 'GV1', 'value': 0, 'uom': units.UOM_RAW},
        {'driver': 'GV2', 'value': 0, 'uom': units.UOM_RAW},
        {'driver': 'BATLVL', 'value': 0, 'uom': units.UOM_BATTERY},
    ]

    def __init__(self, polyglot, primary, address, name):
        super(RtlNode, self).__init__(polyglot, primary, address, name)
        self.poly = polyglot
        self.lock = threading.Lock()
        self.values = {}
        self.last_seen = None
        self.last_report = 0.0
        self.packets = 0
        self.dirty = False

    def ingest(self, reading, count=1):
        """
        Merge a new reading.  Called from a listener thread.

        ``count`` is the number of transmissions this reading represents.
        It is greater than one when packets arrived while the node was still
        being created and were merged, so the counter stays honest.
        """
        with self.lock:
            # Merge rather than replace: a decoder that omits humidity on one
            # transmission must not erase the value from the previous one.
            self.values.update(reading['values'])
            self.last_seen = time.time()
            self.packets += count
            self.dirty = True

    def snapshot(self):
        with self.lock:
            return dict(self.values), self.last_seen, self.packets

    def report(self, unit_mode, offline_timeout, force=False):
        """Push cached values to IoX.  Called from the poll thread."""
        values, last_seen, packets = self.snapshot()
        now = time.time()

        if last_seen is None:
            age = 0
            online = 0
        else:
            elapsed = now - last_seen
            # Decide from the real elapsed time; the reported age is the
            # truncated whole seconds and would go offline a second late.
            age = int(elapsed)
            online = 1 if elapsed <= offline_timeout else 0

        self.setDriver('ST', online, True, force)
        self.setDriver('GV0', age, True, force)
        self.setDriver('GV1', packets, True, force)

        if 'rssi' in values:
            self.setDriver('GV2', round(values['rssi'], 1), True, force)
        if 'battery' in values:
            self.setDriver('BATLVL', values['battery'], True, force)

        self.report_values(values, unit_mode, force)

        self.last_report = now
        with self.lock:
            self.dirty = False

    def report_values(self, values, unit_mode, force):
        """Subclass hook for the class-specific drivers."""

    def query(self, command=None):
        """Admin console 'Query' -- re-send everything we currently hold."""
        self.reportDrivers()

    commands = {'QUERY': query}


class ThermoNode(RtlNode):
    """Temperature and/or humidity sensor."""

    id = 'rtemp'
    drivers = RtlNode.drivers + [
        {'driver': 'CLITEMP', 'value': 0, 'uom': units.UOM_F},
        {'driver': 'CLIHUM', 'value': 0, 'uom': units.UOM_PERCENT},
    ]

    def report_values(self, values, unit_mode, force):
        if 'temperature_c' in values:
            value, uom = units.temperature_out(values['temperature_c'],
                                               unit_mode)
            self.setDriver('CLITEMP', value, True, force, uom)
        if 'humidity' in values:
            self.setDriver('CLIHUM', round(values['humidity']), True, force)


class EnergyNode(RtlNode):
    """Whole-house energy monitor: current, and power/energy when reported."""

    id = 'rpower'
    drivers = RtlNode.drivers + [
        {'driver': 'CC', 'value': 0, 'uom': units.UOM_AMP},
        {'driver': 'CPW', 'value': 0, 'uom': units.UOM_WATT},
        {'driver': 'TPW', 'value': 0, 'uom': units.UOM_KWH},
    ]

    def report_values(self, values, unit_mode, force):
        if 'current_a' in values:
            self.setDriver('CC', round(values['current_a'], 3), True, force)
        if 'power_w' in values:
            self.setDriver('CPW', round(values['power_w'], 1), True, force)
        if 'energy_kwh' in values:
            self.setDriver('TPW', round(values['energy_kwh'], 3), True, force)


class TpmsNode(RtlNode):
    """Tire pressure monitor: pressure plus the tire's temperature."""

    id = 'rtpms'
    drivers = RtlNode.drivers + [
        {'driver': 'GV3', 'value': 0, 'uom': units.UOM_PSI},
        {'driver': 'CLITEMP', 'value': 0, 'uom': units.UOM_F},
    ]

    def report_values(self, values, unit_mode, force):
        if 'pressure_kpa' in values:
            value, uom = units.pressure_out(values['pressure_kpa'], unit_mode)
            self.setDriver('GV3', value, True, force, uom)
        if 'temperature_c' in values:
            value, uom = units.temperature_out(values['temperature_c'],
                                               unit_mode)
            self.setDriver('CLITEMP', value, True, force, uom)


class GenericNode(RtlNode):
    """Anything we can hear but cannot classify -- still tracked and counted."""

    id = 'rgen'


NODE_CLASSES = {
    classify.CLASS_THERMO: ThermoNode,
    classify.CLASS_ENERGY: EnergyNode,
    classify.CLASS_TPMS: TpmsNode,
    classify.CLASS_GENERIC: GenericNode,
}


def node_class_for(device_class):
    return NODE_CLASSES.get(device_class, GenericNode)
