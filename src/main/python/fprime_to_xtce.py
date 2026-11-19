#!/usr/bin/env python3
"""
fprime_to_xtce.py

Converts F Prime deployment dictionary JSON files into XTCE XML
compatible with Yamcs mission database loading.

Usage:
    python fprime_to_xtce.py --input dictionary.json --output mission.xml
    python fprime_to_xtce.py --input dictionary.json --output mission.xml --validate --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.dom import minidom
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# XTCE namespace constants
# ---------------------------------------------------------------------------
XTCE_NS = "http://www.omg.org/spec/XTCE/20180204"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
FPRIME_OPCODE_SIZE_BYTES = 4  # FlightComputer uses an unsigned 32-bit FwOpcodeType.
SCHEMA_LOC = (
    "http://www.omg.org/spec/XTCE/20180204 "
    "https://www.omg.org/spec/XTCE/20180204/SpaceSystem.xsd"
)

ET.register_namespace("xtce", XTCE_NS)
ET.register_namespace("xsi", XSI_NS)

def _xtce(tag: str) -> str:
    return f"{{{XTCE_NS}}}{tag}"


# ---------------------------------------------------------------------------
# Data model dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EnumValue:
    label: str
    value: int


@dataclass
class FPrimeType:
    kind: str
    name: str
    signed: bool = True
    size: int = 32
    enum_values: list[EnumValue] = field(default_factory=list)

    # Array support
    element_type: FPrimeType | None = None
    array_size: int | None = None

    # Struct support
    # Ordered: (member name, member type) pairs
    members: list[tuple[str, FPrimeType]] = field(default_factory=list)

    # Strings
    string_size: int | None = None

@dataclass
class FPrimeParam:
    name: str
    fprime_type: FPrimeType
    description: str = ""
    default_value: str | None = None
    units: str | None = None


@dataclass
class FPrimeCommand:
    name: str
    opcode: int
    kind: str
    params: list[FPrimeParam]
    description: str = ""


@dataclass
class FPrimeTelemetry:
    name: str
    channel_id: int
    fprime_type: FPrimeType
    description: str = ""
    units: str | None = None
    update_policy: str = ""
    default_value: str | None = None


@dataclass
class FPrimeEvent:
    name: str
    event_id: int
    severity: str
    format_string: str
    params: list[FPrimeParam]
    description: str = ""


@dataclass
class FPrimeDictionary:
    deployment_name: str
    commands: list[FPrimeCommand] = field(default_factory=list)
    telemetry: list[FPrimeTelemetry] = field(default_factory=list)
    events: list[FPrimeEvent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# JSON loading
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"Input file is empty: {path}")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise TypeError("Top-level JSON value must be an object.")
    return data


# ---------------------------------------------------------------------------
# Type parsing helpers
# ---------------------------------------------------------------------------

def _parse_enum_values(type_obj: dict[str, Any]) -> list[EnumValue]:

    items = type_obj.get(
        "enumeratedConstants",
        type_obj.get("enumeration", []),
    )

    if not isinstance(items, list):
        raise TypeError("Enum constants must be a list")

    values = []

    for item in items:
        if not isinstance(item, dict):
            raise TypeError(
                f"Invalid enum constant: {item!r}"
            )

        values.append(
            EnumValue(
                label=item.get(
                    "name",
                    item.get("label", "UNKNOWN"),
                ),
                value=int(item["value"]),
            )
        )

    return values

def _parse_type(type_obj: dict[str, Any] | None, context: str = "", type_definitions: dict[str, Any] | None = None, resolving: frozenset[str] = frozenset(),) -> FPrimeType:

    if not isinstance(type_obj, dict):
        raise TypeError(f"Missing or invalid type for {context}")

    kind = type_obj.get("kind", "")
    name = type_obj.get("name", type_obj.get("qualifiedName", ""))

    # Resolve named F Prime types
    if kind == "qualifiedIdentifier":
        definition = (type_definitions or {}).get(name)

        if definition is None:
            raise ValueError(
                f"Unknown named type {name!r} in {context}"
            )

        if name in resolving:
            raise ValueError(
                f"Recursive type definition involving {name!r}"
            )

        resolved = resolving | {name}

        if definition.get("kind") == "alias":
            return _parse_type(
                definition.get("type"),
                context,
                type_definitions,
                resolved,
            )

        return _parse_type(
            definition,
            context,
            type_definitions,
            resolved,
        )

    # Integers
    if kind == "integer":
        signed = bool(type_obj.get("signed", True))
        size = int(type_obj.get("size", 32))

        return FPrimeType(
            kind="integer",
            name=name or f"{'I' if signed else 'U'}{size}",
            signed=signed,
            size=size,
        )

    # Floating-point numbers
    if kind == "float":
        size = int(type_obj.get("size", 32))

        return FPrimeType(
            kind="float",
            name=name or f"F{size}",
            size=size,
        )

    # Booleans
    if kind == "bool":
        return FPrimeType(kind="bool", name="bool")

    # Strings
    if kind == "string":
        size = type_obj.get("size")

        return FPrimeType(
            kind="string",
            name="string",
            string_size=int(size) if size is not None else None,
        )

    # Enums
    if kind == "enum":
        representation = type_obj.get("representationType", {})

        return FPrimeType(
            kind="enum",
            name=name,
            signed=bool(representation.get("signed", False)),
            size=int(representation.get("size", 32)),
            enum_values=_parse_enum_values(type_obj),
        )

    # Arrays
    if kind == "array":
        count = int(type_obj["size"])

        if count < 1:
            raise ValueError(
                f"Invalid array size for {name}: {count}"
            )

        element_type = _parse_type(
            type_obj.get("elementType"),
            context=f"{context}[]",
            type_definitions=type_definitions,
            resolving=resolving,
        )

        return FPrimeType(
            kind="array",
            name=name or context,
            array_size=count,
            element_type=element_type,
        )

    # Structs
    if kind == "struct":
        raw_members = type_obj.get("members")

        if not isinstance(raw_members, dict):
            raise ValueError(
                f"Invalid struct members for {name}"
            )

        ordered = sorted(
            raw_members.items(),
            key=lambda item: int(item[1]["index"]),
        )

        indexes = [
            int(member["index"])
            for _, member in ordered
        ]

        if indexes != list(range(len(indexes))):
            raise ValueError(
                f"Invalid member indexes in {name}"
            )

        members = []

        for member_name, member in ordered:
            member_type = _parse_type(
                member["type"],
                context=f"{context}.{member_name}",
                type_definitions=type_definitions,
                resolving=resolving,
            )

            members.append((member_name, member_type))

        return FPrimeType(
            kind="struct",
            name=name or context,
            members=members,
        )

    raise ValueError(
        f"Unsupported type kind {kind!r} for {context}"
    )


def _parse_formal_params(raw_params: list[dict[str, Any]], type_definitions: dict[str, Any] | None = None,) -> list[FPrimeParam]:
    params: list[FPrimeParam] = []
    for p in raw_params:
        pname = p.get("name", "unnamed")
        ptype = _parse_type(p.get("type"), context=pname, type_definitions=type_definitions,)
        desc = p.get("annotation", p.get("comment", ""))
        default = p.get("default")
        units_obj = p.get("units")
        units = units_obj if isinstance(units_obj, str) else None
        params.append(
            FPrimeParam(
                name=pname,
                fprime_type=ptype,
                description=desc,
                default_value=str(default) if default is not None else None,
                units=units,
            )
        )
    return params


# ---------------------------------------------------------------------------
# Dictionary parsing
# ---------------------------------------------------------------------------

def parse_dictionary(data: dict[str, Any]) -> FPrimeDictionary:
    metadata = data.get("metadata", {})
    deployment_name = metadata.get("deploymentName", "FPrimeMission")

    dictionary = FPrimeDictionary(deployment_name=deployment_name)

    # Named types defined in the F Prime dictionary
    type_definitions = {
                        definition["qualifiedName"]: definition
                        for definition in data.get("typeDefinitions", [])
                        }

    # Commands
    for raw in data.get("commands", []):
        try:
            opcode = raw.get("opcode", 0)
            cmd = FPrimeCommand(
                name=raw.get("name", "UNKNOWN_CMD"),
                opcode=int(opcode),
                kind=raw.get("commandKind", "async"),
                params=_parse_formal_params(raw.get("formalParams", []), type_definitions=type_definitions),
                description=raw.get("annotation", raw.get("comment", "")),
            )
            dictionary.commands.append(cmd)
            logger.debug("Parsed command: %s (0x%X)", cmd.name, cmd.opcode)
        except Exception as exc:
             raise ValueError(
                f"Cannot parse command {raw.get('name', '?')}: {exc}"
            ) from exc

    # Telemetry channels
    for raw in data.get("telemetryChannels", []):
        try:
            ch_id = raw.get("id", raw.get("channelId", 0))
            units_obj = raw.get("units")
            tlm = FPrimeTelemetry(
                name=raw.get("name", "UNKNOWN_CH"),
                channel_id=int(ch_id),
                fprime_type=_parse_type(raw.get("type"), context=raw.get("name", "?"), type_definitions=type_definitions),
                description=raw.get("annotation", raw.get("comment", "")),
                units=units_obj if isinstance(units_obj, str) else None,
                update_policy=raw.get("telemetryUpdate", ""),
                default_value=str(raw["default"]) if raw.get("default") is not None else None,
            )
            dictionary.telemetry.append(tlm)
            logger.debug("Parsed telemetry: %s (0x%X)", tlm.name, tlm.channel_id)
        except Exception as exc:
             raise ValueError(
                f"Cannot parse telemetry {raw.get('name', '?')}: {exc}"
            ) from exc

    # Events
    for raw in data.get("events", []):
        try:
            ev = FPrimeEvent(
                name=raw.get("name", "UNKNOWN_EVT"),
                event_id=int(raw.get("id", raw.get("eventId", 0))),
                severity=raw.get("severity", "DIAGNOSTIC"),
                format_string=raw.get("formatString", ""),
                params=_parse_formal_params(raw.get("formalParams", []), type_definitions=type_definitions),
                description=raw.get("annotation", raw.get("comment", "")),
            )
            dictionary.events.append(ev)
            logger.debug("Parsed event: %s (0x%X)", ev.name, ev.event_id)
        except Exception as exc:
            raise ValueError(
                f"Cannot parse event {raw.get('name', '?')}: {exc}"
            ) from exc

    logger.info(
        "Parsed dictionary '%s': %d commands, %d telemetry channels, %d events",
        deployment_name,
        len(dictionary.commands),
        len(dictionary.telemetry),
        len(dictionary.events),
    )
    return dictionary


# ---------------------------------------------------------------------------
# XTCE type name mapping
# ---------------------------------------------------------------------------

_SEEN_ENUM_TYPES: dict[str, str] = {}

def _xtce_type_name(fprime_type: FPrimeType, param_name: str = "",) -> str:
    kind = fprime_type.kind

    if kind == "integer":
        prefix = "Integer" if fprime_type.signed else "Unsigned"
        return f"{prefix}_{fprime_type.size}Type"

    if kind == "float":
        return f"Float_{fprime_type.size}Type"

    if kind == "bool":
        return "BooleanType"

    if kind == "string":
        if fprime_type.string_size is not None:
            return f"String_{fprime_type.string_size}Type"
        return "StringType"

    if kind == "array":
        return f"Array_{_safe_param_name(fprime_type.name)}Type"

    if kind == "struct":
        return f"Struct_{_safe_param_name(fprime_type.name)}Type"

    if kind == "enum":
        return f"Enum_{_safe_param_name(fprime_type.name)}Type"

    raise ValueError(f"Unsupported type: {kind}")


def _argument_type_name(fprime_type: FPrimeType, param_name: str = "",) -> str:
    return f"Arg_{_xtce_type_name(fprime_type, param_name)}"

def _add_types(type_set: ET.Element, types: list[FPrimeType], argument: bool,) -> None:
    """Generate XTCE types, including nested arrays and structs."""

    added: set[str] = set()

    suffix = "ArgumentType" if argument else "ParameterType"

    def ref(t: FPrimeType) -> str:
        if argument:
            return _argument_type_name(t)
        return _xtce_type_name(t)

    def add(t: FPrimeType) -> None:
        tname = ref(t)

        if tname in added:
            return

        # Generate dependencies first
        if t.kind == "array":
            if t.element_type is None or t.array_size is None:
                raise ValueError(f"Incomplete array {t.name}")

            add(t.element_type)

        elif t.kind == "struct":
            for _, member_type in t.members:
                add(member_type)

        added.add(tname)

        kind_tags = {
            "integer": "Integer",
            "float": "Float",
            "bool": "Boolean",
            "string": "String",
            "enum": "Enumerated",
            "array": "Array",
            "struct": "Aggregate",
        }

        if t.kind not in kind_tags:
            raise ValueError(
                f"Cannot generate XTCE type for {t.kind}: {t.name}"
            )

        el = ET.SubElement(
            type_set,
            _xtce(kind_tags[t.kind] + suffix),
            {"name": tname},
        )

        # Integer, enum, and boolean encoding
        if t.kind in ("integer", "enum", "bool"):

            # Define binary encoding
            enc = ET.SubElement(
                el,
                _xtce("IntegerDataEncoding"),
            )

            enc.set(
                "sizeInBits",
                str(8 if t.kind == "bool" else t.size),
            )

            enc.set(
                "encoding",
                "twosComplement"
                if t.signed and t.kind != "bool"
                else "unsigned",
            )

            # XTCE places enum values after the binary encoding.
            if t.kind == "enum":
                enum_list = ET.SubElement(
                    el,
                    _xtce("EnumerationList"),
                )

                for ev in t.enum_values:
                    ET.SubElement(
                        enum_list,
                        _xtce("Enumeration"),
                        {
                            "label": ev.label,
                            "value": str(ev.value),
                        },
                    )

        # Floating-point encoding
        elif t.kind == "float":
            enc = ET.SubElement(
                el,
                _xtce("FloatDataEncoding"),
            )

            enc.set("sizeInBits", str(t.size))
            enc.set("encoding", "IEEE754_1985")

        # String encoding
        elif t.kind == "string":
            enc = ET.SubElement(
                el,
                _xtce("StringDataEncoding"),
            )

            enc.set("encoding", "UTF-8")

            if t.string_size is not None:
                size = ET.SubElement(
                    enc,
                    _xtce("SizeInBits"),
                )

                fixed = ET.SubElement(
                    size,
                    _xtce("Fixed"),
                )

                ET.SubElement(
                    fixed,
                    _xtce("FixedValue"),
                ).text = str(t.string_size * 8)

        # Array encoding
        elif t.kind == "array":
            el.set("arrayTypeRef", ref(t.element_type))

            dimensions = ET.SubElement(
                el,
                _xtce("DimensionList"),
            )

            dim = ET.SubElement(
                dimensions,
                _xtce("Dimension"),
            )

            starting = ET.SubElement(
                dim,
                _xtce("StartingIndex"),
            )

            ET.SubElement(
                starting,
                _xtce("FixedValue"),
            ).text = "0"

            ending = ET.SubElement(
                dim,
                _xtce("EndingIndex"),
            )

            ET.SubElement(
                ending,
                _xtce("FixedValue"),
            ).text = str(t.array_size - 1)

        # Struct encoding
        elif t.kind == "struct":
            member_list = ET.SubElement(
                el,
                _xtce("MemberList"),
            )

            for member_name, member_type in t.members:
                ET.SubElement(
                    member_list,
                    _xtce("Member"),
                    {
                        "name": member_name,
                        "typeRef": ref(member_type),
                    },
                )

    for t in types:
        add(t)


def _add_telemetry_parameter_types(type_set: ET.Element, tlm_list: list[FPrimeTelemetry], events: list[FPrimeEvent],) -> set[str]:

    types = [
        tlm.fprime_type
        for tlm in tlm_list
    ]

    types.extend(
        p.fprime_type
        for evt in events
        for p in evt.params
    )

    _add_types(
        type_set,
        types,
        argument=False,
    )

    return {
        el.get("name", "")
        for el in type_set
    }


def _add_argument_types(type_set: ET.Element, commands: list[FPrimeCommand],) -> None:

    types = [
        p.fprime_type
        for cmd in commands
        for p in cmd.params
    ]

    _add_types(
        type_set,
        types,
        argument=True,
    )

def _arg_type_ref(fprime_type: FPrimeType) -> str:
    return _argument_type_name(fprime_type)


def _safe_param_name(name: str) -> str:
    return name.replace(".", "_").replace(" ", "_").replace("-", "_")


def _add_telemetry_meta(
    space_system: ET.Element,
    dictionary: FPrimeDictionary,
) -> None:
    tlm_meta = ET.SubElement(space_system, _xtce("TelemetryMetaData"))

    # ParameterTypeSet
    type_set = ET.SubElement(tlm_meta, _xtce("ParameterTypeSet"))
    _add_telemetry_parameter_types(type_set, dictionary.telemetry, dictionary.events)

    # ParameterSet
    param_set = ET.SubElement(tlm_meta, _xtce("ParameterSet"))
    seen: set[str] = set()

    for tlm in dictionary.telemetry:
        pname = _safe_param_name(tlm.name)
        if pname in seen:
            logger.warning("Duplicate telemetry parameter name '%s', skipping", pname)
            continue
        seen.add(pname)
        p = ET.SubElement(param_set, _xtce("Parameter"))
        p.set("name", pname)
        p.set("parameterTypeRef", _xtce_type_name(tlm.fprime_type))
        if tlm.description:
            desc = ET.SubElement(p, _xtce("LongDescription"))
            desc.text = tlm.description

    for evt in dictionary.events:
        for ep in evt.params:
            epname = _safe_param_name(f"{evt.name}_{ep.name}")
            if epname in seen:
                continue
            seen.add(epname)
            p = ET.SubElement(param_set, _xtce("Parameter"))
            p.set("name", epname)
            p.set("parameterTypeRef", _xtce_type_name(ep.fprime_type))

    # ContainerSet — one sequence container per telemetry channel
    container_set = ET.SubElement(tlm_meta, _xtce("ContainerSet"))
    for tlm in dictionary.telemetry:
        pname = _safe_param_name(tlm.name)
        container_name = f"{pname}_Container"
        sc = ET.SubElement(container_set, _xtce("SequenceContainer"))
        sc.set("name", container_name)
        if tlm.description:
            ld = ET.SubElement(sc, _xtce("LongDescription"))
            ld.text = tlm.description
        entry_list = ET.SubElement(sc, _xtce("EntryList"))
        pe = ET.SubElement(entry_list, _xtce("ParameterRefEntry"))
        pe.set("parameterRef", pname)

    logger.info(
        "Built TelemetryMetaData: %d parameters, %d containers",
        len(seen),
        len(dictionary.telemetry),
    )


def _add_command_meta(
    space_system: ET.Element,
    dictionary: FPrimeDictionary,
) -> None:
    cmd_meta = ET.SubElement(space_system, _xtce("CommandMetaData"))

    # ArgumentTypeSet
    arg_type_set = ET.SubElement(cmd_meta, _xtce("ArgumentTypeSet"))
    _add_argument_types(arg_type_set, dictionary.commands)

    # MetaCommandSet
    meta_cmd_set = ET.SubElement(cmd_meta, _xtce("MetaCommandSet"))
    seen: set[str] = set()

    for cmd in dictionary.commands:
        cname = _safe_param_name(cmd.name)
        if cname in seen:
            logger.warning("Duplicate command name '%s', skipping", cname)
            continue
        seen.add(cname)

        mc = ET.SubElement(meta_cmd_set, _xtce("MetaCommand"))
        mc.set("name", cname)
        mc.set("abstract", "false")

        if cmd.description:
            ld = ET.SubElement(mc, _xtce("LongDescription"))
            ld.text = cmd.description

        # Ancillary data for opcode
        ancillary = ET.SubElement(mc, _xtce("AncillaryDataSet"))
        ad = ET.SubElement(ancillary, _xtce("AncillaryData"))
        ad.set("name", "opcode")
        ad.text = str(cmd.opcode)

        ad_hex = ET.SubElement(ancillary, _xtce("AncillaryData"))
        ad_hex.set("name", "opcode_hex")
        ad_hex.text = hex(cmd.opcode)

        ad_kind = ET.SubElement(ancillary, _xtce("AncillaryData"))
        ad_kind.set("name", "commandKind")
        ad_kind.text = cmd.kind

        # Argument list
        if cmd.params:
            arg_list = ET.SubElement(mc, _xtce("ArgumentList"))
            for p in cmd.params:
                arg = ET.SubElement(arg_list, _xtce("Argument"))
                arg.set("name", _safe_param_name(p.name))
                arg.set("argumentTypeRef", _arg_type_ref(p.fprime_type))
                if p.description:
                    ld = ET.SubElement(arg, _xtce("LongDescription"))
                    ld.text = p.description

        # F Prime command data starts with the big-endian opcode before arguments.
        cc = ET.SubElement(mc, _xtce("CommandContainer"))
        cc.set("name", f"{cname}_CC")
        entry_list = ET.SubElement(cc, _xtce("EntryList"))
        try:
            opcode_bytes = cmd.opcode.to_bytes(FPRIME_OPCODE_SIZE_BYTES, "big")
        except OverflowError as exc:
            raise ValueError(f"Opcode for {cmd.name} is outside the unsigned 32-bit range: {cmd.opcode}") from exc
        ET.SubElement(
            entry_list,
            _xtce("FixedValueEntry"),
            name="opcode",
            binaryValue=opcode_bytes.hex().upper(),
            sizeInBits=str(FPRIME_OPCODE_SIZE_BYTES * 8),
        )

        for p in cmd.params:
            ae = ET.SubElement(entry_list, _xtce("ArgumentRefEntry"))
            ae.set("argumentRef", _safe_param_name(p.name))

    logger.info("Built CommandMetaData: %d commands", len(seen))


def _make_root(deployment_name: str) -> ET.Element:
    """Create the XTCE SpaceSystem root element."""
    # XTCE identifiers cannot contain dots or spaces.
    safe_name = _safe_param_name(deployment_name)
    root = ET.Element(
        _xtce("SpaceSystem"),
        {
            "name": safe_name,
            f"{{{XSI_NS}}}schemaLocation": SCHEMA_LOC,
        },
    )
    header = ET.SubElement(root, _xtce("Header"))
    header.set("version", "1.0")
    header.set("date", "2026-11-18")
    header.set("validationStatus", "Unknown")
    return root


def build_xtce(dictionary: FPrimeDictionary) -> ET.Element:
    root = _make_root(dictionary.deployment_name)
    _add_telemetry_meta(root, dictionary)
    _add_command_meta(root, dictionary)
    return root


# ---------------------------------------------------------------------------
# XML writing
# ---------------------------------------------------------------------------

def write_xml(root: ET.Element, output_path: Path) -> None:
    try:
        raw = ET.tostring(root, encoding="unicode", xml_declaration=False)
        dom = minidom.parseString(raw)
        pretty = dom.toprettyxml(indent="  ", encoding="UTF-8")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(pretty)
        logger.info("XTCE written to %s", output_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to write XML: {exc}") from exc


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_xtce(path: Path) -> bool:
    from xml.etree import ElementTree as _ET

    logger.info("Validating %s ...", path)
    ok = True

    try:
        tree = _ET.parse(path)
        root = tree.getroot()
        logger.info("XML is well-formed.")
    except _ET.ParseError as exc:
        logger.error("XML is NOT well-formed: %s", exc)
        return False

    # Check root element
    expected_tag = _xtce("SpaceSystem")
    if root.tag != expected_tag:
        logger.warning(
            "Root element is <%s>, expected <xtce:SpaceSystem>", root.tag
        )
        ok = False

    # Check for duplicate parameter names
    param_names: list[str] = [
        el.get("name", "")
        for el in root.iter(_xtce("Parameter"))
    ]
    duplicates = {n for n in param_names if param_names.count(n) > 1}
    if duplicates:
        logger.error("Duplicate parameter names detected: %s", duplicates)
        ok = False

    # Check for duplicate MetaCommand names
    cmd_names: list[str] = [
        el.get("name", "")
        for el in root.iter(_xtce("MetaCommand"))
    ]
    dup_cmds = {n for n in cmd_names if cmd_names.count(n) > 1}
    if dup_cmds:
        logger.error("Duplicate MetaCommand names detected: %s", dup_cmds)
        ok = False

        # Collect defined parameter and argument types
    parameter_types = {
        el.get("name")
        for el in root.iter()
        if el.tag.startswith(f"{{{XTCE_NS}}}")
        and el.tag.endswith("ParameterType")
    }

    argument_types = {
        el.get("name")
        for el in root.iter()
        if el.tag.startswith(f"{{{XTCE_NS}}}")
        and el.tag.endswith("ArgumentType")
    }

    # Validate telemetry parameter references
    for param in root.iter(_xtce("Parameter")):
        ref = param.get("parameterTypeRef")
        if not ref or ref not in parameter_types:
            logger.error(
                "Parameter '%s' references undefined type '%s'",
                param.get("name"),
                ref,
            )
            ok = False

    # Validate command argument references
    for arg in root.iter(_xtce("Argument")):
        ref = arg.get("argumentTypeRef")
        if not ref or ref not in argument_types:
            logger.error(
                "Argument '%s' references undefined type '%s'",
                arg.get("name"),
                ref,
            )
            ok = False

    # Validate nested array and struct type references
    for el in root.iter():
        if el.tag in (
            _xtce("ArrayParameterType"),
            _xtce("AggregateParameterType"),
        ):
            valid_types = parameter_types
        elif el.tag in (
            _xtce("ArrayArgumentType"),
            _xtce("AggregateArgumentType"),
        ):
            valid_types = argument_types
        else:
            continue

        if el.tag in (
            _xtce("ArrayParameterType"),
            _xtce("ArrayArgumentType"),
        ):
            ref = el.get("arrayTypeRef")
            if not ref or ref not in valid_types:
                logger.error(
                    "Array type '%s' references undefined element type '%s'",
                    el.get("name"),
                    ref,
                )
                ok = False

        else:
            for member in el.findall(_xtce("MemberList") + "/" + _xtce("Member")):
                ref = member.get("typeRef")
                if not ref or ref not in valid_types:
                    logger.error(
                        "Struct '%s' member '%s' references undefined type '%s'",
                        el.get("name"),
                        member.get("name"),
                        ref,
                    )
                    ok = False

    if ok:
        logger.info("Validation passed.")
    else:
        logger.warning("Validation completed with issues.")
    return ok


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
        description="Convert F Prime JSON dictionary to XTCE XML for Yamcs."
    )
    parser.add_argument("--input", required=True, help="Path to F Prime dictionary JSON")
    parser.add_argument("--output", required=True, help="Path for generated XTCE XML")
    parser.add_argument(
        "--validate", action="store_true", help="Validate the generated XTCE XML"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    input_path = Path(args.input)
    output_path = Path(args.output)

    try:
        logger.info("Loading JSON: %s", input_path)
        raw = load_json(input_path)

        logger.info("Parsing F Prime dictionary...")
        dictionary = parse_dictionary(raw)

        logger.info("Building XTCE...")
        xtce_root = build_xtce(dictionary)

        logger.info("Writing XTCE XML: %s", output_path)
        write_xml(xtce_root, output_path)

        if args.validate:
            ok = validate_xtce(output_path)
            if not ok:
                logger.error("Validation failed.")
                return 1

    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2
    except ValueError as exc:
        logger.error("Input error: %s", exc)
        return 3
    except RuntimeError as exc:
        logger.error("Runtime error: %s", exc)
        return 4
    except Exception:
        logger.exception("Unexpected error")
        return 99

    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
