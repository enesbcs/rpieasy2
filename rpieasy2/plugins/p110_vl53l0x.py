from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import (
    DEVICE_TYPE_I2C,
    SENSOR_TYPE_SINGLE,
    SENSOR_V_TYPE_DISTANCE,
    SENSOR_V_TYPE_SINGLE,
)
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p110")

VL53L0X_ADDRS = [0x29, 0x30]

_SYSRANGE_START = 0x00
_SYSTEM_THRESH_HIGH = 0x0C
_SYSTEM_THRESH_LOW = 0x0E
_SYSTEM_SEQUENCE_CONFIG = 0x01
_SYSTEM_RANGE_CONFIG = 0x09
_SYSTEM_INTERMEASUREMENT_PERIOD = 0x04
_SYSTEM_INTERRUPT_CONFIG_GPIO = 0x0A
_GPIO_HV_MUX_ACTIVE_HIGH = 0x84
_SYSTEM_INTERRUPT_CLEAR = 0x0B
_RESULT_INTERRUPT_STATUS = 0x13
_RESULT_RANGE_STATUS = 0x14
_RESULT_CORE_AMBIENT_WINDOW_EVENTS_RTN = 0xBC
_RESULT_CORE_RANGING_TOTAL_EVENTS_RTN = 0xC0
_RESULT_CORE_AMBIENT_WINDOW_EVENTS_REF = 0xD0
_RESULT_CORE_RANGING_TOTAL_EVENTS_REF = 0xD4
_RESULT_PEAK_SIGNAL_RATE_REF = 0xB6
_ALGO_PART_TO_PART_RANGE_OFFSET_MM = 0x28
_I2C_SLAVE_DEVICE_ADDRESS = 0x8A
_MSRC_CONFIG_CONTROL = 0x60
_PRE_RANGE_CONFIG_MIN_SNR = 0x27
_PRE_RANGE_CONFIG_VALID_PHASE_LOW = 0x56
_PRE_RANGE_CONFIG_VALID_PHASE_HIGH = 0x57
_PRE_RANGE_MIN_COUNT_RATE_RTN_LIMIT = 0x64
_FINAL_RANGE_CONFIG_MIN_SNR = 0x67
_FINAL_RANGE_CONFIG_VALID_PHASE_LOW = 0x47
_FINAL_RANGE_CONFIG_VALID_PHASE_HIGH = 0x48
_FINAL_RANGE_CONFIG_MIN_COUNT_RATE_RTN_LIMIT = 0x44
_PRE_RANGE_CONFIG_SIGMA_THRESH_HI = 0x61
_PRE_RANGE_CONFIG_SIGMA_THRESH_LO = 0x62
_PRE_RANGE_CONFIG_VCSEL_PERIOD = 0x50
_PRE_RANGE_CONFIG_TIMEOUT_MACROP_HI = 0x51
_PRE_RANGE_CONFIG_TIMEOUT_MACROP_LO = 0x52
_SYSTEM_HISTOGRAM_BIN = 0x81
_HISTOGRAM_CONFIG_INITIAL_PHASE_SELECT = 0x33
_HISTOGRAM_CONFIG_READOUT_CTRL = 0x55
_FINAL_RANGE_CONFIG_VCSEL_PERIOD = 0x70
_FINAL_RANGE_CONFIG_TIMEOUT_MACROP_HI = 0x71
_FINAL_RANGE_CONFIG_TIMEOUT_MACROP_LO = 0x72
_CROSSTALK_COMPENSATION_PEAK_RATE_MCPS = 0x20
_MSRC_CONFIG_TIMEOUT_MACROP = 0x46
_SOFT_RESET_GO2_SOFT_RESET_N = 0xBF
_IDENTIFICATION_MODEL_ID = 0xC0
_IDENTIFICATION_REVISION_ID = 0xC2
_OSC_CALIBRATE_VAL = 0xF8
_GLOBAL_CONFIG_VCSEL_WIDTH = 0x32
_GLOBAL_CONFIG_SPAD_ENABLES_REF_0 = 0xB0
_GLOBAL_CONFIG_SPAD_ENABLES_REF_1 = 0xB1
_GLOBAL_CONFIG_SPAD_ENABLES_REF_2 = 0xB2
_GLOBAL_CONFIG_SPAD_ENABLES_REF_3 = 0xB3
_GLOBAL_CONFIG_SPAD_ENABLES_REF_4 = 0xB4
_GLOBAL_CONFIG_SPAD_ENABLES_REF_5 = 0xB5
_GLOBAL_CONFIG_REF_EN_START_SELECT = 0xB6
_DYNAMIC_SPAD_NUM_REQUESTED_REF_SPAD = 0x4E
_DYNAMIC_SPAD_REF_EN_START_OFFSET = 0x4F
_POWER_MANAGEMENT_GO1_POWER_FORCE = 0x80
_VHV_CONFIG_PAD_SCL_SDA__EXTSUP_HV = 0x89
_ALGO_PHASECAL_LIM = 0x30
_ALGO_PHASECAL_CONFIG_TIMEOUT = 0x30
_VCSEL_PERIOD_PRE_RANGE = 0
_VCSEL_PERIOD_FINAL_RANGE = 1


def _decode_timeout(val: int) -> int:
    return ((val & 0xFF) << ((val & 0xFF00) >> 8)) + 1


def _encode_timeout(timeout_mclks: int) -> int:
    timeout_mclks = int(timeout_mclks) & 0xFFFF
    ls_byte = 0
    ms_byte = 0
    if timeout_mclks > 0:
        ls_byte = timeout_mclks - 1
        while ls_byte > 255:
            ls_byte >>= 1
            ms_byte += 1
        return ((ms_byte << 8) | (ls_byte & 0xFF)) & 0xFFFF
    return 0


def _timeout_mclks_to_microseconds(timeout_period_mclks: int, vcsel_period_pclks: int) -> int:
    macro_period_ns = ((2304 * vcsel_period_pclks * 1655) + 500) // 1000
    return ((timeout_period_mclks * macro_period_ns) + (macro_period_ns // 2)) // 1000


def _timeout_microseconds_to_mclks(timeout_period_us: int, vcsel_period_pclks: int) -> int:
    macro_period_ns = ((2304 * vcsel_period_pclks * 1655) + 500) // 1000
    return ((timeout_period_us * 1000) + (macro_period_ns // 2)) // macro_period_ns


class P110VL53L0X(PluginBase):
    PLUGIN_ID = 110
    PLUGIN_NAME = "Distance - VL53L0X (200cm)"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SINGLE,
        value_count=2,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = 0x29
        self._distance: int = 0
        self._direction: int = 0
        self._prev_distance: int = -1
        self._stop_variable: int = 0
        self._measurement_timing_budget_us: int = 0
        self._io_timeout_s: float = 1.0
        self._initialized: bool = False

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = int(self._config.get("address") or 0x29)
        self._prev_distance = -1
        if not self._hw:
            return False
        self._initialized = await self._init_sensor()
        return self._initialized

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", 0x29)
        self._config.setdefault("timing", 80)
        self._config.setdefault("range", 0)
        self._config.setdefault("send_always", False)
        self._config.setdefault("delta", 0)
        return True

    async def _read_u8(self, reg: int) -> int:
        return await self._hw.i2c.read_byte_data(self._addr, reg)

    async def _read_u16(self, reg: int) -> int:
        d = await self._hw.i2c.read_i2c_block_data(self._addr, reg, 2)
        return (d[0] << 8) | d[1]

    async def _write_u8(self, reg: int, val: int) -> None:
        await self._hw.i2c.write_byte_data(self._addr, reg, val)

    async def _write_u16(self, reg: int, val: int) -> None:
        await self._hw.i2c.write_i2c_block_data(
            self._addr, reg, [(val >> 8) & 0xFF, val & 0xFF]
        )

    async def _get_spad_info(self) -> tuple[int, bool]:
        i2c = self._hw.i2c
        for reg, val in (
            (0x80, 0x01), (0xFF, 0x01), (0x00, 0x00), (0xFF, 0x06),
        ):
            await i2c.write_byte_data(self._addr, reg, val)
        val83 = await i2c.read_byte_data(self._addr, 0x83)
        await i2c.write_byte_data(self._addr, 0x83, val83 | 0x04)
        for reg, val in (
            (0xFF, 0x07), (0x81, 0x01), (0x80, 0x01), (0x94, 0x6B), (0x83, 0x00),
        ):
            await i2c.write_byte_data(self._addr, reg, val)
        start = time.monotonic()
        while True:
            if await i2c.read_byte_data(self._addr, 0x83) != 0x00:
                break
            if self._io_timeout_s > 0 and (time.monotonic() - start) >= self._io_timeout_s:
                raise TimeoutError("VL53L0X SPAD info timeout")
            await asyncio.sleep(0.001)
        await i2c.write_byte_data(self._addr, 0x83, 0x01)
        tmp = await i2c.read_byte_data(self._addr, 0x92)
        count = tmp & 0x7F
        is_aperture = ((tmp >> 7) & 0x01) == 1
        for reg, val in ((0x81, 0x00), (0xFF, 0x06)):
            await i2c.write_byte_data(self._addr, reg, val)
        val83 = await i2c.read_byte_data(self._addr, 0x83)
        await i2c.write_byte_data(self._addr, 0x83, val83 & ~0x04)
        for reg, val in ((0xFF, 0x01), (0x00, 0x01), (0xFF, 0x00), (0x80, 0x00)):
            await i2c.write_byte_data(self._addr, reg, val)
        return count, is_aperture

    async def _perform_single_ref_calibration(self, vhv_init_byte: int) -> None:
        i2c = self._hw.i2c
        await i2c.write_byte_data(self._addr, _SYSRANGE_START, 0x01 | (vhv_init_byte & 0xFF))
        start = time.monotonic()
        while True:
            if (await i2c.read_byte_data(self._addr, _RESULT_INTERRUPT_STATUS) & 0x07) != 0:
                break
            if self._io_timeout_s > 0 and (time.monotonic() - start) >= self._io_timeout_s:
                raise TimeoutError("VL53L0X calibration timeout")
            await asyncio.sleep(0.001)
        await i2c.write_byte_data(self._addr, _SYSTEM_INTERRUPT_CLEAR, 0x01)
        await i2c.write_byte_data(self._addr, _SYSRANGE_START, 0x00)

    async def _init_sensor(self) -> bool:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            mid = await i2c.read_byte_data(self._addr, _IDENTIFICATION_MODEL_ID)
            if mid != 0xEE:
                logger.error("VL53L0X model ID mismatch at 0x%02x: 0x%02x", self._addr, mid)
                return False

            for reg, val in (
                (0x88, 0x00), (0x80, 0x01), (0xFF, 0x01), (0x00, 0x00),
            ):
                await i2c.write_byte_data(self._addr, reg, val)
            self._stop_variable = await i2c.read_byte_data(self._addr, 0x91)
            for reg, val in ((0x00, 0x01), (0xFF, 0x00), (0x80, 0x00)):
                await i2c.write_byte_data(self._addr, reg, val)

            config_control = await i2c.read_byte_data(self._addr, _MSRC_CONFIG_CONTROL)
            await i2c.write_byte_data(self._addr, _MSRC_CONFIG_CONTROL, config_control | 0x12)

            await self._write_u16(_FINAL_RANGE_CONFIG_MIN_COUNT_RATE_RTN_LIMIT, int(0.25 * (1 << 7)))

            await i2c.write_byte_data(self._addr, _SYSTEM_SEQUENCE_CONFIG, 0xFF)

            spad_count, spad_is_aperture = await self._get_spad_info()

            ref_spad_map = await i2c.read_i2c_block_data(
                self._addr, _GLOBAL_CONFIG_SPAD_ENABLES_REF_0, 6
            )
            ref_spad_map = list(ref_spad_map)

            for reg, val in (
                (0xFF, 0x01),
                (_DYNAMIC_SPAD_REF_EN_START_OFFSET, 0x00),
                (_DYNAMIC_SPAD_NUM_REQUESTED_REF_SPAD, 0x2C),
                (0xFF, 0x00),
                (_GLOBAL_CONFIG_REF_EN_START_SELECT, 0xB4),
            ):
                await i2c.write_byte_data(self._addr, reg, val)

            first_spad_to_enable = 12 if spad_is_aperture else 0
            spads_enabled = 0
            for i in range(48):
                byte_idx = i // 8
                bit_idx = i % 8
                if i < first_spad_to_enable or spads_enabled >= spad_count:
                    ref_spad_map[byte_idx] &= ~(1 << bit_idx)
                elif (ref_spad_map[byte_idx] >> bit_idx) & 0x1:
                    spads_enabled += 1

            await i2c.write_i2c_block_data(
                self._addr, _GLOBAL_CONFIG_SPAD_ENABLES_REF_0, ref_spad_map
            )

            for reg, val in (
                (0xFF, 0x01), (0x00, 0x00), (0xFF, 0x00), (0x09, 0x00),
                (0x10, 0x00), (0x11, 0x00), (0x24, 0x01), (0x25, 0xFF),
                (0x75, 0x00), (0xFF, 0x01), (0x4E, 0x2C), (0x48, 0x00),
                (0x30, 0x20), (0xFF, 0x00), (0x30, 0x09), (0x54, 0x00),
                (0x31, 0x04), (0x32, 0x03), (0x40, 0x83), (0x46, 0x25),
                (0x60, 0x00), (0x27, 0x00), (0x50, 0x06), (0x51, 0x00),
                (0x52, 0x96), (0x56, 0x08), (0x57, 0x30), (0x61, 0x00),
                (0x62, 0x00), (0x64, 0x00), (0x65, 0x00), (0x66, 0xA0),
                (0xFF, 0x01), (0x22, 0x32), (0x47, 0x14), (0x49, 0xFF),
                (0x4A, 0x00), (0xFF, 0x00), (0x7A, 0x0A), (0x7B, 0x00),
                (0x78, 0x21), (0xFF, 0x01), (0x23, 0x34), (0x42, 0x00),
                (0x44, 0xFF), (0x45, 0x26), (0x46, 0x05), (0x40, 0x40),
                (0x0E, 0x06), (0x20, 0x1A), (0x43, 0x40), (0xFF, 0x00),
                (0x34, 0x03), (0x35, 0x44), (0xFF, 0x01), (0x31, 0x04),
                (0x4B, 0x09), (0x4C, 0x05), (0x4D, 0x04), (0xFF, 0x00),
                (0x44, 0x00), (0x45, 0x20), (0x47, 0x08), (0x48, 0x28),
                (0x67, 0x00), (0x70, 0x04), (0x71, 0x01), (0x72, 0xFE),
                (0x76, 0x00), (0x77, 0x00), (0xFF, 0x01), (0x0D, 0x01),
                (0xFF, 0x00), (0x80, 0x01), (0x01, 0xF8), (0xFF, 0x01),
                (0x8E, 0x01), (0x00, 0x01), (0xFF, 0x00), (0x80, 0x00),
            ):
                await i2c.write_byte_data(self._addr, reg, val)

            await i2c.write_byte_data(self._addr, _SYSTEM_INTERRUPT_CONFIG_GPIO, 0x04)
            gpio_hv = await i2c.read_byte_data(self._addr, _GPIO_HV_MUX_ACTIVE_HIGH)
            await i2c.write_byte_data(self._addr, _GPIO_HV_MUX_ACTIVE_HIGH, gpio_hv & ~0x10)
            await i2c.write_byte_data(self._addr, _SYSTEM_INTERRUPT_CLEAR, 0x01)

            self._measurement_timing_budget_us = await self._measurement_timing_budget()
            await i2c.write_byte_data(self._addr, _SYSTEM_SEQUENCE_CONFIG, 0xE8)
            await self._set_measurement_timing_budget(self._measurement_timing_budget_us)
            await i2c.write_byte_data(self._addr, _SYSTEM_SEQUENCE_CONFIG, 0x01)
            await self._perform_single_ref_calibration(0x40)
            await i2c.write_byte_data(self._addr, _SYSTEM_SEQUENCE_CONFIG, 0x02)
            await self._perform_single_ref_calibration(0x00)
            await i2c.write_byte_data(self._addr, _SYSTEM_SEQUENCE_CONFIG, 0xE8)

            if int(self._config.get("range") or 0) == 1:
                await self._write_u16(
                    _FINAL_RANGE_CONFIG_MIN_COUNT_RATE_RTN_LIMIT, int(0.1 * (1 << 7))
                )
                await self._write_u8(_PRE_RANGE_CONFIG_VALID_PHASE_HIGH, 0x50)
                await self._write_u8(_PRE_RANGE_CONFIG_VALID_PHASE_LOW, 0x08)
                await self._write_u8(_PRE_RANGE_CONFIG_VCSEL_PERIOD, 8)
                await self._write_u8(_FINAL_RANGE_CONFIG_VALID_PHASE_HIGH, 0x48)
                await self._write_u8(_FINAL_RANGE_CONFIG_VALID_PHASE_LOW, 0x08)
                await self._write_u8(_GLOBAL_CONFIG_VCSEL_WIDTH, 0x03)
                await self._write_u8(_ALGO_PHASECAL_CONFIG_TIMEOUT, 0x07)
                await self._write_u8(0xFF, 0x01)
                await self._write_u8(_ALGO_PHASECAL_LIM, 0x20)
                await self._write_u8(0xFF, 0x00)
                await self._write_u8(_FINAL_RANGE_CONFIG_VCSEL_PERIOD, 6)
                await self._set_measurement_timing_budget(self._measurement_timing_budget_us)
                await i2c.write_byte_data(self._addr, _SYSTEM_SEQUENCE_CONFIG, 0x02)
                await self._perform_single_ref_calibration(0x00)
                await i2c.write_byte_data(self._addr, _SYSTEM_SEQUENCE_CONFIG, 0xE8)

            timing = int(self._config.get("timing") or 80)
            if timing in (20, 80, 320):
                await self._set_measurement_timing_budget(timing * 1000)

            return True
        except Exception as e:
            logger.error("VL53L0X init failed: %s", e)
            return False

    async def _get_vcsel_pulse_period(self, vcsel_period_type: int) -> int:
        if vcsel_period_type == _VCSEL_PERIOD_PRE_RANGE:
            val = await self._read_u8(_PRE_RANGE_CONFIG_VCSEL_PERIOD)
            return ((val + 1) & 0xFF) << 1
        elif vcsel_period_type == _VCSEL_PERIOD_FINAL_RANGE:
            val = await self._read_u8(_FINAL_RANGE_CONFIG_VCSEL_PERIOD)
            return ((val + 1) & 0xFF) << 1
        return 255

    async def _get_sequence_step_enables(self) -> tuple[bool, bool, bool, bool, bool]:
        sc = await self._read_u8(_SYSTEM_SEQUENCE_CONFIG)
        tcc = (sc >> 4) & 0x1 > 0
        dss = (sc >> 3) & 0x1 > 0
        msrc = (sc >> 2) & 0x1 > 0
        pre_range = (sc >> 6) & 0x1 > 0
        final_range = (sc >> 7) & 0x1 > 0
        return tcc, dss, msrc, pre_range, final_range

    async def _get_sequence_step_timeouts(
        self, pre_range: bool
    ) -> tuple[int, int, int, int, int]:
        pre_range_vcsel_period_pclks = await self._get_vcsel_pulse_period(_VCSEL_PERIOD_PRE_RANGE)
        msrc_dss_tcc_mclks = (await self._read_u8(_MSRC_CONFIG_TIMEOUT_MACROP) + 1) & 0xFF
        msrc_dss_tcc_us = _timeout_mclks_to_microseconds(
            msrc_dss_tcc_mclks, pre_range_vcsel_period_pclks
        )
        pre_range_mclks = _decode_timeout(
            await self._read_u16(_PRE_RANGE_CONFIG_TIMEOUT_MACROP_HI)
        )
        pre_range_us = _timeout_mclks_to_microseconds(
            pre_range_mclks, pre_range_vcsel_period_pclks
        )
        final_range_vcsel_period_pclks = await self._get_vcsel_pulse_period(
            _VCSEL_PERIOD_FINAL_RANGE
        )
        final_range_mclks = _decode_timeout(
            await self._read_u16(_FINAL_RANGE_CONFIG_TIMEOUT_MACROP_HI)
        )
        if pre_range:
            final_range_mclks -= pre_range_mclks
        final_range_us = _timeout_mclks_to_microseconds(
            final_range_mclks, final_range_vcsel_period_pclks
        )
        return msrc_dss_tcc_us, pre_range_us, final_range_us, final_range_vcsel_period_pclks, pre_range_mclks

    async def _signal_rate_limit(self) -> float:
        val = await self._read_u16(_FINAL_RANGE_CONFIG_MIN_COUNT_RATE_RTN_LIMIT)
        return val / (1 << 7)

    async def _set_signal_rate_limit(self, val: float) -> None:
        assert 0.0 <= val <= 511.99
        await self._write_u16(_FINAL_RANGE_CONFIG_MIN_COUNT_RATE_RTN_LIMIT, int(val * (1 << 7)))

    async def _measurement_timing_budget(self) -> int:
        budget_us = 1910 + 960
        tcc, dss, msrc, pre_range, final_range = await self._get_sequence_step_enables()
        step_timeouts = await self._get_sequence_step_timeouts(pre_range)
        msrc_dss_tcc_us, pre_range_us, final_range_us, _, _ = step_timeouts
        if tcc:
            budget_us += msrc_dss_tcc_us + 590
        if dss:
            budget_us += 2 * (msrc_dss_tcc_us + 690)
        elif msrc:
            budget_us += msrc_dss_tcc_us + 660
        if pre_range:
            budget_us += pre_range_us + 660
        if final_range:
            budget_us += final_range_us + 550
        self._measurement_timing_budget_us = budget_us
        return budget_us

    async def _set_measurement_timing_budget(self, budget_us: int) -> None:
        assert budget_us >= 20000
        used_budget_us = 1320 + 960
        tcc, dss, msrc, pre_range, final_range = await self._get_sequence_step_enables()
        step_timeouts = await self._get_sequence_step_timeouts(pre_range)
        msrc_dss_tcc_us, pre_range_us, _ = step_timeouts[:3]
        final_range_vcsel_period_pclks, pre_range_mclks = step_timeouts[3:]
        if tcc:
            used_budget_us += msrc_dss_tcc_us + 590
        if dss:
            used_budget_us += 2 * (msrc_dss_tcc_us + 690)
        elif msrc:
            used_budget_us += msrc_dss_tcc_us + 660
        if pre_range:
            used_budget_us += pre_range_us + 660
        if final_range:
            used_budget_us += 550
            if used_budget_us > budget_us:
                raise ValueError("Requested timeout too big.")
            final_range_timeout_us = budget_us - used_budget_us
            final_range_timeout_mclks = _timeout_microseconds_to_mclks(
                final_range_timeout_us, final_range_vcsel_period_pclks
            )
            if pre_range:
                final_range_timeout_mclks += pre_range_mclks
            await self._write_u16(
                _FINAL_RANGE_CONFIG_TIMEOUT_MACROP_HI,
                _encode_timeout(final_range_timeout_mclks),
            )
            self._measurement_timing_budget_us = budget_us

    async def _do_range_measurement(self) -> None:
        i2c = self._hw.i2c
        for reg, val in (
            (0x80, 0x01), (0xFF, 0x01), (0x00, 0x00),
            (0x91, self._stop_variable), (0x00, 0x01), (0xFF, 0x00),
            (0x80, 0x00), (_SYSRANGE_START, 0x01),
        ):
            await i2c.write_byte_data(self._addr, reg, val)
        start = time.monotonic()
        while True:
            if (await i2c.read_byte_data(self._addr, _SYSRANGE_START) & 0x01) == 0:
                break
            if self._io_timeout_s > 0 and (time.monotonic() - start) >= self._io_timeout_s:
                raise TimeoutError("VL53L0X range measurement timeout")
            await asyncio.sleep(0.001)

    async def _read_range(self) -> int:
        i2c = self._hw.i2c
        start = time.monotonic()
        while True:
            if (await i2c.read_byte_data(self._addr, _RESULT_INTERRUPT_STATUS) & 0x07) != 0:
                break
            if self._io_timeout_s > 0 and (time.monotonic() - start) >= self._io_timeout_s:
                raise TimeoutError("VL53L0X read range timeout")
            await asyncio.sleep(0.001)
        range_mm = await self._read_u16(_RESULT_RANGE_STATUS + 10)
        await i2c.write_byte_data(self._addr, _SYSTEM_INTERRUPT_CLEAR, 0x01)
        return range_mm

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw or not self._initialized:
            return False
        try:
            await self._do_range_measurement()
            dist = await self._read_range()
            if 0 <= dist < 8192:
                self._distance = dist
                delta = int(self._config.get("delta") or 0)
                send_always = bool(self._config.get("send_always", False))
                if send_always or abs(dist - self._prev_distance) >= delta:
                    if self._prev_distance >= 0:
                        diff = dist - self._prev_distance
                        self._direction = 1 if diff > 0 else -1 if diff < 0 else 0
                    else:
                        self._direction = 0
                    self._prev_distance = dist
                else:
                    return False
            else:
                return False
            event.data["values"] = {"Distance": self._distance, "Direction": self._direction}
            return True
        except Exception as e:
            logger.error("VL53L0X read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select",
             "value": self._config.get("address") or 0x29, "options": [
                {"value": 0x29, "label": "0x29"},
                {"value": 0x30, "label": "0x30"},
            ]},
            {"name": "timing", "label": "Timing", "type": "select",
             "value": self._config.get("timing") or 80, "options": [
                {"value": 80, "label": "Normal"},
                {"value": 20, "label": "Fast"},
                {"value": 320, "label": "Accurate"},
            ]},
            {"name": "range", "label": "Range", "type": "select",
             "value": self._config.get("range") or 0, "options": [
                {"value": 0, "label": "Normal"},
                {"value": 1, "label": "Long"},
            ]},
            {"name": "send_always", "label": "Send event when value unchanged",
             "type": "checkbox",
             "value": self._config.get("send_always")
             if self._config.get("send_always") is not None else False},
            {"name": "delta", "label": "Trigger delta (mm)", "type": "number",
             "value": self._config.get("delta") or 0},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) in VL53L0X_ADDRS

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_DISTANCE, SENSOR_V_TYPE_SINGLE]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Distance": 0, "Direction": 0}
