#!/usr/bin/env python3
"""
simulator.py

Simulates a spacecraft sending binary telemetry packets to Yamcs over UDP.

Packet layout (big-endian, 22 bytes total):
  Offset  Size  Type    Field
  0       2     uint16  apid          (CCSDS Application Process ID)
  2       2     uint16  sequence      (packet sequence count)
  4       2     uint16  length        (data length - 1, per CCSDS)
  6       4     uint32  packet_id     (monotonic counter)
  10      4     float32 temperature   (degrees Celsius)
  14      4     float32 voltage       (volts)
  18      4     uint32  status        (0=IDLE, 1=ACTIVE, 2=ERROR)

Usage:
    python simulator.py --host 127.0.0.1 --port 10015 --rate 1
    python simulator.py --host 127.0.0.1 --port 10015 --rate 2 --count 50 --verbose
"""

from __future__ import annotations

import argparse
import logging
import math
import socket
import struct
import sys
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Packet definition
# ---------------------------------------------------------------------------

# Big-endian: 2 uint16 (primary header) + uint16 (length field) +
#             uint32 (packet_id) + float (temp) + float (voltage) + uint32 (status)
PACKET_FORMAT = ">HHHIffI"
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)   # 22 bytes

CCSDS_APID = 0x0001          # Application Process ID for this spacecraft
CCSDS_VERSION = 0b000        # CCSDS version bits
CCSDS_TYPE = 0               # 0 = telemetry
CCSDS_SEC_HDR = 0            # no secondary header in this minimal example

STATUS_IDLE = 0
STATUS_ACTIVE = 1
STATUS_ERROR = 2


@dataclass
class TelemetryPacket:
    """Represents one telemetry frame sent to Yamcs."""

    packet_id: int
    temperature: float
    voltage: float
    status: int
    sequence: int = 0

    def encode(self) -> bytes:
        """
        Pack fields into a CCSDS-framed binary packet.

        Primary header word 0: version(3) | type(1) | sec_hdr_flag(1) | apid(11)
        Primary header word 1: sequence flags(2) | sequence count(14)
        Primary header word 2: data length - 1 (number of octets in data field minus 1)
        """
        primary_word0 = (
            (CCSDS_VERSION << 13)
            | (CCSDS_TYPE << 12)
            | (CCSDS_SEC_HDR << 11)
            | (CCSDS_APID & 0x07FF)
        )
        # sequence flags 0b11 = standalone packet
        primary_word1 = (0b11 << 14) | (self.sequence & 0x3FFF)

        # data field = everything after the 6-byte primary header
        data_field_length = PACKET_SIZE - 6
        primary_word2 = data_field_length - 1  # CCSDS length field = data bytes - 1

        return struct.pack(
            PACKET_FORMAT,
            primary_word0,
            primary_word1,
            primary_word2,
            self.packet_id,
            self.temperature,
            self.voltage,
            self.status,
        )

    @classmethod
    def decode(cls, data: bytes) -> "TelemetryPacket":
        """
        Unpack a raw byte buffer back into a TelemetryPacket.

        Raises struct.error if the buffer is the wrong size.
        """
        if len(data) != PACKET_SIZE:
            raise ValueError(
                f"Expected {PACKET_SIZE} bytes, got {len(data)}"
            )
        w0, w1, _w2, packet_id, temperature, voltage, status = struct.unpack(
            PACKET_FORMAT, data
        )
        sequence = w1 & 0x3FFF
        return cls(
            packet_id=packet_id,
            temperature=temperature,
            voltage=voltage,
            status=status,
            sequence=sequence,
        )


# ---------------------------------------------------------------------------
# Telemetry generation
# ---------------------------------------------------------------------------

def generate_temperature(tick: int) -> float:
    """
    Produce a realistic oscillating temperature reading.

    Simulates thermal cycling between ~18 °C and ~28 °C.
    """
    return 23.0 + 5.0 * math.sin(tick * 0.1)


def generate_voltage(tick: int) -> float:
    """
    Produce a slowly drifting bus voltage reading.

    Simulates a 3.3 V rail with minor noise.
    """
    return 3.3 + 0.05 * math.sin(tick * 0.05)


def generate_status(tick: int) -> int:
    """
    Cycle through IDLE → ACTIVE → IDLE … with an occasional ERROR state.

    Returns one of STATUS_IDLE, STATUS_ACTIVE, STATUS_ERROR.
    """
    cycle = tick % 20
    if cycle < 8:
        return STATUS_IDLE
    if cycle < 18:
        return STATUS_ACTIVE
    return STATUS_ERROR


# ---------------------------------------------------------------------------
# UDP sender
# ---------------------------------------------------------------------------

def send_packet(sock: socket.socket, host: str, port: int, packet: TelemetryPacket) -> None:
    """
    Encode and transmit a single telemetry packet over UDP.

    Args:
        sock:   Bound UDP socket.
        host:   Destination hostname or IP.
        port:   Destination UDP port.
        packet: The TelemetryPacket to send.
    """
    data = packet.encode()
    sock.sendto(data, (host, port))
    logger.debug(
        "Sent packet seq=%d id=%d temp=%.2f°C volt=%.3fV status=%d (%d bytes)",
        packet.sequence,
        packet.packet_id,
        packet.temperature,
        packet.voltage,
        packet.status,
        len(data),
    )


def run_simulator(
    host: str,
    port: int,
    rate_hz: float,
    count: int | None,
) -> None:
    """
    Main simulation loop. Sends telemetry packets at the specified rate.

    Args:
        host:     Yamcs UDP listener address.
        port:     Yamcs UDP listener port.
        rate_hz:  Packets per second.
        count:    Total packets to send. None = run forever.
    """
    interval = 1.0 / rate_hz
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    logger.info(
        "Simulator started → %s:%d | %.1f Hz | packet size = %d bytes | count = %s",
        host,
        port,
        rate_hz,
        PACKET_SIZE,
        str(count) if count is not None else "∞",
    )

    tick = 0
    try:
        while count is None or tick < count:
            packet = TelemetryPacket(
                packet_id=tick,
                temperature=generate_temperature(tick),
                voltage=generate_voltage(tick),
                status=generate_status(tick),
                sequence=tick & 0x3FFF,
            )
            try:
                send_packet(sock, host, port, packet)
            except OSError as exc:
                logger.error("Failed to send packet %d: %s", tick, exc)

            tick += 1
            time.sleep(interval)

    except KeyboardInterrupt:
        logger.info("Simulator stopped by user after %d packets.", tick)
    finally:
        sock.close()
        logger.info("Socket closed.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    )
    logging.getLogger().setLevel(level)
    logging.getLogger().addHandler(handler)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the telemetry simulator CLI."""
    parser = argparse.ArgumentParser(
        description="Simulate spacecraft telemetry packets sent to Yamcs over UDP."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Yamcs UDP host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=10015, help="Yamcs UDP port (default: 10015)")
    parser.add_argument(
        "--rate", type=float, default=1.0, help="Packets per second (default: 1.0)"
    )
    parser.add_argument(
        "--count", type=int, default=None, help="Total packets to send (default: unlimited)"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    if args.rate <= 0:
        logger.error("--rate must be greater than 0.")
        return 1

    run_simulator(
        host=args.host,
        port=args.port,
        rate_hz=args.rate,
        count=args.count,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())