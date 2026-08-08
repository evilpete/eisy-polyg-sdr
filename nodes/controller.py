"""
Controller node: owns configuration, the TCP listener, and the device
registry that maps rtl_433 transmissions onto IoX nodes.

Flow of a packet:

    listener thread  ->  ingest()      classify, filter, cache on the node
    worker thread    ->  _creator()    create nodes for devices not seen yet
    poll thread      ->  poll()        flush cached values to IoX

Node creation is deliberately serialized on one worker: addNode() is
asynchronous and has to be awaited, and several new devices can easily be
heard within the same second at startup.
"""

import queue
import threading
import time

import udi_interface

from rtl433 import classify, listener, units
from nodes import sensor

LOGGER = udi_interface.LOGGER
Custom = udi_interface.Custom

DEFAULTS = {
    'port': 1433,
    'bind': '0.0.0.0',
    'units': units.UNITS_US,
    'offline_timeout': 3600,
    'immediate': False,
    'min_interval': 10,
    'min_rssi': None,
    'ignore_models': [],
    'ignore_ids': [],
    'include_models': [],
    'allow_from': [],
}


def _as_list(value):
    if value is None:
        return []
    return [item.strip() for item in str(value).split(',') if item.strip()]


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on', 'y')


def _as_int(value, default, name, notices):
    if value is None or str(value).strip() == '':
        return default
    try:
        return int(str(value).strip())
    except ValueError:
        notices[name] = "'{}' must be a whole number, using {}".format(
            name, default)
        return default


def _as_float(value, default, name, notices):
    if value is None or str(value).strip() == '':
        return default
    try:
        return float(str(value).strip())
    except ValueError:
        notices[name] = "'{}' must be a number, ignoring".format(name)
        return default


class Controller(udi_interface.Node):
    id = 'ctl'
    drivers = [
        {'driver': 'ST', 'value': 0, 'uom': units.UOM_BOOL},
        {'driver': 'GV0', 'value': 0, 'uom': units.UOM_RAW},
        {'driver': 'GV1', 'value': 0, 'uom': units.UOM_RAW},
        {'driver': 'GV2', 'value': 0, 'uom': units.UOM_RAW},
        {'driver': 'GV3', 'value': 0, 'uom': units.UOM_RAW},
    ]

    def __init__(self, polyglot, primary, address, name):
        super(Controller, self).__init__(polyglot, primary, address, name)

        self.poly = polyglot
        self.lock = threading.Lock()
        self.n_queue = []

        self.Parameters = Custom(polyglot, 'customparams')
        self.Data = Custom(polyglot, 'customdata')

        self.config = dict(DEFAULTS)
        self.listener = listener.Listener(LOGGER)

        self.devices = {}          # address -> node object
        self.registry = {}         # address -> persisted device description
        self.by_identity = {}      # (model, id, channel) -> address
        self.staged = {}           # address -> reading awaiting node creation
        self.pending = queue.Queue()

        self.packets = 0
        self.dropped = 0
        self.stopping = False
        self.worker = None
        self.config_done = False

        polyglot.subscribe(polyglot.CUSTOMPARAMS, self.parameterHandler)
        polyglot.subscribe(polyglot.CUSTOMDATA, self.dataHandler)
        polyglot.subscribe(polyglot.START, self.start, address)
        polyglot.subscribe(polyglot.CONFIGDONE, self.configDone)
        polyglot.subscribe(polyglot.POLL, self.poll)
        polyglot.subscribe(polyglot.STOP, self.stop)
        polyglot.subscribe(polyglot.ADDNODEDONE, self.node_queue)
        polyglot.subscribe(polyglot.DISCOVER, self.discover)

        polyglot.ready()
        self.poly.addNode(self)

    # ------------------------------------------------------------------
    # node add synchronization
    # ------------------------------------------------------------------

    def node_queue(self, data):
        self.n_queue.append(data.get('address'))

    def wait_for_node_done(self, timeout=10):
        waited = 0.0
        while not self.n_queue and waited < timeout:
            time.sleep(0.1)
            waited += 0.1
        if self.n_queue:
            self.n_queue.pop()
            return True
        LOGGER.warning('Timed out waiting for node add to complete')
        return False

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------

    def parameterHandler(self, params):
        self.Parameters.load(params)
        notices = {}

        cfg = dict(DEFAULTS)
        cfg['port'] = _as_int(self.Parameters['port'], DEFAULTS['port'],
                              'port', notices)
        cfg['bind'] = (self.Parameters['bind'] or DEFAULTS['bind']).strip()
        cfg['units'] = units.normalize_units(self.Parameters['units'])
        cfg['offline_timeout'] = _as_int(self.Parameters['offline_timeout'],
                                         DEFAULTS['offline_timeout'],
                                         'offline_timeout', notices)
        cfg['immediate'] = _as_bool(self.Parameters['immediate'])
        cfg['min_interval'] = _as_int(self.Parameters['min_interval'],
                                      DEFAULTS['min_interval'],
                                      'min_interval', notices)
        cfg['min_rssi'] = _as_float(self.Parameters['min_rssi'], None,
                                    'min_rssi', notices)
        cfg['ignore_models'] = [m.lower() for m in
                                _as_list(self.Parameters['ignore_models'])]
        cfg['ignore_ids'] = [i.lower() for i in
                             _as_list(self.Parameters['ignore_ids'])]
        cfg['include_models'] = [m.lower() for m in
                                 _as_list(self.Parameters['include_models'])]
        cfg['allow_from'] = _as_list(self.Parameters['allow_from'])

        if not 1 <= cfg['port'] <= 65535:
            notices['port'] = 'port must be between 1 and 65535, using {}'.format(
                DEFAULTS['port'])
            cfg['port'] = DEFAULTS['port']

        rebind = (cfg['port'], cfg['bind'], cfg['allow_from']) != (
            self.config['port'], self.config['bind'], self.config['allow_from'])
        self.config = cfg

        self.poly.Notices.clear()
        for key, text in notices.items():
            self.poly.Notices[key] = text

        LOGGER.info('Configuration: listening on %s:%s, units=%s, '
                    'offline after %ss, immediate=%s',
                    cfg['bind'], cfg['port'], cfg['units'],
                    cfg['offline_timeout'], cfg['immediate'])

        if self.config_done and (rebind or not self.listener.is_running()):
            self._start_listener()

    def dataHandler(self, data):
        self.Data.load(data)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self):
        LOGGER.info('Starting rtl_433 SDR plugin')
        self.poly.updateProfile()
        self.poly.setCustomParamsDoc()
        self.setDriver('ST', 1, True, True)

        self.worker = threading.Thread(target=self._creator,
                                       name='rtl433-creator')
        self.worker.daemon = True
        self.worker.start()

    def configDone(self):
        """Config and saved data are both present; safe to build nodes."""
        self.config_done = True
        self._restore_devices()
        self._start_listener()

    def _start_listener(self):
        ok = self.listener.start(self.config['bind'], self.config['port'],
                                 self.ingest, self.config['allow_from'])
        if ok:
            self.poly.Notices.delete('listener')
        else:
            self.poly.Notices['listener'] = (
                'Could not bind {}:{} -- is another process using that '
                'port?'.format(self.config['bind'], self.config['port']))

    def stop(self):
        LOGGER.info('Stopping rtl_433 SDR plugin')
        self.stopping = True
        self.listener.stop()
        if self.worker is not None:
            self.worker.join(timeout=3)
        for node in list(self.devices.values()):
            node.setDriver('ST', 0, True, True)
        self.setDriver('ST', 0, True, True)
        self.poly.stop()

    # ------------------------------------------------------------------
    # device registry
    # ------------------------------------------------------------------

    def _restore_devices(self):
        """Recreate node objects for devices discovered in earlier runs."""
        saved = self.Data['devices'] or {}
        if not saved:
            LOGGER.info('No previously discovered devices to restore')
            return

        LOGGER.info('Restoring %d previously discovered device(s)', len(saved))
        for address, info in saved.items():
            ident = tuple(info.get('identity', (info.get('model', 'unknown'),
                                                info.get('id', 'noid'),
                                                info.get('channel'))))
            self.registry[address] = {
                'identity': list(ident),
                'class': info.get('class', classify.CLASS_GENERIC),
                'name': info.get('name', address),
            }
            self.by_identity[ident] = address
            self._add_node(address)

    def _save_registry(self):
        self.Data['devices'] = dict(self.registry)

    def _address_for(self, reading):
        """Find or allocate the node address for a device identity."""
        ident = reading['identity']
        with self.lock:
            address = self.by_identity.get(ident)
            if address is not None:
                return address

            base = classify.make_address(ident)
            address = base
            suffix = 1
            while address in self.registry:
                # Truncated addresses can collide across models; disambiguate.
                tail = '_{}'.format(suffix)
                address = base[:classify.MAX_ADDRESS_LEN - len(tail)] + tail
                suffix += 1

            self.registry[address] = {
                'identity': list(ident),
                'class': reading['class'],
                'name': classify.make_name(ident, reading['class']),
            }
            self.by_identity[ident] = address

        LOGGER.info('New device heard: %s -> node %s (%s)',
                    classify.make_name(ident, reading['class']), address,
                    reading['class'])
        self._save_registry()
        return address

    def _add_node(self, address):
        """Instantiate and register the node object for a known address."""
        info = self.registry[address]
        node_class = sensor.node_class_for(info['class'])
        name = self.poly.getValidName(info['name'])
        try:
            node = node_class(self.poly, self.address, address, name)
            self.poly.addNode(node)
            self.wait_for_node_done()
            self.devices[address] = node
            return node
        except Exception:
            LOGGER.exception('Failed to create node %s (%s)', address, name)
            return None

    # ------------------------------------------------------------------
    # ingest
    # ------------------------------------------------------------------

    def ingest(self, record):
        """Handle one decoded rtl_433 record.  Runs on a listener thread."""
        with self.lock:
            self.packets += 1

        try:
            reading = classify.reading(record)
        except Exception:
            LOGGER.exception('Could not interpret record: %.200s', record)
            with self.lock:
                self.dropped += 1
            return

        if not self._accepted(reading):
            with self.lock:
                self.dropped += 1
            return

        address = self._address_for(reading)
        node = self.devices.get(address)

        if node is None:
            self._stage(address, reading)
            return

        node.ingest(reading)
        self._maybe_report_now(node)

    def _accepted(self, reading):
        """Apply the user's filters to a reading."""
        cfg = self.config
        model = reading['model'].lower()
        dev_id = str(reading['id']).lower()

        if cfg['include_models'] and model not in cfg['include_models']:
            return False
        if model in cfg['ignore_models']:
            return False
        if dev_id in cfg['ignore_ids']:
            return False
        if cfg['min_rssi'] is not None:
            rssi = reading['values'].get('rssi')
            if rssi is not None and rssi < cfg['min_rssi']:
                return False
        return True

    def _stage(self, address, reading):
        """
        Hold a reading until its node exists, merging repeat packets.

        Merged packets are counted so the node's packet total reflects every
        transmission heard, not just the one that survived the merge.
        """
        with self.lock:
            staged = self.staged.get(address)
            if staged is None:
                reading['count'] = 1
                self.staged[address] = reading
                is_new = True
            else:
                staged['values'].update(reading['values'])
                staged['count'] += 1
                is_new = False
        if is_new:
            self.pending.put(address)

    def _creator(self):
        """Serialize node creation for newly heard devices."""
        while not self.stopping:
            try:
                address = self.pending.get(timeout=1)
            except queue.Empty:
                continue

            with self.lock:
                reading = self.staged.pop(address, None)

            try:
                node = self.devices.get(address)
                if node is None:
                    node = self._add_node(address)
                if node is not None and reading is not None:
                    node.ingest(reading, reading.get('count', 1))
                    self._maybe_report_now(node)
            except Exception:
                LOGGER.exception('Error creating node %s', address)

    def _maybe_report_now(self, node):
        """Optional low-latency path, rate limited per node."""
        if not self.config['immediate']:
            return
        now = time.time()
        if now - node.last_report < self.config['min_interval']:
            return
        node.report(self.config['units'], self.config['offline_timeout'])

    # ------------------------------------------------------------------
    # polling
    # ------------------------------------------------------------------

    def poll(self, polltype):
        if 'shortPoll' in polltype:
            self._flush(force=False)
            self._report_stats()
        elif 'longPoll' in polltype:
            # Periodic full refresh so IoX cannot drift out of sync with us.
            self._flush(force=True)

    def _flush(self, force=False):
        for address, node in list(self.devices.items()):
            try:
                # Reported unconditionally: setDriver only sends to IoX when a
                # value actually changed, and the age/online drivers move on
                # every poll whether or not a packet arrived.
                node.report(self.config['units'],
                            self.config['offline_timeout'], force)
            except Exception:
                LOGGER.exception('Error reporting node %s', address)

    def _report_stats(self):
        with self.lock:
            packets, dropped = self.packets, self.dropped
        self.setDriver('GV0', len(self.devices), True, False)
        self.setDriver('GV1', packets, True, False)
        self.setDriver('GV2', self.listener.active_connections, True, False)
        self.setDriver('GV3', dropped + self.listener.errors, True, False)

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------

    def discover(self, command=None):
        """Re-send every driver for every node."""
        LOGGER.info('Refreshing all %d node(s)', len(self.devices))
        self._flush(force=True)
        self._report_stats()

    def query(self, command=None):
        self.discover()
        self.reportDrivers()

    commands = {
        'DISCOVER': discover,
        'QUERY': query,
    }
