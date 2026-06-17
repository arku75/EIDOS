from core.kernel import DeterministicKernel
import logging
logging.basicConfig(level=logging.ERROR)
k = DeterministicKernel()
print("Run result:", k.run("Dime la hora y luego averigua quien soy con whoami"))
