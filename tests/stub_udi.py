"""
Minimal stand-in for udi_interface, so the controller can be exercised off
the EISY.

It mirrors the parts of the real interface this plugin depends on -- the
event subscription model, the per-instance deep copy of drivers, the
setDriver signature including the uom override, and the asynchronous
addNode/ADDNODEDONE handshake.  It is a test double, not an emulator: it
does not talk to IoX and makes no attempt to cover the rest of the API.
"""

import copy
import logging

LOGGER = logging.getLogger('stub_udi')


class Custom(dict):
    """Stands in for the persisted Custom stores (params, data, notices)."""

    def __init__(self, poly, name):
        super().__init__()
        self.poly = poly
        self.name = name

    def __getitem__(self, key):
        return self.get(key)

    def load(self, data, save=False):
        if data:
            self.update(data)

    def delete(self, key):
        self.pop(key, None)

    def dump(self):
        return dict(self)


class Node:
    id = 'node'
    drivers = []
    commands = {}

    def __init__(self, poly, primary, address, name):
        self.poly = poly
        self.primary = primary
        self.address = address
        self.name = name
        self.drivers = copy.deepcopy(self.drivers)
        self.reports = []

    def getDriver(self, driver):
        for entry in self.drivers:
            if entry['driver'] == driver:
                return entry['value']
        return None

    def setDriver(self, driver, value, report=True, force=False, uom=None,
                  text=None):
        entry = next((d for d in self.drivers if d['driver'] == driver), None)
        if entry is None:
            raise AssertionError(
                '{}: driver {} is not declared for nodedef {}'.format(
                    self.address, driver, self.id))
        changed = False
        if uom is not None and entry['uom'] != uom:
            entry['uom'] = uom
            changed = True
        if entry['value'] != value:
            entry['value'] = value
            changed = True
        if report and (changed or force):
            self.reports.append((driver, value, entry['uom']))
        return changed

    def reportDrivers(self):
        for entry in self.drivers:
            self.reports.append((entry['driver'], entry['value'],
                                 entry['uom']))


class Interface:
    CONFIG = 'config'
    START = 'start'
    STARTDONE = 'startdone'
    STOP = 'stop'
    DELETE = 'delete'
    ADDNODEDONE = 'addnodedone'
    DELNODEDONE = 'delnodedone'
    CUSTOMDATA = 'customdata'
    CUSTOMPARAMS = 'customparams'
    CUSTOMNS = 'customns'
    NOTICES = 'notices'
    POLL = 'poll'
    LOGLEVEL = 'loglevel'
    ISY = 'isy'
    CONFIGDONE = 'configdone'
    DISCOVER = 'discover'

    def __init__(self, classes=None):
        self.handlers = {}
        self.nodes_by_address = {}
        self.Notices = Custom(self, 'notices')
        self.started = False
        self.stopped = False
        self.profile_updated = False

    # -- interface API used by the plugin -----------------------------

    def start(self, version=None):
        self.started = True

    def ready(self):
        pass

    def subscribe(self, event, callback, address=None):
        self.handlers.setdefault(event, []).append(callback)

    def addNode(self, node, conn_status=None, rename=False):
        self.nodes_by_address[node.address] = node
        for handler in self.handlers.get(self.ADDNODEDONE, []):
            handler({'address': node.address})

    def getNodes(self):
        return self.nodes_by_address

    def getNode(self, address):
        return self.nodes_by_address.get(address)

    def delNode(self, address):
        self.nodes_by_address.pop(address, None)

    def getValidName(self, name):
        return name

    def getValidAddress(self, address):
        return address

    def updateProfile(self):
        self.profile_updated = True

    def setCustomParamsDoc(self, doc=None):
        pass

    def stop(self):
        self.stopped = True

    def runForever(self):
        raise NotImplementedError('not used in tests')

    # -- test helpers -------------------------------------------------

    def fire(self, event, *args):
        for handler in list(self.handlers.get(event, [])):
            handler(*args)


def install():
    """Register this module as 'udi_interface' before the plugin imports it."""
    import sys
    import types

    module = types.ModuleType('udi_interface')
    module.LOGGER = LOGGER
    module.Custom = Custom
    module.Node = Node
    module.Interface = Interface
    sys.modules['udi_interface'] = module
    return module
