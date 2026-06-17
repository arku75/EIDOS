import sys
sys.path.insert(0, "/home/ser/EIDOS")
from eidos_cli import FAST_MODEL
from core.agent import EIDOSAgent

print("Model:", FAST_MODEL)
agent = EIDOSAgent(agent_id="test", verbose=True)
res = agent.run("Ping a localhost", use_tools=True, verify=False)
print("Result:\n", res)
