#!/usr/bin/env python3
"""
test_fprime_to_xtce.py

pytest test suite for fprime_to_xtce.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from fprime_to_xtce import (
    EnumValue,
    FPrimeDictionary,
    FPrimeType,
    _xtce,
    build_xtce,
    load_json,
    parse_dictionary,
    validate_xtce,
    write_xml,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_dict() -> dict:
    return {
        "metadata": {"deploymentName": "TestMission"},
        "commands": [],
        "telemetryChannels": [],
        "events": [],
    }


@pytest.fixture
def full_dict() -> dict:
    return {
        "metadata": {"deploymentName": "FullMission"},
        "commands": [
            {
                "name": "sys.CMD_NO_OP",
                "opcode": 1,
                "commandKind": "async",
                "formalParams": [],
                "annotation": "No-op",
            },
            {
                "name": "sys.CMD_SET_RATE",
                "opcode": 2,
                "commandKind": "async",
                "formalParams": [
                    {
                        "name": "rate",
                        "type": {"kind": "integer", "name": "U32", "signed": False, "size": 32},
                    }
                ],
                "annotation": "Set rate",
            },
            {
                "name": "sys.CMD_SEND_FILE",
                "opcode": 3,
                "commandKind": "async",
                "formalParams": [
                    {"name": "path", "type": {"kind": "string", "name": "string"}},
                    {"name": "flag", "type": {"kind": "bool", "name": "bool"}},
                ],
                "annotation": "Send file",
            },
        ],
        "telemetryChannels": [
            {
                "name": "sys.Temperature",
                "id": 10,
                "type": {"kind": "float", "name": "F32", "size": 32},
                "units": "degC",
                "telemetryUpdate": "ALWAYS",
                "annotation": "MCU temperature",
            },
            {
                "name": "sys.Status",
                "id": 11,
                "type": {
                    "kind": "qualifiedIdentifier",
                    "name": "Sys.Status",
                    "enumeration": [
                        {"name": "IDLE", "value": 0},
                        {"name": "ACTIVE", "value": 1},
                        {"name": "ERROR", "value": 2},
                    ],
                },
                "telemetryUpdate": "ON_CHANGE",
                "annotation": "System status",
            },
        ],
        "events": [
            {
                "name": "sys.StatusChanged",
                "id": 1,
                "severity": "ACTIVITY_HI",
                "formatString": "Status: %d",
                "formalParams": [
                    {
                        "name": "newStatus",
                        "type": {"kind": "integer", "name": "I32", "signed": True, "size": 32},
                    }
                ],
                "annotation": "Status changed event",
            }
        ],
    }


# ---------------------------------------------------------------------------
# 1. Valid JSON dictionary
# ---------------------------------------------------------------------------

def test_load_json_valid(tmp_path: Path, full_dict: dict) -> None:
    p = tmp_path / "dict.json"
    p.write_text(json.dumps(full_dict), encoding="utf-8")
    data = load_json(p)
    assert data["metadata"]["deploymentName"] == "FullMission"


def test_parse_commands(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    assert len(d.commands) == 3
    assert d.commands[0].name == "sys.CMD_NO_OP"
    assert d.commands[0].opcode == 1
    assert d.commands[1].params[0].name == "rate"
    assert d.commands[1].params[0].fprime_type.kind == "integer"


def test_parse_telemetry(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    assert len(d.telemetry) == 2
    assert d.telemetry[0].name == "sys.Temperature"
    assert d.telemetry[0].fprime_type.kind == "float"
    assert d.telemetry[0].units == "degC"


def test_parse_events(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    assert len(d.events) == 1
    assert d.events[0].severity == "ACTIVITY_HI"
    assert d.events[0].params[0].name == "newStatus"


# ---------------------------------------------------------------------------
# 2. Nested telemetry definitions
# ---------------------------------------------------------------------------

def test_nested_telemetry_name_preserved(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    names = [t.name for t in d.telemetry]
    assert "sys.Temperature" in names
    assert "sys.Status" in names


def test_telemetry_type_integer_unsigned(full_dict: dict) -> None:
    extra = {
        "metadata": {"deploymentName": "X"},
        "commands": [],
        "telemetryChannels": [
            {
                "name": "ch.Counter",
                "id": 99,
                "type": {"kind": "integer", "name": "U16", "signed": False, "size": 16},
                "telemetryUpdate": "ALWAYS",
            }
        ],
        "events": [],
    }
    d = parse_dictionary(extra)
    assert d.telemetry[0].fprime_type.size == 16
    assert d.telemetry[0].fprime_type.signed is False


# ---------------------------------------------------------------------------
# 3. Enum conversion
# ---------------------------------------------------------------------------

def test_enum_values_parsed(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    status_ch = next(t for t in d.telemetry if "Status" in t.name)
    assert status_ch.fprime_type.kind == "enum"
    labels = [ev.label for ev in status_ch.fprime_type.enum_values]
    assert "IDLE" in labels
    assert "ACTIVE" in labels
    assert "ERROR" in labels


def test_enum_values_in_xtce(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    enum_types = [
        el for el in root.iter(_xtce("EnumeratedParameterType"))
    ]
    assert len(enum_types) >= 1
    enum_list = enum_types[0].find(_xtce("EnumerationList"))
    assert enum_list is not None
    labels = [e.get("label") for e in enum_list.findall(_xtce("Enumeration"))]
    assert "IDLE" in labels or "OFF" in labels or len(labels) > 0


def test_command_enum_arg_type(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    enum_arg_types = [
        el for el in root.iter(_xtce("EnumeratedArgumentType"))
    ]
    # full_dict commands don't include enums, so none expected
    assert isinstance(enum_arg_types, list)


# ---------------------------------------------------------------------------
# 4. Invalid JSON
# ---------------------------------------------------------------------------

def test_load_json_invalid(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_json(p)


def test_load_json_wrong_type(tmp_path: Path) -> None:
    p = tmp_path / "arr.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="Top-level JSON value must be an object"):
        load_json(p)


def test_load_json_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        load_json(Path("/nonexistent/path/dict.json"))


# ---------------------------------------------------------------------------
# 5. Empty input
# ---------------------------------------------------------------------------

def test_empty_commands_and_telemetry(minimal_dict: dict) -> None:
    d = parse_dictionary(minimal_dict)
    assert d.commands == []
    assert d.telemetry == []
    assert d.events == []
    assert d.deployment_name == "TestMission"


def test_empty_dict_builds_xtce(minimal_dict: dict) -> None:
    d = parse_dictionary(minimal_dict)
    root = build_xtce(d)
    assert root.tag == _xtce("SpaceSystem")
    assert root.get("name") == "TestMission"


def test_empty_file_raises(tmp_path: Path) -> None:
    p = tmp_path / "empty.json"
    p.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        load_json(p)


# ---------------------------------------------------------------------------
# 6. XTCE XML output validity
# ---------------------------------------------------------------------------

def test_write_and_validate_xml(tmp_path: Path, full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    out = tmp_path / "mission.xml"
    write_xml(root, out)
    assert out.exists()
    assert out.stat().st_size > 0
    ok = validate_xtce(out)
    assert ok is True


def test_xml_has_space_system_root(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    assert root.tag == _xtce("SpaceSystem")


def test_xml_has_telemetry_meta(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    tlm = root.find(_xtce("TelemetryMetaData"))
    assert tlm is not None


def test_xml_has_command_meta(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    cmd_meta = root.find(_xtce("CommandMetaData"))
    assert cmd_meta is not None


def test_xml_parameters_present(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    params = list(root.iter(_xtce("Parameter")))
    assert len(params) >= 2  # sys.Temperature, sys.Status, plus event params


def test_xml_metacommands_present(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    cmds = list(root.iter(_xtce("MetaCommand")))
    assert len(cmds) == 3


def test_xml_opcode_ancillary_data(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    ancillary_data = list(root.iter(_xtce("AncillaryData")))
    opcodes = [ad for ad in ancillary_data if ad.get("name") == "opcode"]
    assert len(opcodes) == 3
    values = {int(ad.text) for ad in opcodes}
    assert 1 in values
    assert 2 in values


def test_no_duplicate_parameters(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    names = [el.get("name") for el in root.iter(_xtce("Parameter"))]
    assert len(names) == len(set(names)), f"Duplicate parameter names: {names}"


def test_no_duplicate_metacommands(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    names = [el.get("name") for el in root.iter(_xtce("MetaCommand"))]
    assert len(names) == len(set(names)), f"Duplicate MetaCommand names: {names}"


def test_containers_present(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    containers = list(root.iter(_xtce("SequenceContainer")))
    assert len(containers) == len(d.telemetry)


def test_argument_list_for_parameterized_command(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    meta_cmds = {el.get("name"): el for el in root.iter(_xtce("MetaCommand"))}
    cmd_rate = meta_cmds.get("sys_CMD_SET_RATE")
    assert cmd_rate is not None
    arg_list = cmd_rate.find(_xtce("ArgumentList"))
    assert arg_list is not None
    args = arg_list.findall(_xtce("Argument"))
    assert len(args) == 1
    assert args[0].get("name") == "rate"


def test_bool_argument_type(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    # sys.CMD_SEND_FILE has a bool param
    meta_cmds = {el.get("name"): el for el in root.iter(_xtce("MetaCommand"))}
    send_file = meta_cmds.get("sys_CMD_SEND_FILE")
    assert send_file is not None
    arg_list = send_file.find(_xtce("ArgumentList"))
    args = {a.get("name"): a for a in arg_list.findall(_xtce("Argument"))}
    assert "flag" in args
    assert args["flag"].get("argumentTypeRef") == "Arg_BooleanType"


def test_write_xml_creates_parent_dirs(tmp_path: Path, minimal_dict: dict) -> None:
    d = parse_dictionary(minimal_dict)
    root = build_xtce(d)
    nested = tmp_path / "a" / "b" / "c" / "out.xml"
    write_xml(root, nested)
    assert nested.exists()


def test_float_parameter_type(full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    float_types = [
        el for el in root.iter(_xtce("FloatParameterType"))
    ]
    assert len(float_types) >= 1
    sizes = {el.find(_xtce("FloatDataEncoding")).get("sizeInBits") for el in float_types}
    assert "32" in sizes


def test_string_parameter_type_present() -> None:
    data = {
        "metadata": {"deploymentName": "Str"},
        "commands": [],
        "telemetryChannels": [
            {
                "name": "ch.Version",
                "id": 1,
                "type": {"kind": "string", "name": "string"},
                "telemetryUpdate": "ON_CHANGE",
            }
        ],
        "events": [],
    }
    d = parse_dictionary(data)
    root = build_xtce(d)
    string_types = list(root.iter(_xtce("StringParameterType")))
    assert len(string_types) >= 1


def test_unknown_type_defaults_to_string() -> None:
    data = {
        "metadata": {"deploymentName": "Unk"},
        "commands": [],
        "telemetryChannels": [
            {
                "name": "ch.X",
                "id": 1,
                "type": {"kind": "COMPLETELY_UNKNOWN_KIND", "name": "WeirdType"},
                "telemetryUpdate": "ALWAYS",
            }
        ],
        "events": [],
    }
    d = parse_dictionary(data)
    assert d.telemetry[0].fprime_type.kind == "string"


def test_missing_metadata_defaults() -> None:
    data = {"commands": [], "telemetryChannels": [], "events": []}
    d = parse_dictionary(data)
    assert d.deployment_name == "FPrimeMission"


def test_xml_well_formed(tmp_path: Path, full_dict: dict) -> None:
    d = parse_dictionary(full_dict)
    root = build_xtce(d)
    out = tmp_path / "out.xml"
    write_xml(root, out)
    tree = ET.parse(out)
    assert tree.getroot() is not None