package com.example.myproject;

import java.nio.ByteBuffer;
import java.util.HashMap;
import java.util.Map;

import org.yamcs.TmPacket;
import org.yamcs.YConfiguration;
import org.yamcs.tctm.AbstractPacketPreprocessor;
import org.yamcs.utils.TimeEncoding;

/**
 * Checks F Prime space packets and stamps them with the time tag set on board.
 * <p>
 * Field widths match the F Prime defaults: U16 packet descriptor, U32 channel and event IDs, U16 telemetry packet ID,
 * and a time tag of U16 base, U8 context, U32 seconds and U32 microseconds.
 */
public class FprimePacketPreprocessor extends AbstractPacketPreprocessor {

    private static final int PRIMARY_HEADER_SIZE = 6;
    private static final int DESCRIPTOR_SIZE = 2;
    private static final int TIME_TAG_SIZE = 11;

    // Default F Prime APIDs (ComCfg.Apid)
    private static final int APID_TELEMETRY_CHANNEL = 1;
    private static final int APID_EVENT = 2;
    private static final int APID_PACKETIZED_TELEMETRY = 4;
    private static final int APID_FPRIME_IDLE = 6;
    private static final int APID_CCSDS_IDLE = 0x7FF;

    private static final int TB_WORKSTATION_TIME = 2; // Unix time, as set by Svc.ChronoTime

    private final Map<Integer, Integer> lastSequenceCounts = new HashMap<>();

    public FprimePacketPreprocessor(String yamcsInstance) {
        this(yamcsInstance, YConfiguration.emptyConfig());
    }

    public FprimePacketPreprocessor(String yamcsInstance, YConfiguration config) {
        super(yamcsInstance, config);
    }

    @Override
    public TmPacket process(TmPacket packet) {
        byte[] bytes = packet.getPacket();
        if (bytes.length <= PRIMARY_HEADER_SIZE) {
            eventProducer.sendWarning("SHORT_PACKET", "Dropped a packet of " + bytes.length + " bytes");
            return null;
        }

        ByteBuffer buffer = ByteBuffer.wrap(bytes);
        int apidAndSequence = buffer.getInt(0);
        int apid = (apidAndSequence >> 16) & 0x7FF;
        if (apid == APID_CCSDS_IDLE || apid == APID_FPRIME_IDLE) {
            return null;
        }

        int declaredSize = Short.toUnsignedInt(buffer.getShort(4)) + 1 + PRIMARY_HEADER_SIZE;
        if (declaredSize != bytes.length) {
            eventProducer.sendWarning("PACKET_LENGTH", "Dropped a packet for APID " + apid + ": header says "
                    + declaredSize + " bytes, received " + bytes.length);
            return null;
        }

        checkSequence(apid, apidAndSequence & 0x3FFF);
        setTime(packet, buffer, apid);

        // APID and count together identify the packet, along with its generation time
        packet.setSequenceCount(apidAndSequence);
        return packet;
    }

    private void checkSequence(int apid, int sequenceCount) {
        Integer last = lastSequenceCounts.put(apid, sequenceCount);
        if (last != null && ((sequenceCount - last) & 0x3FFF) != 1) {
            eventProducer.sendWarning("SEQ_COUNT_JUMP",
                    "Sequence count jump for APID " + apid + ": " + last + " to " + sequenceCount);
        }
    }

    private void setTime(TmPacket packet, ByteBuffer buffer, int apid) {
        int offset = timeTagOffset(apid);
        if (offset > 0 && buffer.capacity() >= offset + TIME_TAG_SIZE
                && Short.toUnsignedInt(buffer.getShort(offset)) == TB_WORKSTATION_TIME) {
            long seconds = Integer.toUnsignedLong(buffer.getInt(offset + 3));
            long microseconds = Integer.toUnsignedLong(buffer.getInt(offset + 7));
            packet.setGenerationTime(TimeEncoding.fromUnixMillisec(seconds * 1000 + microseconds / 1000));
        } else {
            // No time tag, or one whose epoch is unknown
            packet.setGenerationTime(TimeEncoding.getWallclockTime());
            packet.setLocalGenTimeFlag();
        }
    }

    /** Returns where the time tag starts, or -1 if this APID does not carry one. */
    private static int timeTagOffset(int apid) {
        switch (apid) {
        case APID_TELEMETRY_CHANNEL:
        case APID_EVENT:
            return PRIMARY_HEADER_SIZE + DESCRIPTOR_SIZE + 4;
        case APID_PACKETIZED_TELEMETRY:
            return PRIMARY_HEADER_SIZE + DESCRIPTOR_SIZE + 2;
        default:
            return -1;
        }
    }
}
