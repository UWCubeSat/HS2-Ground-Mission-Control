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
XTCE_NS = "http://www.omg.org/space/xtce"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOC = (
    "http://www.omg.org/space/xtce "
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
        raise ValueError("Top-level JSON value must be an object.")
    return data


# ---------------------------------------------------------------------------
# Type parsing helpers
# ---------------------------------------------------------------------------

def _parse_enum_values(type_obj: dict[str, Any]) -> list[EnumValue]:
    values: list[EnumValue] = []
    for item in type_obj.get("enumeration", []):
        label = item.get("name", item.get("label", "UNKNOWN"))
        value = item.get("value", 0)
        try:
            value = int(value)
        except (TypeError, ValueError):
            logger.warning("Non-integer enum value for %s, defaulting to 0", label)
            value = 0
        values.append(EnumValue(label=label, value=value))
    return values


def _parse_type(type_obj: dict[str, Any] | None, context: str = "") -> FPrimeType:
    if type_obj is None:
        logger.warning("Missing type for %s, defaulting to U32", context)
        return FPrimeType(kind="integer", name="U32", signed=False, size=32)

    kind = type_obj.get("kind", "")
    name = type_obj.get("name", "")

    if kind == "integer":
        signed = type_obj.get("signed", True)
        size = type_obj.get("size", 32)
        prefix = "I" if signed else "U"
        resolved_name = name or f"{prefix}{size}"
        return FPrimeType(kind="integer", name=resolved_name, signed=signed, size=size)

    if kind == "float":
        size = type_obj.get("size", 32)
        resolved_name = name or f"F{size}"
        return FPrimeType(kind="float", name=resolved_name, size=size)

    if kind == "bool":
        return FPrimeType(kind="bool", name="bool")

    if kind == "string":
        return FPrimeType(kind="string", name="string")

    if kind == "qualifiedIdentifier":
        enum_values = _parse_enum_values(type_obj)
        if enum_values:
            return FPrimeType(kind="enum", name=name, enum_values=enum_values)
        return FPrimeType(kind="qualifiedIdentifier", name=name)

    if kind == "array":
        logger.warning("Array type not fully supported for %s, mapping as string", context)
        return FPrimeType(kind="string", name="string")

    if kind == "struct":
        logger.warning("Struct type not fully supported for %s, mapping as string", context)
        return FPrimeType(kind="string", name="string")

    logger.warning("Unknown type kind '%s' for %s, defaulting to string", kind, context)
    return FPrimeType(kind="string", name="string")


def _parse_formal_params(raw_params: list[dict[str, Any]]) -> list[FPrimeParam]:
    params: list[FPrimeParam] = []
    for p in raw_params:
        pname = p.get("name", "unnamed")
        ptype = _parse_type(p.get("type"), context=pname)
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

    # Commands
    for raw in data.get("commands", []):
        try:
            opcode = raw.get("opcode", 0)
            cmd = FPrimeCommand(
                name=raw.get("name", "UNKNOWN_CMD"),
                opcode=int(opcode),
                kind=raw.get("commandKind", "async"),
                params=_parse_formal_params(raw.get("formalParams", [])),
                description=raw.get("annotation", raw.get("comment", "")),
            )
            dictionary.commands.append(cmd)
            logger.debug("Parsed command: %s (0x%X)", cmd.name, cmd.opcode)
        except Exception as exc:
            logger.warning("Skipping command %s: %s", raw.get("name", "?"), exc)

    # Telemetry channels
    for raw in data.get("telemetryChannels", []):
        try:
            ch_id = raw.get("id", raw.get("channelId", 0))
            units_obj = raw.get("units")
            tlm = FPrimeTelemetry(
                name=raw.get("name", "UNKNOWN_CH"),
                channel_id=int(ch_id),
                fprime_type=_parse_type(raw.get("type"), context=raw.get("name", "?")),
                description=raw.get("annotation", raw.get("comment", "")),
                units=units_obj if isinstance(units_obj, str) else None,
                update_policy=raw.get("telemetryUpdate", ""),
                default_value=str(raw["default"]) if raw.get("default") is not None else None,
            )
            dictionary.telemetry.append(tlm)
            logger.debug("Parsed telemetry: %s (0x%X)", tlm.name, tlm.channel_id)
        except Exception as exc:
            logger.warning("Skipping telemetry %s: %s", raw.get("name", "?"), exc)

    # Events
    for raw in data.get("events", []):
        try:
            ev = FPrimeEvent(
                name=raw.get("name", "UNKNOWN_EVT"),
                event_id=int(raw.get("id", raw.get("eventId", 0))),
                severity=raw.get("severity", "DIAGNOSTIC"),
                format_string=raw.get("formatString", ""),
                params=_parse_formal_params(raw.get("formalParams", [])),
                description=raw.get("annotation", raw.get("comment", "")),
            )
            dictionary.events.append(ev)
            logger.debug("Parsed event: %s (0x%X)", ev.name, ev.event_id)
        except Exception as exc:
            logger.warning("Skipping event %s: %s", raw.get("name", "?"), exc)

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


def _xtce_type_name(fprime_type: FPrimeType, param_name: str = "") -> str:
    kind = fprime_type.kind
    if kind == "integer":
        prefix = "Integer" if fprime_type.signed else "Unsigned"
        return f"{prefix}_{fprime_type.size}Type"
    if kind == "float":
        return f"Float_{fprime_type.size}Type"
    if kind == "bool":
        return "BooleanType"
    if kind == "string":
        return "StringType"
    if kind in ("enum", "qualifiedIdentifier"):
        safe = fprime_type.name.replace(".", "_").replace(" ", "_")
        return f"Enum_{safe}Type"
    return "StringType"


def _argument_type_name(fprime_type: FPrimeType, param_name: str = "") -> str:
    base = _xtce_type_name(fprime_type, param_name)
    return f"Arg_{base}"


# ---------------------------------------------------------------------------
# XTCE XML builder
# ---------------------------------------------------------------------------

def _make_root(deployment_name: str) -> ET.Element:
    root = ET.Element(
        _xtce("SpaceSystem"),
        attrib={
            "name": deployment_name,
            f"{{{XSI_NS}}}schemaLocation": SCHEMA_LOC,
        },
    )
    header = ET.SubElement(root, _xtce("Header"))
    header.set("validationStatus", "Draft")
    header.set("classification", "NotClassified")
    history = ET.SubElement(header, _xtce("History"))
    entry = ET.SubElement(history, _xtce("HistoryEntry"))
    entry.set("purpose", "creation")
    ET.SubElement(entry, _xtce("Comment")).text = (
        f"Generated from F Prime dictionary for deployment: {deployment_name}"
    )
    return root


def _add_telemetry_parameter_types(
    type_set: ET.Element,
    tlm_list: list[FPrimeTelemetry],
    events: list[FPrimeEvent],
) -> set[str]:
    added: set[str] = set()

    def _add_integer(signed: bool, size: int) -> None:
        prefix = "Integer" if signed else "Unsigned"
        tname = f"{prefix}_{size}Type"
        if tname in added:
            return
        added.add(tname)
        el = ET.SubElement(type_set, _xtce("IntegerParameterType"), attrib={"name": tname})
        enc = ET.SubElement(el, _xtce("IntegerDataEncoding"))
        enc.set("sizeInBits", str(size))
        enc.set("encoding", "signed" if signed else "unsigned")

    def _add_float(size: int) -> None:
        tname = f"Float_{size}Type"
        if tname in added:
            return
        added.add(tname)
        el = ET.SubElement(type_set, _xtce("FloatParameterType"), attrib={"name": tname})
        enc = ET.SubElement(el, _xtce("FloatDataEncoding"))
        enc.set("sizeInBits", str(size))
        enc.set("encoding", "IEEE754_1985" if size in (32, 64) else "IEEE754_1985")

    def _add_bool() -> None:
        tname = "BooleanType"
        if tname in added:
            return
        added.add(tname)
        el = ET.SubElement(type_set, _xtce("BooleanParameterType"), attrib={"name": tname})
        enc = ET.SubElement(el, _xtce("IntegerDataEncoding"))
        enc.set("sizeInBits", "8")
        enc.set("encoding", "unsigned")

    def _add_string() -> None:
        tname = "StringType"
        if tname in added:
            return
        added.add(tname)
        el = ET.SubElement(type_set, _xtce("StringParameterType"), attrib={"name": tname})
        enc = ET.SubElement(el, _xtce("StringDataEncoding"))
        enc.set("encoding", "UTF-8")

    def _add_enum(fprime_type: FPrimeType) -> None:
        tname = _xtce_type_name(fprime_type)
        if tname in added:
            return
        added.add(tname)
        el = ET.SubElement(type_set, _xtce("EnumeratedParameterType"), attrib={"name": tname})
        enc = ET.SubElement(el, _xtce("IntegerDataEncoding"))
        enc.set("sizeInBits", "32")
        enc.set("encoding", "unsigned")
        enum_list = ET.SubElement(el, _xtce("EnumerationList"))
        for ev in fprime_type.enum_values:
            e = ET.SubElement(enum_list, _xtce("Enumeration"))
            e.set("label", ev.label)
            e.set("value", str(ev.value))

    def _dispatch(fprime_type: FPrimeType) -> None:
        k = fprime_type.kind
        if k == "integer":
            _add_integer(fprime_type.signed, fprime_type.size)
        elif k == "float":
            _add_float(fprime_type.size)
        elif k == "bool":
            _add_bool()
        elif k == "string":
            _add_string()
        elif k in ("enum", "qualifiedIdentifier"):
            if fprime_type.enum_values:
                _add_enum(fprime_type)
            else:
                _add_string()
        else:
            _add_string()

    for tlm in tlm_list:
        _dispatch(tlm.fprime_type)
    for evt in events:
        for p in evt.params:
            _dispatch(p.fprime_type)

    return added


def _add_argument_types(
    type_set: ET.Element,
    commands: list[FPrimeCommand],
) -> None:
    added: set[str] = set()

    def _add_int_arg(signed: bool, size: int) -> None:
        prefix = "Integer" if signed else "Unsigned"
        tname = f"Arg_Integer_{size}Type" if signed else f"Arg_Unsigned_{size}Type"
        if tname in added:
            return
        added.add(tname)
        el = ET.SubElement(type_set, _xtce("IntegerArgumentType"), attrib={"name": tname})
        enc = ET.SubElement(el, _xtce("IntegerDataEncoding"))
        enc.set("sizeInBits", str(size))
        enc.set("encoding", "signed" if signed else "unsigned")

    def _add_float_arg(size: int) -> None:
        tname = f"Arg_Float_{size}Type"
        if tname in added:
            return
        added.add(tname)
        el = ET.SubElement(type_set, _xtce("FloatArgumentType"), attrib={"name": tname})
        enc = ET.SubElement(el, _xtce("FloatDataEncoding"))
        enc.set("sizeInBits", str(size))
        enc.set("encoding", "IEEE754_1985")

    def _add_bool_arg() -> None:
        tname = "Arg_BooleanType"
        if tname in added:
            return
        added.add(tname)
        el = ET.SubElement(type_set, _xtce("IntegerArgumentType"), attrib={"name": tname})
        enc = ET.SubElement(el, _xtce("IntegerDataEncoding"))
        enc.set("sizeInBits", "8")
        enc.set("encoding", "unsigned")

    def _add_string_arg() -> None:
        tname = "Arg_StringType"
        if tname in added:
            return
        added.add(tname)
        el = ET.SubElement(type_set, _xtce("StringArgumentType"), attrib={"name": tname})
        enc = ET.SubElement(el, _xtce("StringDataEncoding"))
        enc.set("encoding", "UTF-8")

    def _add_enum_arg(fprime_type: FPrimeType) -> None:
        safe = fprime_type.name.replace(".", "_").replace(" ", "_")
        tname = f"Arg_Enum_{safe}Type"
        if tname in added:
            return
        added.add(tname)
        el = ET.SubElement(type_set, _xtce("EnumeratedArgumentType"), attrib={"name": tname})
        enc = ET.SubElement(el, _xtce("IntegerDataEncoding"))
        enc.set("sizeInBits", "32")
        enc.set("encoding", "unsigned")
        if fprime_type.enum_values:
            enum_list = ET.SubElement(el, _xtce("EnumerationList"))
            for ev in fprime_type.enum_values:
                e = ET.SubElement(enum_list, _xtce("Enumeration"))
                e.set("label", ev.label)
                e.set("value", str(ev.value))

    def _dispatch(fprime_type: FPrimeType) -> None:
        k = fprime_type.kind
        if k == "integer":
            _add_int_arg(fprime_type.signed, fprime_type.size)
        elif k == "float":
            _add_float_arg(fprime_type.size)
        elif k == "bool":
            _add_bool_arg()
        elif k == "string":
            _add_string_arg()
        elif k in ("enum", "qualifiedIdentifier"):
            if fprime_type.enum_values:
                _add_enum_arg(fprime_type)
            else:
                _add_string_arg()
        else:
            _add_string_arg()

    for cmd in commands:
        for p in cmd.params:
            _dispatch(p.fprime_type)


def _arg_type_ref(fprime_type: FPrimeType) -> str:
    k = fprime_type.kind
    if k == "integer":
        prefix = "Integer" if fprime_type.signed else "Unsigned"
        return f"Arg_{prefix}_{fprime_type.size}Type"
    if k == "float":
        return f"Arg_Float_{fprime_type.size}Type"
    if k == "bool":
        return "Arg_BooleanType"
    if k == "string":
        return "Arg_StringType"
    if k in ("enum", "qualifiedIdentifier"):
        if fprime_type.enum_values:
            safe = fprime_type.name.replace(".", "_").replace(" ", "_")
            return f"Arg_Enum_{safe}Type"
        return "Arg_StringType"
    return "Arg_StringType"


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

        # CommandContainer with opcode fixed value
        cc = ET.SubElement(mc, _xtce("CommandContainer"))
        cc.set("name", f"{cname}_CC")
        entry_list = ET.SubElement(cc, _xtce("EntryList"))

        for p in cmd.params:
            ae = ET.SubElement(entry_list, _xtce("ArgumentRefEntry"))
            ae.set("argumentRef", _safe_param_name(p.name))

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

    logger.info("Built CommandMetaData: %d commands", len(seen))


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

    # Verify parameterTypeRef links
    type_names = {
        el.get("name", "")
        for el in root.iter()
        if el.tag.endswith("ParameterType")
        or "EnumeratedParameterType" in el.tag
        or "IntegerParameterType" in el.tag
        or "FloatParameterType" in el.tag
        or "BooleanParameterType" in el.tag
        or "StringParameterType" in el.tag
    }
    for param in root.iter(_xtce("Parameter")):
        ref = param.get("parameterTypeRef", "")
        if ref and ref not in type_names:
            logger.warning(
                "Parameter '%s' references undefined type '%s'",
                param.get("name"),
                ref,
            )

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
    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)
        return 99

    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())