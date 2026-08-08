#!/usr/bin/env python3
"""
EISY / Polyglot v3 plugin for rtl_433 SDR devices.

Receives line-delimited JSON from rtl_433 over a TCP connection and presents
each transmitting device as an IoX node.

Copyright (C) 2026 Peter Shipley
MIT License
"""

import sys

import udi_interface

from nodes import controller

LOGGER = udi_interface.LOGGER
VERSION = '1.0.0'

if __name__ == '__main__':
    try:
        polyglot = udi_interface.Interface([])
        polyglot.start(VERSION)

        controller.Controller(polyglot, 'controller', 'controller',
                              'SDR rtl_433')

        polyglot.runForever()
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
