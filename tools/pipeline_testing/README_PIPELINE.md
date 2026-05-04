# HS2 Ingest Pipeline Testing

End-to-end validation and simulation tools for the F Prime → XTCE → Yamcs pipeline.

---

## Files

| File | Purpose |
|------|---------|
| `validate_xtce.py` | Validates an XTCE XML file (well-formedness, sections, refs, duplicates) |
| `simulator.py` | Sends binary CCSDS-framed telemetry packets to Yamcs over UDP |
| `hs2_mission.xml` | Minimal but realistic XTCE file matching the simulator packet layout |
| `test_pipeline.py` | pytest suite (39 tests covering validator + simulator) |
| `requirements_pipeline.txt` | Python dependencies |

---

## Install

```bash
pip install -r requirements_pipeline.txt
```

No external dependencies beyond pytest — everything uses Python stdlib.

---

## Part 1: Validate an XTCE file

```bash
python validate_xtce.py --input hs2_mission.xml
python validate_xtce.py --input hs2_mission.xml --verbose
```

Example output:
```
============================================================
  XTCE Validation Report: hs2_mission.xml
============================================================
  [PASS] ✓ Well-formed XML
  [PASS] ✓ Root element
  [PASS] ✓ TelemetryMetaData
  [PASS] ✓ ParameterTypeSet
  [PASS] ✓ ParameterSet
  [PASS] ✓ ContainerSet
  [PASS] ✓ CommandMetaData
  [PASS] ✓ MetaCommandSet
  [PASS] ✓ No duplicate parameters
  [PASS] ✓ No duplicate commands
  [PASS] ✓ Container parameterRefs
  [PASS] ✓ Parameter type refs
  [PASS] ✓ Argument type refs
  [PASS] ✓ Element counts         7 parameters, 2 commands, 1 sequence containers.
============================================================
  Overall: ALL CHECKS PASSED
============================================================
```

Exit codes: `0` = all passed, `1` = checks failed, `2` = file not found.

---

## Part 2: Run the telemetry simulator

```bash
# Send 1 packet/sec forever to Yamcs on localhost:10015
python simulator.py --host 127.0.0.1 --port 10015 --rate 1

# Send 50 packets at 2/sec with debug logging
python simulator.py --host 127.0.0.1 --port 10015 --rate 2 --count 50 --verbose

# Stop with Ctrl+C
```

### Packet layout (22 bytes, big-endian)

| Offset | Bytes | Type    | Field       | Notes |
|--------|-------|---------|-------------|-------|
| 0      | 2     | uint16  | ccsds_word0 | version\|type\|sec_hdr\|apid |
| 2      | 2     | uint16  | ccsds_word1 | seq flags \| seq count |
| 4      | 2     | uint16  | ccsds_length| data bytes - 1 |
| 6      | 4     | uint32  | packet_id   | monotonic counter |
| 10     | 4     | float32 | temperature | degrees Celsius (~18–28 °C) |
| 14     | 4     | float32 | voltage     | volts (~3.25–3.35 V) |
| 18     | 4     | uint32  | status      | 0=IDLE, 1=ACTIVE, 2=ERROR |

---

## Part 3: Run the tests

```bash
python -m pytest test_pipeline.py -v
```

Expected: **39 passed**

Test classes:
- `TestWellFormedXML` — malformed, empty, non-XML inputs
- `TestRootElement` — correct and wrong root tags
- `TestRequiredSections` — missing TelemetryMetaData, ParameterSet, ContainerSet
- `TestDuplicateParameters` — duplicate parameter and command names
- `TestParameterRefs` — broken parameterRef and parameterTypeRef links
- `TestFullValidation` — end-to-end validate() against real hs2_mission.xml
- `TestPacketEncoding` — size, roundtrip, big-endian, edge values
- `TestTelemetryGenerators` — value ranges for temperature/voltage/status
- `TestUDPTransmission` — live loopback UDP send/receive with packet inspection

---

## Part 4: Loading hs2_mission.xml into Yamcs

### 1. Place the XTCE file

```
yamcs/
└── mdb/
    └── hs2_mission.xml
```

### 2. Configure yamcs.instance.yaml

```yaml
mdb:
  - type: xtce
    spec: mdb/hs2_mission.xml

dataLinks:
  - name: udp-in
    class: org.yamcs.tctm.UdpTmDataLink
    stream: tm_realtime
    host: 0.0.0.0
    port: 10015
```

### 3. Configure the packet processor

In your processor config, tell Yamcs to decode incoming UDP frames as `HS2HousekeepingPacket`:

```yaml
streamConfig:
  tm:
    - name: tm_realtime
      rootContainer: /HS2/HS2HousekeepingPacket
```

### 4. Start Yamcs, then start the simulator

```bash
# Terminal 1 — Yamcs
yamcsadmin run

# Terminal 2 — simulator
python simulator.py --host 127.0.0.1 --port 10015 --rate 1 --verbose
```

### 5. Verify in Yamcs Web UI (http://localhost:8090)

| Where | What to check |
|-------|---------------|
| MDB → Parameters | `temperature`, `voltage`, `status`, `packet_id` all listed |
| MDB → Containers | `HS2HousekeepingPacket` present with 7 entries |
| MDB → Commands | `CMD_NO_OP`, `CMD_SET_LED` present |
| Telemetry → Realtime | All 4 parameters updating at 1 Hz |
| Telemetry → status | Cycling IDLE → ACTIVE → ERROR as expected |
| Archive | Packets accumulating in the archive browser |

---

## Where to put these files in the repo

```
HS2-Ground-Mission-Control/
└── tools/
    ├── fprime_to_xtce/          ← converter (previous deliverable)
    └── pipeline_testing/        ← this deliverable
        ├── validate_xtce.py
        ├── simulator.py
        ├── hs2_mission.xml
        ├── test_pipeline.py
        ├── requirements_pipeline.txt
        └── README.md
```