# fprime_to_xtce — F Prime → XTCE Converter

Converts F Prime deployment dictionary JSON files into XTCE XML
compatible with Yamcs mission database loading.

---

## Files

| File | Description |
|------|-------------|
| `fprime_to_xtce.py` | Main converter script |
| `sample_dictionary.json` | Sample F Prime JSON input |
| `example_mission.xml` | Generated XTCE output from the sample |
| `test_fprime_to_xtce.py` | pytest test suite (33 tests) |
| `requirements.txt` | Python dependencies |

---

## Requirements

- Python 3.11+
- pip packages: `pytest`, `lxml` (lxml is optional; stdlib xml is used by default)

---

## Installation

```bash
pip install -r requirements.txt
```

---

## Usage

```bash
# Basic conversion
python fprime_to_xtce.py --input dictionary.json --output mission.xml

# With validation and verbose logging
python fprime_to_xtce.py --input dictionary.json --output mission.xml --validate --verbose
```

### CLI Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--input` | Yes | Path to F Prime dictionary JSON |
| `--output` | Yes | Path for generated XTCE XML |
| `--validate` | No | Validate the generated XML after writing |
| `--verbose` | No | Enable DEBUG-level logging |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Validation failed |
| 2 | Input file not found |
| 3 | Invalid JSON or input error |
| 4 | XML write failure |
| 99 | Unexpected error |

---

## Running Tests

```bash
python -m pytest test_fprime_to_xtce.py -v
```

Expected: **33 passed**

---

## What Gets Converted

### From F Prime JSON

| F Prime field | Parsed as |
|---------------|-----------|
| `commands[]` | `MetaCommand` with `ArgumentList` and opcode `AncillaryData` |
| `telemetryChannels[]` | `Parameter` + `SequenceContainer` per channel |
| `events[]` | Event parameters added to `ParameterSet` |
| `formalParams[].type` with `enumeration` | `EnumeratedParameterType` / `EnumeratedArgumentType` |
| `integer` types | `IntegerParameterType` / `IntegerArgumentType` (signed/unsigned, sized) |
| `float` types | `FloatParameterType` / `FloatArgumentType` (IEEE754) |
| `bool` types | Mapped to 8-bit unsigned integer |
| `string` types | `StringParameterType` / `StringArgumentType` (UTF-8) |
| `units` | Preserved in type metadata |
| `annotation` | Written as `LongDescription` |

### XTCE Structure Generated

```
SpaceSystem
├── Header
├── TelemetryMetaData
│   ├── ParameterTypeSet      ← all unique parameter types (incl. enums)
│   ├── ParameterSet          ← one Parameter per telemetry channel + event params
│   └── ContainerSet          ← one SequenceContainer per telemetry channel
└── CommandMetaData
    ├── ArgumentTypeSet       ← all unique argument types (incl. enums)
    └── MetaCommandSet        ← one MetaCommand per command
        ├── ArgumentList      ← formal parameters
        ├── CommandContainer  ← argument refs
        └── AncillaryDataSet  ← opcode (dec + hex), commandKind
```

---

## Loading into Yamcs

### 1. Deploy the XTCE file

Copy `mission.xml` into your Yamcs instance's `mdb/` directory:

```
yamcs/
└── etc/
    └── yamcs.<instance>.yaml
mdb/
└── mission.xml
```

### 2. Configure `yamcs.<instance>.yaml`

```yaml
mdb:
  - type: xtce
    spec: mdb/mission.xml
```

### 3. Start Yamcs

```bash
./mvnw compile
./mvnw yamcs:run
```

### 4. Verify in Yamcs Web

- Open `http://localhost:8090`
- Navigate to **MDB → Parameters** to confirm telemetry channels loaded
- Navigate to **MDB → Commands** to confirm MetaCommands appear
- Issue a test command via the Commander panel

### Notes on Yamcs Compatibility

- **Opcodes** are stored in `AncillaryData` elements. To bind them to Yamcs
  command encoding, add a `CommandContainer` with a fixed `FixedValueEntry` for
  the opcode field once you have defined your link-layer framing.
- **String parameters** use unbounded UTF-8 encoding; set a `maxSizeInBits`
  in `StringDataEncoding` if your frame format requires fixed-length strings.
- **Boolean** values are encoded as 8-bit unsigned integers (0 = false, 1 = true).
  Yamcs will display them as integers unless you add an `EnumerationList` with
  `FALSE/TRUE` labels.
- Yamcs requires the XTCE namespace `http://www.omg.org/space/xtce`; this tool
  always emits the correct namespace.
- Tested compatible with Yamcs 5.x XTCE loader.

---

## Unsupported / Gracefully Skipped

| F Prime feature | Handling |
|-----------------|----------|
| `array` types | Mapped to `string`, warning logged |
| `struct` types | Mapped to `string`, warning logged |
| Unknown type kinds | Mapped to `string`, warning logged |
| Duplicate command/parameter names | Second occurrence skipped, warning logged |
| Missing required fields | Field skipped, warning logged |