#!/usr/bin/env python3
"""
fprime_dict_to_csv.py
Parses an FPrime deployment dictionary JSON and outputs two CSVs:
  - commands.csv
  - telemetry.csv
Usage: python3 fprime_dict_to_csv.py <dict.json> [--outdir <dir>]
"""

import json
import csv
import argparse
from pathlib import Path


def format_type(type_obj):
    """Return a human-readable string for an FPrime type object."""
    if type_obj is None:
        return ""
    kind = type_obj.get("kind", "")
    name = type_obj.get("name", "")
    if kind == "integer":
        signed = type_obj.get("signed", True)
        size = type_obj.get("size", "?")
        prefix = "I" if signed else "U"
        return f"{prefix}{size}"
    elif kind == "float":
        size = type_obj.get("size", "?")
        return f"F{size}"
    elif kind in ("qualifiedIdentifier", "string"):
        return name
    elif kind == "bool":
        return "bool"
    else:
        return name or kind


def format_params(formal_params):
    """Format a list of formal parameters into 'name: type' strings."""
    if not formal_params:
        return ""
    parts = []
    for p in formal_params:
        pname = p.get("name", "?")
        ptype = format_type(p.get("type"))
        parts.append(f"{pname}: {ptype}")
    return "; ".join(parts)


def parse_commands(commands):
    rows = []
    for cmd in commands:
        name = cmd.get("name", "")
        opcode = cmd.get("opcode", "")
        kind = cmd.get("commandKind", "")
        formal_params = cmd.get("formalParams", [])
        num_params = len(formal_params)
        param_str = format_params(formal_params)
        annotation = cmd.get("annotation", "")
        rows.append({
            "Name": name,
            "Opcode (dec)": opcode,
            "Opcode (hex)": hex(opcode) if isinstance(opcode, int) else opcode,
            "Kind": kind,
            "Num Params": num_params,
            "Params (name: type)": param_str,
            "Annotation": annotation,
        })
    return rows


def parse_telemetry(channels):
    rows = []
    for ch in channels:
        name = ch.get("name", "")
        ch_id = ch.get("id", "")
        type_str = format_type(ch.get("type"))
        update_policy = ch.get("telemetryUpdate", "")
        annotation = ch.get("annotation", "")
        rows.append({
            "Name": name,
            "ID (dec)": ch_id,
            "ID (hex)": hex(ch_id) if isinstance(ch_id, int) else ch_id,
            "Type": type_str,
            "Update Policy": update_policy,
            "Annotation": annotation,
        })
    return rows


def write_csv(rows, fieldnames, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Written: {path}  ({len(rows)} rows)")


def main():
    parser = argparse.ArgumentParser(description="FPrime dict JSON → CSV")
    parser.add_argument("json_file", help="Path to the FPrime deployment dictionary JSON")
    parser.add_argument("--outdir", default=".", help="Output directory (default: current dir)")
    args = parser.parse_args()

    json_path = Path(args.json_file)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"Loading {json_path} ...")
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    deployment = data.get("metadata", {}).get("deploymentName", "unknown")
    print(f"Deployment: {deployment}")

    # Commands
    cmd_rows = parse_commands(data.get("commands", []))
    cmd_fields = ["Name", "Opcode (dec)", "Opcode (hex)", "Kind", "Num Params", "Params (name: type)", "Annotation"]
    write_csv(cmd_rows, cmd_fields, outdir / "commands.csv")

    # Telemetry
    tlm_rows = parse_telemetry(data.get("telemetryChannels", []))
    tlm_fields = ["Name", "ID (dec)", "ID (hex)", "Type", "Update Policy", "Annotation"]
    write_csv(tlm_rows, tlm_fields, outdir / "telemetry.csv")

    print("Done.")


if __name__ == "__main__":
    main()