"""
Unit conversion and ISY UOM constants.

Pure standard library -- this module is importable without udi_interface so
that it can be exercised by the offline tests in tests/.
"""

# ISY units of measure used by this plugin.
UOM_AMP = 1
UOM_BOOL = 2
UOM_C = 4
UOM_F = 17
UOM_PERCENT = 22       # relative humidity
UOM_KPA = 31
UOM_KWH = 33
UOM_BATTERY = 51       # percent, used for BATLVL
UOM_RAW = 56           # unitless
UOM_SECONDS = 58
UOM_WATT = 73
UOM_PSI = 138

# Units mode strings accepted in the 'units' custom parameter.
UNITS_US = 'F'
UNITS_METRIC = 'C'


def c_to_f(deg_c):
    return (deg_c * 9.0 / 5.0) + 32.0


def f_to_c(deg_f):
    return (deg_f - 32.0) * 5.0 / 9.0


def kpa_to_psi(kpa):
    return kpa * 0.14503773773


def psi_to_kpa(psi):
    return psi / 0.14503773773


def bar_to_kpa(bar):
    return bar * 100.0


def temperature_out(deg_c, units):
    """Canonical Celsius -> (value, uom) in the configured display units."""
    if deg_c is None:
        return None, None
    if units == UNITS_METRIC:
        return round(deg_c, 1), UOM_C
    return round(c_to_f(deg_c), 1), UOM_F


def pressure_out(kpa, units):
    """Canonical kPa -> (value, uom) in the configured display units."""
    if kpa is None:
        return None, None
    if units == UNITS_METRIC:
        return round(kpa, 1), UOM_KPA
    return round(kpa_to_psi(kpa), 1), UOM_PSI


def normalize_units(value, default=UNITS_US):
    """Coerce a user-entered units parameter into 'F' or 'C'."""
    if value is None:
        return default
    v = str(value).strip().upper()
    if v in ('C', 'CELSIUS', 'METRIC', 'SI'):
        return UNITS_METRIC
    if v in ('F', 'FAHRENHEIT', 'US', 'IMPERIAL'):
        return UNITS_US
    return default
