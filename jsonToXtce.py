#!/usr/bin/env python3
"""
jsonToXtce.py
Converts an FPrime deployment dictionary JSON to an XTCE XML file
suitable for import into YAMCS.

Usage: python3 jsonToXtce.py <dict.json> [--outdir <dir>]
"""

import json
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

XTCE_NS = "http://www.omg.org/spec/XTCE/20180204"
NS = f"{{{XTCE_NS}}}"


def tag(local):
    return f"{NS}{local}"


def xtce_name(fp_name):
    """Sanitize an FPrime dotted name to a valid XTCE identifier."""
    return fp_name.replace(".", "_").replace(" ", "_").replace("/", "_")


def build_type_registry(type_defs):
    """Build a lookup dict of typeDefinitions keyed by qualifiedName."""
    return {td["qualifiedName"]: td for td in type_defs}


def resolve_type(type_obj, registry):
    """
    Resolve a type object to its concrete definition.
    Follows qualifiedIdentifier references and alias chains until a
    concrete kind (integer, float, string, bool, enum, struct, array) is reached.
    """
    if type_obj is None:
        return None
    kind = type_obj.get("kind", "")
    if kind == "qualifiedIdentifier":
        name = type_obj["name"]
        td = registry.get(name)
        if td is None:
            return type_obj  # unknown — return as-is
        if td["kind"] == "alias":
            return resolve_type(td["type"], registry)
        return td
    if kind == "alias":
        return resolve_type(type_obj.get("type", type_obj), registry)
    return type_obj


def type_xtce_name(resolved):
    """Generate a unique, deterministic XTCE type name for a resolved FPrime type."""
    if resolved is None:
        return "Unknown_Type"
    kind = resolved.get("kind", "")
    if kind == "integer":
        prefix = "I" if resolved.get("signed", True) else "U"
        return f"{prefix}{resolved.get('size', '?')}_Type"
    if kind == "float":
        return f"F{resolved.get('size', '?')}_Type"
    if kind == "string":
        return f"String{resolved.get('size', '')}_Type"
    if kind == "bool":
        return "Bool_Type"
    if kind in ("enum", "struct", "array"):
        return xtce_name(resolved["qualifiedName"]) + "_Type"
    name = resolved.get("qualifiedName", resolved.get("name", "Unknown"))
    return xtce_name(name) + "_Type"


# ---------------------------------------------------------------------------
# Low-level type element builders
# ---------------------------------------------------------------------------

def _add_integer_type(parent, type_name, resolved, is_arg):
    kind_word = "Argument" if is_arg else "Parameter"
    signed = resolved.get("signed", True)
    size = resolved.get("size", 32)
    elem = ET.SubElement(parent, tag(f"Integer{kind_word}Type"), name=type_name,
                         signed="true" if signed else "false")
    enc = "twosComplement" if signed else "unsigned"
    ET.SubElement(elem, tag("IntegerDataEncoding"), sizeInBits=str(size), encoding=enc)
    ET.SubElement(elem, tag("UnitSet"))


def _add_float_type(parent, type_name, resolved, is_arg):
    kind_word = "Argument" if is_arg else "Parameter"
    size = resolved.get("size", 32)
    elem = ET.SubElement(parent, tag(f"Float{kind_word}Type"), name=type_name)
    ET.SubElement(elem, tag("FloatDataEncoding"), sizeInBits=str(size))
    ET.SubElement(elem, tag("UnitSet"))


def _add_string_type(parent, type_name, resolved, is_arg):
    kind_word = "Argument" if is_arg else "Parameter"
    char_count = resolved.get("size", 64)
    elem = ET.SubElement(parent, tag(f"String{kind_word}Type"), name=type_name)
    enc = ET.SubElement(elem, tag("StringDataEncoding"), encoding="UTF-8")
    size_el = ET.SubElement(enc, tag("SizeInBits"))
    fixed_el = ET.SubElement(size_el, tag("Fixed"))
    ET.SubElement(fixed_el, tag("FixedValue")).text = str(char_count * 8)
    ET.SubElement(elem, tag("UnitSet"))


def _add_bool_type(parent, type_name, is_arg):
    kind_word = "Argument" if is_arg else "Parameter"
    elem = ET.SubElement(parent, tag(f"Boolean{kind_word}Type"), name=type_name,
                         zeroStringValue="FALSE", oneStringValue="TRUE")
    ET.SubElement(elem, tag("IntegerDataEncoding"), sizeInBits="8", encoding="unsigned")
    ET.SubElement(elem, tag("UnitSet"))


def _add_enum_type(parent, type_name, resolved, is_arg):
    kind_word = "Argument" if is_arg else "Parameter"
    rep = resolved.get("representationType", {})
    size = rep.get("size", 8)
    elem = ET.SubElement(parent, tag(f"Enumerated{kind_word}Type"), name=type_name)
    ET.SubElement(elem, tag("IntegerDataEncoding"), sizeInBits=str(size), encoding="unsigned")
    enum_list = ET.SubElement(elem, tag("EnumerationList"))
    for const in resolved.get("enumeratedConstants", []):
        ET.SubElement(enum_list, tag("Enumeration"),
                      value=str(const["value"]),
                      label=const["name"])
    ET.SubElement(elem, tag("UnitSet"))


def _add_struct_type(parent, type_name, resolved, registry, seen, is_arg):
    """AggregateParameterType / AggregateArgumentType for FPrime structs."""
    kind_word = "Argument" if is_arg else "Parameter"
    members = resolved.get("members", {})
    sorted_members = sorted(members.items(), key=lambda kv: kv[1].get("index", 0))

    # Ensure all member types are registered first (correct declaration order).
    member_rows = []
    for mname, mdef in sorted_members:
        mresolved = resolve_type(mdef.get("type", {}), registry)
        mtname = type_xtce_name(mresolved)
        ensure_type(parent, mtname, mresolved, registry, seen, is_arg)
        member_rows.append((mname, mtname))

    elem = ET.SubElement(parent, tag(f"Aggregate{kind_word}Type"), name=type_name)
    mlist = ET.SubElement(elem, tag("MemberList"))
    for mname, mtname in member_rows:
        ET.SubElement(mlist, tag("Member"), name=mname, typeRef=mtname)


def _add_array_type(parent, type_name, resolved, registry, seen, is_arg):
    """Model fixed-size FPrime arrays as aggregate types with indexed members."""
    kind_word = "Argument" if is_arg else "Parameter"
    elem_resolved = resolve_type(resolved.get("elementType", {}), registry)
    elem_tname = type_xtce_name(elem_resolved)
    ensure_type(parent, elem_tname, elem_resolved, registry, seen, is_arg)

    size = resolved.get("size", 0)
    arr_elem = ET.SubElement(parent, tag(f"Aggregate{kind_word}Type"), name=type_name)
    mlist = ET.SubElement(arr_elem, tag("MemberList"))
    for i in range(size):
        ET.SubElement(mlist, tag("Member"), name=f"element{i}", typeRef=elem_tname)


def ensure_type(parent, type_name, resolved, registry, seen, is_arg=False):
    """
    Add a type XML element to parent if not already in seen.
    seen is a set that tracks type names already emitted to this type set.
    """
    if type_name in seen:
        return
    seen.add(type_name)  # mark first to guard against circular references

    if resolved is None:
        _add_integer_type(parent, type_name, {"kind": "integer", "size": 32, "signed": False}, is_arg)
        return

    kind = resolved.get("kind", "")
    if kind == "integer":
        _add_integer_type(parent, type_name, resolved, is_arg)
    elif kind == "float":
        _add_float_type(parent, type_name, resolved, is_arg)
    elif kind == "string":
        _add_string_type(parent, type_name, resolved, is_arg)
    elif kind == "bool":
        _add_bool_type(parent, type_name, is_arg)
    elif kind == "enum":
        _add_enum_type(parent, type_name, resolved, is_arg)
    elif kind == "struct":
        _add_struct_type(parent, type_name, resolved, registry, seen, is_arg)
    elif kind == "array":
        _add_array_type(parent, type_name, resolved, registry, seen, is_arg)
    else:
        # Fallback: treat as unsigned 32-bit integer
        _add_integer_type(parent, type_name, {"kind": "integer", "size": 32, "signed": False}, is_arg)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def build_telemetry_metadata(space_system, channels, events, registry):
    tlm = ET.SubElement(space_system, tag("TelemetryMetaData"))
    type_set = ET.SubElement(tlm, tag("ParameterTypeSet"))
    param_set = ET.SubElement(tlm, tag("ParameterSet"))
    seen_types = set()

    # --- Telemetry channels ---
    for ch in channels:
        name = xtce_name(ch.get("name", ""))
        resolved = resolve_type(ch.get("type"), registry)
        tname = type_xtce_name(resolved)
        ensure_type(type_set, tname, resolved, registry, seen_types, is_arg=False)

        attribs = {"name": name, "parameterTypeRef": tname}
        ann = ch.get("annotation", "")
        if ann:
            attribs["shortDescription"] = ann
        param = ET.SubElement(param_set, tag("Parameter"), **attribs)

        # Preserve FPrime-specific metadata in ancillary data
        anc_items = {}
        ch_id = ch.get("id")
        if ch_id is not None:
            anc_items["fprime_id"] = str(ch_id)
        update = ch.get("telemetryUpdate", "")
        if update:
            anc_items["fprime_update"] = update
        limits = ch.get("limits", {})
        if limits:
            anc_items["fprime_limits"] = json.dumps(limits)
        fmt = ch.get("format", "")
        if fmt:
            anc_items["fprime_format"] = fmt
        if anc_items:
            anc_set = ET.SubElement(param, tag("AncillaryDataSet"))
            for k, v in anc_items.items():
                ET.SubElement(anc_set, tag("AncillaryData"), name=k).text = v

    # --- Events (modeled as telemetry parameters) ---
    for ev in events:
        ev_name = xtce_name(ev.get("name", ""))
        formal_params = ev.get("formalParams", [])
        ann = ev.get("annotation", "")
        severity = ev.get("severity", "")

        if formal_params:
            ev_type_name = ev_name + "_Event_Type"
            if ev_type_name not in seen_types:
                seen_types.add(ev_type_name)
                # Ensure all member types exist first (correct declaration order).
                member_rows = []
                for fp in formal_params:
                    presolved = resolve_type(fp.get("type"), registry)
                    ptname = type_xtce_name(presolved)
                    ensure_type(type_set, ptname, presolved, registry, seen_types, is_arg=False)
                    member_rows.append((fp.get("name", "arg"), ptname))
                agg = ET.SubElement(type_set, tag("AggregateParameterType"), name=ev_type_name)
                mlist = ET.SubElement(agg, tag("MemberList"))
                for mname, mtname in member_rows:
                    ET.SubElement(mlist, tag("Member"), name=mname, typeRef=mtname)
            tname = ev_type_name
        else:
            tname = "U32_Type"
            ensure_type(type_set, tname,
                        {"kind": "integer", "size": 32, "signed": False},
                        registry, seen_types)

        attribs = {"name": ev_name, "parameterTypeRef": tname}
        if ann:
            attribs["shortDescription"] = ann
        param = ET.SubElement(param_set, tag("Parameter"), **attribs)

        anc_items = {}
        ev_id = ev.get("id")
        if ev_id is not None:
            anc_items["fprime_id"] = str(ev_id)
        if severity:
            anc_items["fprime_severity"] = severity
        fmt = ev.get("format", "")
        if fmt:
            anc_items["fprime_format"] = fmt
        throttle = ev.get("throttle")
        if throttle:
            anc_items["fprime_throttle"] = str(throttle.get("count", ""))
        if anc_items:
            anc_set = ET.SubElement(param, tag("AncillaryDataSet"))
            for k, v in anc_items.items():
                ET.SubElement(anc_set, tag("AncillaryData"), name=k).text = v


def build_command_metadata(space_system, commands, registry):
    cmd_meta = ET.SubElement(space_system, tag("CommandMetaData"))
    arg_type_set = ET.SubElement(cmd_meta, tag("ArgumentTypeSet"))
    metacmd_set = ET.SubElement(cmd_meta, tag("MetaCommandSet"))
    seen_types = set()

    for cmd in commands:
        name = xtce_name(cmd.get("name", ""))
        opcode = cmd.get("opcode", 0)
        annotation = cmd.get("annotation", "")
        formal_params = cmd.get("formalParams", [])

        attribs = {"name": name}
        if annotation:
            attribs["shortDescription"] = annotation
        metacmd = ET.SubElement(metacmd_set, tag("MetaCommand"), **attribs)

        if formal_params:
            arg_list = ET.SubElement(metacmd, tag("ArgumentList"))
            for fp in formal_params:
                pname = fp.get("name", "arg")
                presolved = resolve_type(fp.get("type"), registry)
                ptname = type_xtce_name(presolved)
                ensure_type(arg_type_set, ptname, presolved, registry, seen_types, is_arg=True)
                arg_attribs = {"name": pname, "argumentTypeRef": ptname}
                fp_ann = fp.get("annotation", "")
                if fp_ann:
                    arg_attribs["shortDescription"] = fp_ann
                ET.SubElement(arg_list, tag("Argument"), **arg_attribs)

        # Encode the opcode as a 32-bit fixed value at the start of the packet.
        container = ET.SubElement(metacmd, tag("CommandContainer"), name=name + "_CC")
        entry_list = ET.SubElement(container, tag("EntryList"))
        opcode_hex = f"{opcode:08X}" if isinstance(opcode, int) else "00000000"
        ET.SubElement(entry_list, tag("FixedValueEntry"),
                      name="opcode", binaryValue=opcode_hex, sizeInBits="32")
        for fp in formal_params:
            ET.SubElement(entry_list, tag("ArgumentRefEntry"),
                          argumentRef=fp.get("name", "arg"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def convert(dict_path, outdir):
    print(f"Loading {dict_path} ...")
    with open(dict_path, encoding="utf-8") as f:
        data = json.load(f)

    deployment = data.get("metadata", {}).get("deploymentName", "unknown")
    print(f"Deployment: {deployment}")

    registry = build_type_registry(data.get("typeDefinitions", []))
    print(f"  Type registry: {len(registry)} entries")

    ET.register_namespace("", XTCE_NS)
    root = ET.Element(tag("SpaceSystem"), name=deployment)
    root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root.set("xsi:noNamespaceSchemaLocation",
             "https://www.omg.org/spec/XTCE/20180204/SpaceSystem.xsd")

    channels = data.get("telemetryChannels", [])
    events = data.get("events", [])
    commands = data.get("commands", [])

    build_telemetry_metadata(root, channels, events, registry)
    print(f"  Telemetry channels: {len(channels)}")
    print(f"  Events: {len(events)}")

    build_command_metadata(root, commands, registry)
    print(f"  Commands: {len(commands)}")

    ET.indent(root, space="  ")

    stem = xtce_name(deployment)
    out_path = outdir / f"{stem}.xtce.xml"
    ET.ElementTree(root).write(str(out_path), xml_declaration=True, encoding="utf-8")
    print(f"  Written: {out_path}")
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="FPrime dict JSON → XTCE XML")
    parser.add_argument("json_file", help="Path to the FPrime deployment dictionary JSON")
    parser.add_argument("--outdir", default=".", help="Output directory (default: current dir)")
    args = parser.parse_args()

    dict_path = Path(args.json_file)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    convert(dict_path, outdir)


if __name__ == "__main__":
    main()
