#!/usr/bin/env python3
"""
test_pipeline.py

pytest test suite covering:
  - XTCE validator (validate_xtce.py)
  - Telemetry simulator packet encoding (test_simulator.py)
"""

from __future__ import annotations

import math
import socket
import struct
import threading
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from validate_xtce import (
    ValidationReport,
    check_argument_type_refs,
    check_counts,
    check_duplicate_commands,
    check_duplicate_parameters,
    check_parameter_refs,
    check_root_element,
    check_type_refs,
    check_well_formed,
    validate,
)
from simulator import (
    PACKET_FORMAT,
    PACKET_SIZE,
    STATUS_ACTIVE,
    STATUS_ERROR,
    STATUS_IDLE,
    TelemetryPacket,
    generate_status,
    generate_temperature,
    generate_voltage,
    run_simulator,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

XTCE_NS = "http://www.omg.org/space/xtce"


def _xtce(tag: str) -> str:
    return f"{{{XTCE_NS}}}{tag}"


def _write_xml(tmp_path: Path, content: str, name: str = "test.xml") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


VALID_XTCE = """\
<?xml version='1.0' encoding='UTF-8'?>
<xtce:SpaceSystem xmlns:xtce="http://www.omg.org/space/xtce" name="TestMission">
  <xtce:TelemetryMetaData>
    <xtce:ParameterTypeSet>
      <xtce:IntegerParameterType name="uint32Type" signed="false">
        <xtce:IntegerDataEncoding sizeInBits="32" encoding="unsigned"/>
      </xtce:IntegerParameterType>
      <xtce:FloatParameterType name="floatType">
        <xtce:FloatDataEncoding sizeInBits="32" encoding="IEEE754_1985"/>
      </xtce:FloatParameterType>
      <xtce:EnumeratedParameterType name="statusType">
        <xtce:IntegerDataEncoding sizeInBits="32" encoding="unsigned"/>
        <xtce:EnumerationList>
          <xtce:Enumeration label="IDLE" value="0"/>
          <xtce:Enumeration label="ACTIVE" value="1"/>
        </xtce:EnumerationList>
      </xtce:EnumeratedParameterType>
    </xtce:ParameterTypeSet>
    <xtce:ParameterSet>
      <xtce:Parameter name="packet_id"   parameterTypeRef="uint32Type"/>
      <xtce:Parameter name="temperature" parameterTypeRef="floatType"/>
      <xtce:Parameter name="status"      parameterTypeRef="statusType"/>
    </xtce:ParameterSet>
    <xtce:ContainerSet>
      <xtce:SequenceContainer name="TlmPacket">
        <xtce:EntryList>
          <xtce:ParameterRefEntry parameterRef="packet_id"/>
          <xtce:ParameterRefEntry parameterRef="temperature"/>
          <xtce:ParameterRefEntry parameterRef="status"/>
        </xtce:EntryList>
      </xtce:SequenceContainer>
    </xtce:ContainerSet>
  </xtce:TelemetryMetaData>
  <xtce:CommandMetaData>
    <xtce:ArgumentTypeSet/>
    <xtce:MetaCommandSet>
      <xtce:MetaCommand name="CMD_NO_OP" abstract="false">
        <xtce:CommandContainer name="CMD_NO_OP_CC">
          <xtce:EntryList/>
        </xtce:CommandContainer>
      </xtce:MetaCommand>
    </xtce:MetaCommandSet>
  </xtce:CommandMetaData>
</xtce:SpaceSystem>
"""


# ============================================================================
# Part 1: XTCE Validator Tests
# ============================================================================

class TestWellFormedXML:
    def test_valid_xml_is_well_formed(self, tmp_path: Path) -> None:
        p = _write_xml(tmp_path, VALID_XTCE)
        ok, msg, root = check_well_formed(p)
        assert ok is True
        assert root is not None

    def test_malformed_xml_fails(self, tmp_path: Path) -> None:
        p = _write_xml(tmp_path, "<xtce:SpaceSystem><unclosed>")
        ok, msg, root = check_well_formed(p)
        assert ok is False
        assert root is None
        assert "parse error" in msg.lower()

    def test_empty_file_fails(self, tmp_path: Path) -> None:
        p = _write_xml(tmp_path, "")
        ok, msg, root = check_well_formed(p)
        assert ok is False

    def test_non_xml_content_fails(self, tmp_path: Path) -> None:
        p = _write_xml(tmp_path, "this is not xml at all !!!")
        ok, msg, root = check_well_formed(p)
        assert ok is False


class TestRootElement:
    def test_correct_root_passes(self, tmp_path: Path) -> None:
        p = _write_xml(tmp_path, VALID_XTCE)
        _, _, root = check_well_formed(p)
        ok, msg = check_root_element(root)
        assert ok is True
        assert "SpaceSystem" in msg

    def test_wrong_root_fails(self, tmp_path: Path) -> None:
        xml = '<root xmlns:xtce="http://www.omg.org/space/xtce"><child/></root>'
        p = _write_xml(tmp_path, xml)
        _, _, root = check_well_formed(p)
        ok, msg = check_root_element(root)
        assert ok is False


class TestRequiredSections:
    def test_valid_xtce_has_all_sections(self, tmp_path: Path) -> None:
        p = _write_xml(tmp_path, VALID_XTCE)
        report = validate(p)
        section_checks = [
            r for r in report.results
            if r.name in {
                "TelemetryMetaData", "ParameterTypeSet", "ParameterSet",
                "ContainerSet", "CommandMetaData", "MetaCommandSet"
            }
        ]
        assert all(r.passed for r in section_checks), [
            r for r in section_checks if not r.passed
        ]

    def test_missing_telemetry_meta_fails(self, tmp_path: Path) -> None:
        xml = """\
<?xml version='1.0' encoding='UTF-8'?>
<xtce:SpaceSystem xmlns:xtce="http://www.omg.org/space/xtce" name="X">
  <xtce:CommandMetaData>
    <xtce:ArgumentTypeSet/>
    <xtce:MetaCommandSet/>
  </xtce:CommandMetaData>
</xtce:SpaceSystem>
"""
        p = _write_xml(tmp_path, xml)
        report = validate(p)
        tlm_check = next(r for r in report.results if r.name == "TelemetryMetaData")
        assert tlm_check.passed is False

    def test_missing_parameter_set_fails(self, tmp_path: Path) -> None:
        xml = """\
<?xml version='1.0' encoding='UTF-8'?>
<xtce:SpaceSystem xmlns:xtce="http://www.omg.org/space/xtce" name="X">
  <xtce:TelemetryMetaData>
    <xtce:ParameterTypeSet/>
    <xtce:ContainerSet/>
  </xtce:TelemetryMetaData>
  <xtce:CommandMetaData>
    <xtce:ArgumentTypeSet/>
    <xtce:MetaCommandSet/>
  </xtce:CommandMetaData>
</xtce:SpaceSystem>
"""
        p = _write_xml(tmp_path, xml)
        report = validate(p)
        ps_check = next(r for r in report.results if r.name == "ParameterSet")
        assert ps_check.passed is False

    def test_missing_container_set_fails(self, tmp_path: Path) -> None:
        xml = """\
<?xml version='1.0' encoding='UTF-8'?>
<xtce:SpaceSystem xmlns:xtce="http://www.omg.org/space/xtce" name="X">
  <xtce:TelemetryMetaData>
    <xtce:ParameterTypeSet/>
    <xtce:ParameterSet/>
  </xtce:TelemetryMetaData>
  <xtce:CommandMetaData>
    <xtce:ArgumentTypeSet/>
    <xtce:MetaCommandSet/>
  </xtce:CommandMetaData>
</xtce:SpaceSystem>
"""
        p = _write_xml(tmp_path, xml)
        report = validate(p)
        cs_check = next(r for r in report.results if r.name == "ContainerSet")
        assert cs_check.passed is False


class TestDuplicateParameters:
    def test_no_duplicates_in_valid_xtce(self, tmp_path: Path) -> None:
        p = _write_xml(tmp_path, VALID_XTCE)
        _, _, root = check_well_formed(p)
        ok, msg = check_duplicate_parameters(root)
        assert ok is True

    def test_duplicate_parameter_detected(self, tmp_path: Path) -> None:
        xml = """\
<?xml version='1.0' encoding='UTF-8'?>
<xtce:SpaceSystem xmlns:xtce="http://www.omg.org/space/xtce" name="X">
  <xtce:TelemetryMetaData>
    <xtce:ParameterTypeSet>
      <xtce:IntegerParameterType name="uint32Type" signed="false">
        <xtce:IntegerDataEncoding sizeInBits="32" encoding="unsigned"/>
      </xtce:IntegerParameterType>
    </xtce:ParameterTypeSet>
    <xtce:ParameterSet>
      <xtce:Parameter name="temp" parameterTypeRef="uint32Type"/>
      <xtce:Parameter name="temp" parameterTypeRef="uint32Type"/>
    </xtce:ParameterSet>
    <xtce:ContainerSet/>
  </xtce:TelemetryMetaData>
  <xtce:CommandMetaData>
    <xtce:ArgumentTypeSet/>
    <xtce:MetaCommandSet/>
  </xtce:CommandMetaData>
</xtce:SpaceSystem>
"""
        p = _write_xml(tmp_path, xml)
        _, _, root = check_well_formed(p)
        ok, msg = check_duplicate_parameters(root)
        assert ok is False
        assert "temp" in msg

    def test_duplicate_command_detected(self, tmp_path: Path) -> None:
        xml = """\
<?xml version='1.0' encoding='UTF-8'?>
<xtce:SpaceSystem xmlns:xtce="http://www.omg.org/space/xtce" name="X">
  <xtce:TelemetryMetaData>
    <xtce:ParameterTypeSet/>
    <xtce:ParameterSet/>
    <xtce:ContainerSet/>
  </xtce:TelemetryMetaData>
  <xtce:CommandMetaData>
    <xtce:ArgumentTypeSet/>
    <xtce:MetaCommandSet>
      <xtce:MetaCommand name="CMD_A" abstract="false">
        <xtce:CommandContainer name="CC_A"><xtce:EntryList/></xtce:CommandContainer>
      </xtce:MetaCommand>
      <xtce:MetaCommand name="CMD_A" abstract="false">
        <xtce:CommandContainer name="CC_A2"><xtce:EntryList/></xtce:CommandContainer>
      </xtce:MetaCommand>
    </xtce:MetaCommandSet>
  </xtce:CommandMetaData>
</xtce:SpaceSystem>
"""
        p = _write_xml(tmp_path, xml)
        _, _, root = check_well_formed(p)
        ok, msg = check_duplicate_commands(root)
        assert ok is False
        assert "CMD_A" in msg


class TestParameterRefs:
    def test_valid_refs_pass(self, tmp_path: Path) -> None:
        p = _write_xml(tmp_path, VALID_XTCE)
        _, _, root = check_well_formed(p)
        ok, msg = check_parameter_refs(root)
        assert ok is True

    def test_broken_ref_detected(self, tmp_path: Path) -> None:
        xml = """\
<?xml version='1.0' encoding='UTF-8'?>
<xtce:SpaceSystem xmlns:xtce="http://www.omg.org/space/xtce" name="X">
  <xtce:TelemetryMetaData>
    <xtce:ParameterTypeSet>
      <xtce:IntegerParameterType name="uint32Type" signed="false">
        <xtce:IntegerDataEncoding sizeInBits="32" encoding="unsigned"/>
      </xtce:IntegerParameterType>
    </xtce:ParameterTypeSet>
    <xtce:ParameterSet>
      <xtce:Parameter name="packet_id" parameterTypeRef="uint32Type"/>
    </xtce:ParameterSet>
    <xtce:ContainerSet>
      <xtce:SequenceContainer name="Pkt">
        <xtce:EntryList>
          <xtce:ParameterRefEntry parameterRef="does_not_exist"/>
        </xtce:EntryList>
      </xtce:SequenceContainer>
    </xtce:ContainerSet>
  </xtce:TelemetryMetaData>
  <xtce:CommandMetaData>
    <xtce:ArgumentTypeSet/>
    <xtce:MetaCommandSet/>
  </xtce:CommandMetaData>
</xtce:SpaceSystem>
"""
        p = _write_xml(tmp_path, xml)
        _, _, root = check_well_formed(p)
        ok, msg = check_parameter_refs(root)
        assert ok is False
        assert "does_not_exist" in msg

    def test_type_ref_validation(self, tmp_path: Path) -> None:
        p = _write_xml(tmp_path, VALID_XTCE)
        _, _, root = check_well_formed(p)
        ok, msg = check_type_refs(root)
        assert ok is True

    def test_broken_type_ref_detected(self, tmp_path: Path) -> None:
        xml = """\
<?xml version='1.0' encoding='UTF-8'?>
<xtce:SpaceSystem xmlns:xtce="http://www.omg.org/space/xtce" name="X">
  <xtce:TelemetryMetaData>
    <xtce:ParameterTypeSet/>
    <xtce:ParameterSet>
      <xtce:Parameter name="temp" parameterTypeRef="nonExistentType"/>
    </xtce:ParameterSet>
    <xtce:ContainerSet/>
  </xtce:TelemetryMetaData>
  <xtce:CommandMetaData>
    <xtce:ArgumentTypeSet/>
    <xtce:MetaCommandSet/>
  </xtce:CommandMetaData>
</xtce:SpaceSystem>
"""
        p = _write_xml(tmp_path, xml)
        _, _, root = check_well_formed(p)
        ok, msg = check_type_refs(root)
        assert ok is False
        assert "nonExistentType" in msg


class TestFullValidation:
    def test_valid_xtce_passes_all(self, tmp_path: Path) -> None:
        p = _write_xml(tmp_path, VALID_XTCE)
        report = validate(p)
        assert report.passed is True

    def test_hs2_mission_xml_passes(self) -> None:
        path = Path(__file__).parent / "hs2_mission.xml"
        if not path.exists():
            pytest.skip("hs2_mission.xml not found alongside test file")
        report = validate(path)
        assert report.passed is True

    def test_report_has_results(self, tmp_path: Path) -> None:
        p = _write_xml(tmp_path, VALID_XTCE)
        report = validate(p)
        assert len(report.results) > 0

    def test_report_path_preserved(self, tmp_path: Path) -> None:
        p = _write_xml(tmp_path, VALID_XTCE)
        report = validate(p)
        assert report.path == p

    def test_counts_check_always_passes(self, tmp_path: Path) -> None:
        p = _write_xml(tmp_path, VALID_XTCE)
        _, _, root = check_well_formed(p)
        ok, msg = check_counts(root)
        assert ok is True
        assert "parameters" in msg


# ============================================================================
# Part 2: Simulator / Packet Encoding Tests
# ============================================================================

class TestPacketEncoding:
    def test_packet_size_matches_format(self) -> None:
        expected = struct.calcsize(PACKET_FORMAT)
        assert PACKET_SIZE == expected
        assert PACKET_SIZE == 22

    def test_encode_returns_correct_byte_count(self) -> None:
        pkt = TelemetryPacket(packet_id=1, temperature=23.5, voltage=3.3, status=STATUS_IDLE)
        raw = pkt.encode()
        assert len(raw) == PACKET_SIZE

    def test_encode_decode_roundtrip(self) -> None:
        original = TelemetryPacket(
            packet_id=42,
            temperature=21.75,
            voltage=3.28,
            status=STATUS_ACTIVE,
            sequence=7,
        )
        raw = original.encode()
        recovered = TelemetryPacket.decode(raw)
        assert recovered.packet_id == original.packet_id
        assert recovered.sequence == original.sequence
        assert recovered.status == original.status
        assert math.isclose(recovered.temperature, original.temperature, rel_tol=1e-5)
        assert math.isclose(recovered.voltage, original.voltage, rel_tol=1e-5)

    def test_decode_wrong_size_raises(self) -> None:
        with pytest.raises((ValueError, struct.error)):
            TelemetryPacket.decode(b"\x00" * 10)

    def test_status_values_are_correct_constants(self) -> None:
        assert STATUS_IDLE == 0
        assert STATUS_ACTIVE == 1
        assert STATUS_ERROR == 2

    def test_packet_id_zero(self) -> None:
        pkt = TelemetryPacket(packet_id=0, temperature=0.0, voltage=0.0, status=0)
        raw = pkt.encode()
        recovered = TelemetryPacket.decode(raw)
        assert recovered.packet_id == 0

    def test_large_packet_id(self) -> None:
        pkt = TelemetryPacket(packet_id=0xFFFFFFFF, temperature=0.0, voltage=0.0, status=0)
        raw = pkt.encode()
        recovered = TelemetryPacket.decode(raw)
        assert recovered.packet_id == 0xFFFFFFFF

    def test_negative_temperature_encodes(self) -> None:
        pkt = TelemetryPacket(packet_id=1, temperature=-40.0, voltage=3.3, status=0)
        raw = pkt.encode()
        recovered = TelemetryPacket.decode(raw)
        assert math.isclose(recovered.temperature, -40.0, rel_tol=1e-5)

    def test_big_endian_byte_order(self) -> None:
        pkt = TelemetryPacket(packet_id=1, temperature=0.0, voltage=0.0, status=0, sequence=0)
        raw = pkt.encode()
        # CCSDS word0 should be big-endian; APID=0x0001 → word0 = 0x0001
        # version=0, type=0, sec_hdr=0, apid=1 → 0x0001
        word0 = struct.unpack(">H", raw[0:2])[0]
        assert (word0 & 0x07FF) == 0x0001  # APID bits

    def test_sequence_wraps_at_14_bits(self) -> None:
        pkt = TelemetryPacket(
            packet_id=0, temperature=0.0, voltage=0.0, status=0, sequence=0x3FFF
        )
        raw = pkt.encode()
        recovered = TelemetryPacket.decode(raw)
        assert recovered.sequence == 0x3FFF

    def test_ccsds_length_field_correct(self) -> None:
        pkt = TelemetryPacket(packet_id=0, temperature=0.0, voltage=0.0, status=0)
        raw = pkt.encode()
        # length field = data bytes - 1; data bytes = total - 6 header bytes = 16
        length_field = struct.unpack(">H", raw[4:6])[0]
        assert length_field == (PACKET_SIZE - 6 - 1)


class TestTelemetryGenerators:
    def test_temperature_in_expected_range(self) -> None:
        for tick in range(100):
            t = generate_temperature(tick)
            assert 15.0 <= t <= 32.0, f"Temperature {t} out of range at tick {tick}"

    def test_voltage_near_3v3(self) -> None:
        for tick in range(100):
            v = generate_voltage(tick)
            assert 3.1 <= v <= 3.5, f"Voltage {v} out of range at tick {tick}"

    def test_status_only_valid_values(self) -> None:
        valid = {STATUS_IDLE, STATUS_ACTIVE, STATUS_ERROR}
        for tick in range(100):
            s = generate_status(tick)
            assert s in valid, f"Invalid status {s} at tick {tick}"

    def test_status_cycles_through_all_states(self) -> None:
        statuses = {generate_status(tick) for tick in range(200)}
        assert STATUS_IDLE in statuses
        assert STATUS_ACTIVE in statuses
        assert STATUS_ERROR in statuses


class TestUDPTransmission:
    def test_simulator_sends_udp_packets(self) -> None:
        """Launch simulator for 3 packets and verify receipt over loopback UDP."""
        received: list[bytes] = []
        port = 19876

        def _listen() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            sock.settimeout(5.0)
            try:
                while len(received) < 3:
                    data, _ = sock.recvfrom(1024)
                    received.append(data)
            except socket.timeout:
                pass
            finally:
                sock.close()

        listener = threading.Thread(target=_listen, daemon=True)
        listener.start()
        time.sleep(0.05)

        sim = threading.Thread(
            target=run_simulator,
            kwargs={"host": "127.0.0.1", "port": port, "rate_hz": 10.0, "count": 3},
            daemon=True,
        )
        sim.start()
        listener.join(timeout=6.0)

        assert len(received) == 3, f"Expected 3 packets, got {len(received)}"
        for raw in received:
            assert len(raw) == PACKET_SIZE
            pkt = TelemetryPacket.decode(raw)
            assert 15.0 <= pkt.temperature <= 32.0
            assert pkt.status in {STATUS_IDLE, STATUS_ACTIVE, STATUS_ERROR}

    def test_decoded_packets_have_incrementing_ids(self) -> None:
        received: list[bytes] = []
        port = 19877

        def _listen() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            sock.settimeout(5.0)
            try:
                while len(received) < 5:
                    data, _ = sock.recvfrom(1024)
                    received.append(data)
            except socket.timeout:
                pass
            finally:
                sock.close()

        listener = threading.Thread(target=_listen, daemon=True)
        listener.start()
        time.sleep(0.05)

        sim = threading.Thread(
            target=run_simulator,
            kwargs={"host": "127.0.0.1", "port": port, "rate_hz": 20.0, "count": 5},
            daemon=True,
        )
        sim.start()
        listener.join(timeout=6.0)

        assert len(received) == 5
        ids = [TelemetryPacket.decode(r).packet_id for r in received]
        assert ids == list(range(5)), f"packet_ids not sequential: {ids}"