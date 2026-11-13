#!/usr/bin/env python3
"""
simulator_mqtt.py

Simulates a spacecraft publishing binary CCSDS telemetry packets to an MQTT broker.

Usage:
    python simulator_mqtt.py --host 127.0.0.1 --port 1883 --topic satellite/telemetry --rate 1
"""

from __future__ import annotations

import argparse
import logging
import math
import struct
import sys
import time
from dataclasses import dataclass

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Packet definition (Unchanged)
# ---------------------------------------------------------------------------

PACKET_FORMAT = ">HHHIffI"
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)  # 22 bytes

CCSDS_APID = 0x0001
CCSDS_VERSION = 0b000
CCSDS_TYPE = 0
CCSDS_SEC_HDR = 0

STATUS_IDLE = 0
STATUS_ACTIVE = 1
STATUS_ERROR = 2


@dataclass
class TelemetryPacket:
    packet_id: int
    temperature: float
    voltage: float
    status: int
    sequence: int = 0

    def encode(self) -> bytes:
        primary_word0 = (
            (CCSDS_VERSION << 13)
            | (CCSDS_TYPE << 12)
            | (CCSDS_SEC_HDR << 11)
            | (CCSDS_APID & 0x07FF)
        )
        primary_word1 = (0b11 << 14) | (self.sequence & 0x3FFF)
        data_field_length = PACKET_SIZE - 6
        primary_word2 = data_field_length - 1

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
        if len(data) != PACKET_SIZE:
            raise ValueError(f"Expected {PACKET_SIZE} bytes, got {len(data)}")
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
# Telemetry generation (Unchanged)
# ---------------------------------------------------------------------------

def generate_temperature(tick: int) -> float:
    return 23.0 + 5.0 * math.sin(tick * 0.1)


def generate_voltage(tick: int) -> float:
    return 3.3 + 0.05 * math.sin(tick * 0.05)


def generate_status(tick: int) -> int:
    cycle = tick % 20
    if cycle < 8:
        return STATUS_IDLE
    if cycle < 18:
        return STATUS_ACTIVE
    return STATUS_ERROR


# ---------------------------------------------------------------------------
# MQTT Publisher
# ---------------------------------------------------------------------------

def publish_packet(
    client: mqtt.Client, topic: str, packet: TelemetryPacket, qos: int = 0
) -> None:
    """Encode and publish a single raw binary telemetry packet over MQTT."""
    data = packet.encode()
    # paho-mqtt accepts raw `bytes` directly as the payload
    info = client.publish(topic, payload=data, qos=qos)
    info.wait_for_publish()

    logger.debug(
        "Published packet seq=%d id=%d temp=%.2f°C volt=%.3fV status=%d (%d bytes) -> %s",
        packet.sequence,
        packet.packet_id,
        packet.temperature,
        packet.voltage,
        packet.status,
        len(data),
        topic,
    )


def run_simulator(
    host: str,
    port: int,
    topic: str,
    rate_hz: float,
    count: int | None,
    qos: int,
) -> None:
    interval = 1.0 / rate_hz

    # Initialize MQTT v2 Client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="satellite-sim")
    client.connect(host, port, keepalive=60)
    client.loop_start()  # Runs background network thread for ping/keepalive

    logger.info(
        "Simulator started → mqtt://%s:%d/%s | %.1f Hz | packet size = %d bytes | count = %s",
        host,
        port,
        topic,
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
                publish_packet(client, topic, packet, qos=qos)
            except Exception as exc:
                logger.error("Failed to publish packet %d: %s", tick, exc)

            tick += 1
            time.sleep(interval)

    except KeyboardInterrupt:
        logger.info("Simulator stopped by user after %d packets.", tick)
    finally:
        client.loop_stop()
        client.disconnect()
        logger.info("MQTT client disconnected.")


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
    parser = argparse.ArgumentParser(
        description="Simulate spacecraft telemetry packets published to MQTT."
    )
    parser.add_argument("--host", default="127.0.0.1", help="MQTT broker host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port (default: 1883)")
    parser.add_argument("--topic", default="yamcs-tm", help="MQTT topic (default: satellite/telemetry)")
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=0, help="MQTT QoS level (default: 0)")
    parser.add_argument("--rate", type=float, default=1.0, help="Packets per second (default: 1.0)")
    parser.add_argument("--count", type=int, default=None, help="Total packets to send (default: unlimited)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    if args.rate <= 0:
        logger.error("--rate must be greater than 0.")
        return 1

    run_simulator(
        host=args.host,
        port=args.port,
        topic=args.topic,
        rate_hz=args.rate,
        count=args.count,
        qos=args.qos,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())