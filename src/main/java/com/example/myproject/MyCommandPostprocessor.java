package com.example.myproject;

import org.yamcs.YConfiguration;
import org.yamcs.cmdhistory.CommandHistoryPublisher;
import org.yamcs.commanding.PreparedCommand;
import org.yamcs.tctm.CcsdsSeqCountFiller;
import org.yamcs.tctm.CommandPostprocessor;
import org.yamcs.utils.ByteArrayUtils;

/**
 * Component capable of modifying command binary before passing it to the link for further dispatch.
 * <p>
 * A single instance of this class is created, scoped to the link udp-out.
 * <p>
 * This is specified in the configuration file yamcs.myproject.yaml:
 * 
 * <pre>
 * ...
 * dataLinks:
 *   - name: udp-out
 *     class: org.yamcs.tctm.UdpTcDataLink
 *     stream: tc_realtime
 *     host: localhost
 *     port: 10025
 *     commandPostprocessorClassName: com.example.myproject.MyCommandPostprocessor
 * ...
 * </pre>
 */
public class MyCommandPostprocessor implements CommandPostprocessor {

    private CcsdsSeqCountFiller seqFiller = new CcsdsSeqCountFiller();
    private CommandHistoryPublisher commandHistory;

    // Constructor used when this postprocessor is used without YAML configuration
    public MyCommandPostprocessor(String yamcsInstance) {
        this(yamcsInstance, YConfiguration.emptyConfig());
    }

    // Constructor used when this postprocessor is used with YAML configuration
    // (commandPostprocessorClassArgs)
    public MyCommandPostprocessor(String yamcsInstance, YConfiguration config) {
    }

    // Called by Yamcs during initialization
    @Override
    public void setCommandHistoryPublisher(CommandHistoryPublisher commandHistory) {
        this.commandHistory = commandHistory;
    }

    // Called by Yamcs *after* a command was submitted, but *before* the link handles it.
    // This method must return the (possibly modified) packet binary.
    @Override
    public byte[] process(PreparedCommand pc) {
        byte[] originalBinary = pc.getBinary();
        byte[] binary;

        // CCSDS Primary Header requires at least 6 bytes. 
        // If YAMCS gave us less, allocate a new array that can fit the 6-byte header + payload
        if (originalBinary.length < 6) {
            binary = new byte[6 + originalBinary.length];
            // Shift the original payload bytes down past the new 6-byte header zone
            System.arraycopy(originalBinary, 0, binary, 6, originalBinary.length);
        } else {
            binary = originalBinary;
        }

        // Now index 4 is GUARANTEED to exist safely.
        // Set CCSDS packet length field (Bytes 4-5)
        // Formula: Total packet length - 7
        ByteArrayUtils.encodeUnsignedShort(binary.length - 7, binary, 4);

        // Set CCSDS sequence count fields safely
        int seqCount = seqFiller.fill(binary);

        // Publish updates to YAMCS Command History tracking engine
        commandHistory.publish(pc.getCommandId(), "ccsds-seqcount", seqCount);
        commandHistory.publish(pc.getCommandId(), PreparedCommand.CNAME_BINARY, binary);

        return binary;
    }
}
