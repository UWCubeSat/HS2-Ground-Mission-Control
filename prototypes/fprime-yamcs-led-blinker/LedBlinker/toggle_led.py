#this script takes commands ON and OFF
import sys
from yamcs.client import YamcsClient

state = sys.argv[1].upper() if len(sys.argv) > 1 else "ON"
client = YamcsClient("localhost:8090")
processor = client.get_processor(instance="fprime-project", processor="realtime")
processor.issue_command(
     "/LedBlinker_LedBlinkerDeployment/LedBlinker/led/BLINKING_ON_OFF",
     args={"onOff": state},
)
print("Command sent hehehehe")