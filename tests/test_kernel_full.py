import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.kernel import DeterministicKernel

print("=== STARTING KERNEL FULL CYCLE TEST ===")
print("[1] Inicializando DeterministicKernel (cargará ModelManager y ToolGuard si están disponibles)")  # pyre-ignore[arg-type]
k = DeterministicKernel()

print("\n[2] Ejecutando tarea con k.run() - Prueba de tools write_file y read_file")  # pyre-ignore[arg-type]
task = "Crea un archivo llamado test_kernel_cycle.txt con el texto 'EIDOS KERNEL OK', luego utiliza una herramienta para leerlo y devuélveme el contenido exacto."
res = k.run(task)

print("\n=== FINAL RESULT ===")
print(res)
