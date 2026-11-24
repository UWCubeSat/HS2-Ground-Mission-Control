package com.example.myproject;

import org.yamcs.YConfiguration;
import org.yamcs.cmdhistory.CommandHistoryPublisher;
import org.yamcs.commanding.PreparedCommand;
import org.yamcs.tctm.CommandPostprocessor;
import org.yamcs.utils.ByteArrayUtils;
import org.yamcs.utils.TimeEncoding;

/**
 * Completes the CCSDS primary header of F Prime commands before a link sends them.
 * <p>
 * The generated XTCE leaves the packet length and sequence count as zero. This fills both in.
 */
public class FprimeCommandPostprocessor implements CommandPostprocessor {

    private static final int PRIMARY_HEADER_SIZE = 6;
    private static final int DESCRIPTOR_SIZE = 2; // FwPacketDescriptorType
    private static final int OPCODE_SIZE = 4; // FwOpcodeType
    private static final int MIN_COMMAND_SIZE = PRIMARY_HEADER_SIZE + DESCRIPTOR_SIZE + OPCODE_SIZE;
    private static final int COMMAND_PACKET_ID = 0x1000; // version 0, type TC, no secondary header, APID 0
    private static final int SEQUENCE_COUNT_MASK = 0x3FFF;

    private CommandHistoryPublisher commandHistory;

    // F Prime's ApidManager expects the first count to be 0. After a Yamcs restart the count starts
    // over, which F Prime reports once as UnexpectedSequenceCount and then follows.
    private int nextSequenceCount = 0;

    public FprimeCommandPostprocessor(String yamcsInstance) {
        this(yamcsInstance, YConfiguration.emptyConfig());
    }

    public FprimeCommandPostprocessor(String yamcsInstance, YConfiguration config) {
    }

    @Override
    public void setCommandHistoryPublisher(CommandHistoryPublisher commandHistory) {
        this.commandHistory = commandHistory;
    }

    @Override
    public synchronized byte[] process(PreparedCommand pc) {
        byte[] binary = pc.getBinary();

        // Commands from the other space systems share the TC stream, but F Prime would reject them.
        if (binary.length < MIN_COMMAND_SIZE
                || ByteArrayUtils.decodeUnsignedShort(binary, 0) != COMMAND_PACKET_ID) {
            commandHistory.commandFailed(pc.getCommandId(), TimeEncoding.getWallclockTime(),
                    "Not an F Prime command packet");
            return null;
        }

        int sequenceCount = nextSequenceCount;
        nextSequenceCount = (nextSequenceCount + 1) & SEQUENCE_COUNT_MASK;

        int groupFlags = ByteArrayUtils.decodeUnsignedShort(binary, 2) & ~SEQUENCE_COUNT_MASK;
        ByteArrayUtils.encodeUnsignedShort(groupFlags | sequenceCount, binary, 2);

        // CCSDS defines the length field as the number of data bytes minus one
        ByteArrayUtils.encodeUnsignedShort(binary.length - PRIMARY_HEADER_SIZE - 1, binary, 4);

        commandHistory.publish(pc.getCommandId(), "ccsds-seqcount", sequenceCount);
        commandHistory.publish(pc.getCommandId(), PreparedCommand.CNAME_BINARY, binary);
        return binary;
    }
}
