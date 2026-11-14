#!/usr/bin/env python3
"""
validate_xtce.py

Validates an XTCE XML file for correctness and Yamcs compatibility.

Usage:
    python validate_xtce.py --input mission.xml
    python validate_xtce.py --input mission.xml --verbose
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

XTCE_NS = "http://www.omg.org/space/xtce"


def _xtce(tag: str) -> str:
    """Return a fully-qualified XTCE tag name."""
    return f"{{{XTCE_NS}}}{tag}"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Holds the outcome of a single validation check."""

    name: str
    passed: bool
    message: str


@dataclass
class ValidationReport:
    """Aggregates all validation results for a single XTCE file."""

    path: Path
    results: list[ValidationResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Return True only if every check passed."""
        return all(r.passed for r in self.results)

    def add(self, name: str, passed: bool, message: str) -> None:
        """Append a single result."""
        self.results.append(ValidationResult(name=name, passed=passed, message=message))

    def print_summary(self) -> None:
        """Print a human-readable summary to stdout."""
        width = 60
        print(f"\n{'=' * width}")
        print(f"  XTCE Validation Report: {self.path.name}")
        print(f"{'=' * width}")
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            marker = "✓" if r.passed else "✗"
            print(f"  [{status}] {marker} {r.name}")
            if not r.passed or logger.isEnabledFor(logging.DEBUG):
                print(f"         {r.message}")
        print(f"{'=' * width}")
        overall = "ALL CHECKS PASSED" if self.passed else "VALIDATION FAILED"
        print(f"  Overall: {overall}")
        print(f"{'=' * width}\n")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_well_formed(path: Path) -> tuple[bool, str, ET.Element | None]:
    """Parse the XML file and return (ok, message, root_element)."""
    try:
        tree = ET.parse(path)
        return True, "XML is well-formed.", tree.getroot()
    except ET.ParseError as exc:
        return False, f"XML parse error: {exc}", None


def check_root_element(root: ET.Element) -> tuple[bool, str]:
    """Verify the root element is xtce:SpaceSystem."""
    expected = _xtce("SpaceSystem")
    if root.tag == expected:
        name = root.get("name", "<unnamed>")
        return True, f"Root is SpaceSystem name='{name}'."
    return False, f"Root element is <{root.tag}>, expected <xtce:SpaceSystem>."


def check_required_sections(root: ET.Element) -> list[ValidationResult]:
    """Check that TelemetryMetaData and CommandMetaData exist with sub-sections."""
    results: list[ValidationResult] = []

    required: list[tuple[str, str | None]] = [
        ("TelemetryMetaData", None),
        ("TelemetryMetaData/ParameterTypeSet", "TelemetryMetaData"),
        ("TelemetryMetaData/ParameterSet", "TelemetryMetaData"),
        ("TelemetryMetaData/ContainerSet", "TelemetryMetaData"),
        ("CommandMetaData", None),
        ("CommandMetaData/MetaCommandSet", "CommandMetaData"),
    ]

    for path_str, parent_str in required:
        parts = path_str.split("/")
        node: ET.Element | None = root
        for part in parts:
            if node is None:
                break
            node = node.find(_xtce(part))

        short = parts[-1]
        if node is not None:
            results.append(ValidationResult(short, True, f"<{short}> section present."))
        else:
            results.append(
                ValidationResult(short, False, f"<{short}> section is missing.")
            )

    return results


def check_duplicate_parameters(root: ET.Element) -> tuple[bool, str]:
    """Detect duplicate parameter names in ParameterSet."""
    names: list[str] = [
        el.get("name", "")
        for el in root.iter(_xtce("Parameter"))
    ]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for n in names:
        if n in seen:
            duplicates.add(n)
        seen.add(n)

    if not duplicates:
        return True, f"No duplicate parameter names found ({len(names)} parameters)."
    return False, f"Duplicate parameter names: {sorted(duplicates)}"


def check_duplicate_commands(root: ET.Element) -> tuple[bool, str]:
    """Detect duplicate MetaCommand names."""
    names: list[str] = [
        el.get("name", "")
        for el in root.iter(_xtce("MetaCommand"))
    ]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for n in names:
        if n in seen:
            duplicates.add(n)
        seen.add(n)

    if not duplicates:
        return True, f"No duplicate command names found ({len(names)} commands)."
    return False, f"Duplicate MetaCommand names: {sorted(duplicates)}"


def check_parameter_refs(root: ET.Element) -> tuple[bool, str]:
    """
    Verify every parameterRef inside a SequenceContainer/EntryList
    points to a defined Parameter.
    """
    defined: set[str] = {
        el.get("name", "")
        for el in root.iter(_xtce("Parameter"))
    }
    broken: list[str] = []

    for ref_el in root.iter(_xtce("ParameterRefEntry")):
        ref = ref_el.get("parameterRef", "")
        if ref and ref not in defined:
            broken.append(ref)

    if not broken:
        return True, "All parameterRef entries resolve to defined parameters."
    return False, f"Unresolved parameterRef(s): {broken}"


def check_type_refs(root: ET.Element) -> tuple[bool, str]:
    """
    Verify every parameterTypeRef on a Parameter points to a defined type.
    """
    defined_types: set[str] = set()
    type_tags = [
        "IntegerParameterType",
        "FloatParameterType",
        "BooleanParameterType",
        "StringParameterType",
        "EnumeratedParameterType",
        "BinaryParameterType",
    ]
    for tag in type_tags:
        for el in root.iter(_xtce(tag)):
            name = el.get("name")
            if name:
                defined_types.add(name)

    broken: list[str] = []
    for param in root.iter(_xtce("Parameter")):
        ref = param.get("parameterTypeRef", "")
        if ref and ref not in defined_types:
            broken.append(f"{param.get('name')} → {ref}")

    if not broken:
        return True, f"All parameterTypeRef entries resolve ({len(defined_types)} types defined)."
    return False, f"Unresolved parameterTypeRef(s): {broken}"


def check_argument_type_refs(root: ET.Element) -> tuple[bool, str]:
    """Verify every argumentTypeRef on an Argument points to a defined type."""
    defined_types: set[str] = set()
    arg_type_tags = [
        "IntegerArgumentType",
        "FloatArgumentType",
        "EnumeratedArgumentType",
        "StringArgumentType",
        "BooleanArgumentType",
    ]
    for tag in arg_type_tags:
        for el in root.iter(_xtce(tag)):
            name = el.get("name")
            if name:
                defined_types.add(name)

    broken: list[str] = []
    for arg in root.iter(_xtce("Argument")):
        ref = arg.get("argumentTypeRef", "")
        if ref and ref not in defined_types:
            broken.append(f"{arg.get('name')} → {ref}")

    if not broken:
        return True, f"All argumentTypeRef entries resolve ({len(defined_types)} arg types defined)."
    return False, f"Unresolved argumentTypeRef(s): {broken}"


def check_counts(root: ET.Element) -> tuple[bool, str]:
    """Report counts of key elements as an informational check."""
    params = list(root.iter(_xtce("Parameter")))
    cmds = list(root.iter(_xtce("MetaCommand")))
    containers = list(root.iter(_xtce("SequenceContainer")))
    msg = (
        f"{len(params)} parameters, {len(cmds)} commands, "
        f"{len(containers)} sequence containers."
    )
    return True, msg


# ---------------------------------------------------------------------------
# Main validation orchestrator
# ---------------------------------------------------------------------------

def validate(path: Path) -> ValidationReport:
    """
    Run all validation checks against the given XTCE file.

    Returns a ValidationReport with pass/fail details for each check.
    """
    report = ValidationReport(path=path)

    # 1. Well-formed XML
    ok, msg, root = check_well_formed(path)
    report.add("Well-formed XML", ok, msg)
    if not ok or root is None:
        return report

    # 2. Root element
    ok, msg = check_root_element(root)
    report.add("Root element", ok, msg)

    # 3. Required sections
    for result in check_required_sections(root):
        report.results.append(result)

    # 4. Duplicate parameters
    ok, msg = check_duplicate_parameters(root)
    report.add("No duplicate parameters", ok, msg)

    # 5. Duplicate commands
    ok, msg = check_duplicate_commands(root)
    report.add("No duplicate commands", ok, msg)

    # 6. Parameter refs in containers
    ok, msg = check_parameter_refs(root)
    report.add("Container parameterRefs", ok, msg)

    # 7. Parameter type refs
    ok, msg = check_type_refs(root)
    report.add("Parameter type refs", ok, msg)

    # 8. Argument type refs
    ok, msg = check_argument_type_refs(root)
    report.add("Argument type refs", ok, msg)

    # 9. Counts (always passes, informational)
    ok, msg = check_counts(root)
    report.add("Element counts", ok, msg)

    return report


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
    """Entry point for the XTCE validator CLI."""
    parser = argparse.ArgumentParser(
        description="Validate an XTCE XML file for Yamcs compatibility."
    )
    parser.add_argument("--input", required=True, help="Path to XTCE XML file")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    path = Path(args.input)
    if not path.exists():
        logger.error("File not found: %s", path)
        return 2

    logger.info("Validating %s ...", path)
    report = validate(path)
    report.print_summary()

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())