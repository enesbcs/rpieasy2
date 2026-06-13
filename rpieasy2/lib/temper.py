#!/usr/bin/env python3
# temper.py -*-python-*-
# Copyright 2018 by Pham Urwen (urwen@mail.ru)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.

import binascii
import os
import re
import select
import struct

try:
    import serial
except ImportError:
    raise ImportError('Cannot import "serial". Please install python3-serial')


class USBList(object):
    SYSPATH = '/sys/bus/usb/devices'

    def _readfile(self, path):
        try:
            with open(path, 'r') as fp:
                return fp.read().strip()
        except Exception:
            return ''

    def _find_devices(self, dirname):
        devices = set()
        for entry in os.scandir(dirname):
            if entry.is_dir() and not entry.is_symlink():
                devices |= self._find_devices(os.path.join(dirname, entry.name))
            if re.search(r'tty.*[0-9]', entry.name):
                devices.add(entry.name)
            if re.search(r'hidraw[0-9]', entry.name):
                devices.add(entry.name)
        return devices

    def _get_usb_device(self, dirname):
        info: dict = {}
        vendorid = self._readfile(os.path.join(dirname, 'idVendor'))
        if vendorid == '':
            return None
        info['vendorid'] = int(vendorid, 16)
        productid = self._readfile(os.path.join(dirname, 'idProduct'))
        info['productid'] = int(productid, 16)
        info['manufacturer'] = self._readfile(os.path.join(dirname, 'manufacturer'))
        info['product'] = self._readfile(os.path.join(dirname, 'product'))
        info['busnum'] = int(self._readfile(os.path.join(dirname, 'busnum')))
        info['devnum'] = int(self._readfile(os.path.join(dirname, 'devnum')))
        info['devices'] = sorted(self._find_devices(dirname))
        return info

    def get_usb_devices(self):
        info: dict = {}
        for entry in os.scandir(Temper.SYSPATH):
            if entry.is_dir():
                path = os.path.join(Temper.SYSPATH, entry.name)
                device = self._get_usb_device(path)
                if device is not None:
                    device['port'] = entry.name
                    info[path] = device
        return info


class USBRead(object):
    def __init__(self, device, verbose=False):
        self.device = device
        self.verbose = verbose

    def _parse_bytes(self, name, offset, divisor, bytes_data, info, verbose=False):
        try:
            if bytes_data[offset] == 0x4e and bytes_data[offset + 1] == 0x20:
                return
        except Exception:
            return
        try:
            if verbose:
                print('Converted value: %s' % binascii.hexlify(bytes_data[offset:offset + 2]))
            info[name] = struct.unpack_from('>h', bytes_data, offset)[0] / divisor
        except Exception:
            return

    def _read_hidraw_firmware(self, fd, verbose=False):
        query = struct.pack('8B', 0x01, 0x86, 0xff, 0x01, 0, 0, 0, 0)
        if verbose:
            print('Firmware query: %s' % binascii.b2a_hex(query))
        for i in range(0, 10):
            os.write(fd, query)
            firmware = b''
            while True:
                r, _, _ = select.select([fd], [], [], 0.2)
                if fd not in r:
                    break
                data = os.read(fd, 8)
                firmware += data
            if not len(firmware):
                os.close(fd)
                raise RuntimeError('Cannot read device firmware identifier')
            if len(firmware) > 8:
                break
        if verbose:
            print('Firmware value: %s %s' % (binascii.b2a_hex(firmware), firmware.decode()))
        return firmware

    def _read_hidraw(self, device):
        path = os.path.join('/dev', device)
        fd = os.open(path, os.O_RDWR)
        firmware = self._read_hidraw_firmware(fd, self.verbose)
        os.write(fd, struct.pack('8B', 0x01, 0x80, 0x33, 0x01, 0, 0, 0, 0))
        bytes_data = b''
        while True:
            r, _, _ = select.select([fd], [], [], 0.1)
            if fd not in r:
                break
            data = os.read(fd, 8)
            bytes_data += data
        os.close(fd)
        if self.verbose:
            print('Data value: %s' % binascii.hexlify(bytes_data))
        info: dict = {}
        info['firmware'] = str(firmware, 'latin-1').strip()
        info['hex_firmware'] = str(binascii.b2a_hex(firmware), 'latin-1')
        info['hex_data'] = str(binascii.b2a_hex(bytes_data), 'latin-1')
        if info['firmware'][:10] in ['TEMPerF1.2', 'TEMPerF1.4', 'TEMPer1F1.']:
            info['firmware'] = info['firmware'][:10]
            self._parse_bytes('internal temperature', 2, 256.0, bytes_data, info)
            return info
        if info['firmware'][:15] in ['TEMPerGold_V3.1', 'TEMPerGold_V3.3', 'TEMPerGold_V3.4', 'TEMPerGold_V3.5']:
            info['firmware'] = info['firmware'][:15]
            self._parse_bytes('internal temperature', 2, 100.0, bytes_data, info)
            return info
        if info['firmware'][:12] in ['TEMPerX_V3.1', 'TEMPerX_V3.3']:
            info['firmware'] = info['firmware'][:12]
            self._parse_bytes('internal temperature', 2, 100.0, bytes_data, info)
            self._parse_bytes('internal humidity', 4, 100.0, bytes_data, info)
            self._parse_bytes('external temperature', 10, 100.0, bytes_data, info)
            self._parse_bytes('external humidity', 12, 100.0, bytes_data, info)
            return info
        if info['firmware'][:16] == 'TEMPer2_M12_V1.3':
            info['firmware'] = info['firmware'][:16]
            self._parse_bytes('internal temperature', 2, 256.0, bytes_data, info)
            self._parse_bytes('external temperature', 4, 256.0, bytes_data, info)
            return info
        if info['firmware'][:12] in ['TEMPer2_V3.7', 'TEMPer2_V3.9']:
            info['firmware'] = info['firmware'][:12]
            self._parse_bytes('internal temperature', 2, 100.0, bytes_data, info, self.verbose)
            self._parse_bytes('external temperature', 10, 100.0, bytes_data, info, self.verbose)
            return info
        if info['firmware'][:14] == 'TEMPerHUM_V3.9':
            info['firmware'] = info['firmware'][:14]
            self._parse_bytes('internal temperature', 2, 100.0, bytes_data, info, self.verbose)
            self._parse_bytes('external temperature', 10, 100.0, bytes_data, info, self.verbose)
            self._parse_bytes('internal humidity', 4, 100.0, bytes_data, info)
            return info
        if info['firmware'][:16] == 'TEMPer1F_H1V1.5F':
            info['firmware'] = info['firmware'][:16]
            self._parse_bytes('internal temperature', 2, 1, bytes_data, info, verbose=self.verbose)
            self._parse_bytes('internal humidity', 4, 1, bytes_data, info, verbose=self.verbose)
            t = int(info['internal temperature']) << 2
            if self.verbose:
                print('Raw temperature: %d' % t)
            t = -46.85 + 175.72 * t / 65536
            info['internal temperature'] = t
            h = int(info['internal humidity']) << 4
            if self.verbose:
                print('Raw humidity: %d' % h)
            h = -6 + 125.0 * h / 65536
            info['internal humidity'] = h
            return info
        if info['firmware'][:12] == 'TEMPer2_V4.1':
            info['firmware'] = info['firmware'][:12]
            self._parse_bytes('internal temperature', 2, 100.0, bytes_data, info)
            self._parse_bytes('external temperature', 10, 100.0, bytes_data, info, self.verbose)
            return info
        if info['firmware'][:13] in ['TEMPer1F_V3.9', 'TEMPer1F_V4.1']:
            info['firmware'] = info['firmware'][:13]
            self._parse_bytes('internal temperature', 2, 100.0, bytes_data, info, self.verbose)
            return info
        info['error'] = 'Unknown firmware %s: %s' % (info['firmware'], binascii.hexlify(bytes_data))
        return info

    def _read_serial(self, device):
        path = os.path.join('/dev', device)
        s = serial.Serial(path, 9600)
        s.bytesize = serial.EIGHTBITS
        s.parity = serial.PARITY_NONE
        s.stopbits = serial.STOPBITS_ONE
        s.timeout = 1
        s.xonoff = False
        s.rtscts = False
        s.dsrdtr = False
        s.writeTimeout = 0
        s.write(b'Version')
        firmware = str(s.readline(), 'latin-1').strip()
        s.write(b'ReadTemp')
        reply = str(s.readline(), 'latin-1').strip()
        reply += str(s.readline(), 'latin-1').strip()
        s.close()
        info: dict = {}
        info['firmware'] = firmware
        m = re.search(r'Temp-Inner:(-?[0-9.]+).*, ?(-?[0-9.\-]*)', reply)
        if m is not None:
            info['internal temperature'] = float(m.group(1))
            info['internal humidity'] = float(m.group(2))
        m = re.search(r'Temp-Outer:(-?[0-9.]+).*?, ?(-?[0-9.\-]*)', reply)
        if m is not None:
            try:
                info['external temperature'] = float(m.group(1))
                info['external humidity'] = float(m.group(2))
            except Exception:
                pass
        return info

    def read(self):
        if self.device.startswith('hidraw'):
            return self._read_hidraw(self.device)
        if self.device.startswith('tty'):
            return self._read_serial(self.device)
        return {'error': 'No usable hid/tty devices available'}


class Temper(object):
    SYSPATH = '/sys/bus/usb/devices'

    def __init__(self, verbose=False):
        usblist = USBList()
        self.usb_devices = usblist.get_usb_devices()
        self.forced_vendor_id = None
        self.forced_product_id = None
        self.verbose = verbose

    def _is_known_id(self, vendorid, productid):
        if self.forced_vendor_id is not None and self.forced_product_id is not None:
            return self.forced_vendor_id == vendorid and self.forced_product_id == productid
        if vendorid == 0x0c45 and (productid == 0x7401 or productid == 0x7402):
            return True
        if vendorid == 0x413d and productid == 0x2107:
            return True
        if vendorid == 0x1a86 and productid == 0x5523:
            return True
        if vendorid == 0x1a86 and productid == 0xe025:
            return True
        if vendorid == 0x3553 and productid == 0xa001:
            return True
        return False

    def read(self, verbose=False):
        results = []
        for _, info in sorted(self.usb_devices.items(), key=lambda x: x[1]['busnum'] * 1000 + x[1]['devnum']):
            if not self._is_known_id(info['vendorid'], info['productid']):
                continue
            if len(info['devices']) == 0:
                info['error'] = 'no hid/tty devices available'
                results.append(info)
                continue
            usbread = USBRead(info['devices'][-1], verbose)
            results.append({**info, **usbread.read()})
        return results
