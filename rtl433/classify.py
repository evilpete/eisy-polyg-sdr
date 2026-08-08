"""
Turn a raw rtl_433 JSON record into a normalized reading.

rtl_433 emits one JSON object per decoded transmission, and the field set
varies by decoder.  Rather than hard-coding a table of model names, devices
are classified by the fields they actually carry, so an unfamiliar sensor
still lands on a sensible node type.

Field names are matched case-insensitively: some rtl_433 builds and
post-processors emit "Humidity" rather than "humidity".

Pure standard library -- importable without udi_interface.
"""

import hashlib
import re

from . import units

# Device classes.  These strings are also the keys used to pick a node class
# in nodes/, and are persisted in the device registry, so do not rename them
# without a migration.
CLASS_THERMO = 'thermo'
CLASS_ENERGY = 'energy'
CLASS_TPMS = 'tpms'
CLASS_GENERIC = 'generic'

MAX_ADDRESS_LEN = 14


def normalize_keys(rec):
    """Lower-case every key in the record, preserving values."""
    return {str(k).lower(): v for k, v in rec.items()}


def _num(rec, *names):
    """First numerically-parseable value among names, else None."""
    for name in names:
        if name not in rec:
            continue
        try:
            return float(rec[name])
        except (TypeError, ValueError):
            continue
    return None


def classify(rec):
    """Pick a device class for an already key-normalized record."""
    if str(rec.get('type', '')).upper() == 'TPMS':
        return CLASS_TPMS
    if any(k in rec for k in ('pressure_kpa', 'pressure_psi', 'pressure_bar')):
        return CLASS_TPMS
    if any(k in rec for k in ('current', 'current_a', 'power_w', 'power',
                              'energy_kwh')):
        return CLASS_ENERGY
    if any(k in rec for k in ('temperature_c', 'temperature_f', 'humidity')):
        return CLASS_THERMO
    return CLASS_GENERIC


def battery_percent(rec):
    """Map rtl_433's assorted battery indicators onto 0-100."""
    for key in ('battery_ok', 'battery', 'maybe_battery'):
        if key not in rec:
            continue
        val = rec[key]
        # Some decoders report battery_ok as 0/1, others as "OK"/"LOW".
        if isinstance(val, str):
            token = val.strip().upper()
            if token in ('OK', 'GOOD', 'FULL', '1', 'TRUE'):
                return 100
            if token in ('LOW', 'BAD', 'EMPTY', '0', 'FALSE'):
                return 0
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        # A fractional value in 0..1 is a flag; anything larger is a percent.
        if 0.0 <= num <= 1.0:
            return int(round(num * 100))
        return int(max(0, min(100, round(num))))
    return None


def temperature_c(rec):
    """Canonical Celsius, converting from Fahrenheit when that is all we get."""
    deg_c = _num(rec, 'temperature_c', 'temperature')
    if deg_c is not None:
        return deg_c
    deg_f = _num(rec, 'temperature_f')
    if deg_f is not None:
        return units.f_to_c(deg_f)
    return None


def pressure_kpa(rec):
    """Canonical kPa across the three pressure spellings rtl_433 uses."""
    kpa = _num(rec, 'pressure_kpa')
    if kpa is not None:
        return kpa
    psi = _num(rec, 'pressure_psi')
    if psi is not None:
        return units.psi_to_kpa(psi)
    bar = _num(rec, 'pressure_bar')
    if bar is not None:
        return units.bar_to_kpa(bar)
    return None


def slug(value, limit=None):
    """Reduce a value to lower-case alphanumerics, optionally truncated."""
    out = re.sub(r'[^a-z0-9]', '', str(value).lower())
    if limit is not None:
        out = out[:limit]
    return out


def identity(rec):
    """
    Stable identity tuple for a device: (model, id, channel).

    id alone is not unique -- rtl_433 ids collide freely across decoders, and
    multi-channel sensors like the LaCrosse reuse an id per channel.
    """
    model = str(rec.get('model', 'unknown'))
    dev_id = rec.get('id')
    if dev_id is None:
        dev_id = rec.get('serial', rec.get('address', 'noid'))
    channel = rec.get('channel')
    channel = None if channel in (None, '') else str(channel)
    return (model, str(dev_id), channel)


def make_address(ident):
    """
    Derive an ISY node address from a device identity.

    ISY addresses are lower-case alphanumeric plus underscore and are kept to
    14 characters here, which is the conservative limit across IoX versions.
    Readable when it fits (``lacr_237_1``), hashed when it does not.
    """
    model, dev_id, channel = ident
    prefix = slug(model, 4) or 'dev'
    parts = [prefix, slug(dev_id) or 'noid']
    if channel is not None:
        parts.append(slug(channel) or '0')
    addr = '_'.join(parts)
    if len(addr) <= MAX_ADDRESS_LEN:
        return addr
    digest = hashlib.sha1(
        '|'.join(str(p) for p in ident).encode('utf-8')).hexdigest()[:8]
    return '{}_{}'.format(prefix, digest)[:MAX_ADDRESS_LEN]


def make_name(ident, device_class):
    """Human-readable default node name; the user can rename in the console."""
    model, dev_id, channel = ident
    name = '{} {}'.format(model, dev_id)
    if channel is not None:
        name += ' ch{}'.format(channel)
    if device_class == CLASS_TPMS:
        name = 'TPMS ' + name
    return name


def reading(rec):
    """
    Normalize a raw rtl_433 record into the fields the nodes consume.

    Returns a dict with canonical units (Celsius, kPa) plus the device class
    and identity.  Values absent from the record are omitted entirely rather
    than set to None, so a partial transmission never clobbers a good value
    that was cached from an earlier packet.
    """
    rec = normalize_keys(rec)
    ident = identity(rec)
    device_class = classify(rec)

    values = {}

    temp = temperature_c(rec)
    if temp is not None:
        values['temperature_c'] = temp

    humidity = _num(rec, 'humidity')
    if humidity is not None:
        values['humidity'] = humidity

    press = pressure_kpa(rec)
    if press is not None:
        values['pressure_kpa'] = press

    current = _num(rec, 'current', 'current_a')
    if current is not None:
        values['current_a'] = current

    power = _num(rec, 'power_w', 'power')
    if power is not None:
        values['power_w'] = power

    energy = _num(rec, 'energy_kwh')
    if energy is not None:
        values['energy_kwh'] = energy

    battery = battery_percent(rec)
    if battery is not None:
        values['battery'] = battery

    rssi = _num(rec, 'rssi')
    if rssi is not None:
        values['rssi'] = rssi

    return {
        'identity': ident,
        'model': ident[0],
        'id': ident[1],
        'channel': ident[2],
        'class': device_class,
        'values': values,
        'raw': rec,
    }
