import time
from loguru import logger
logger.info("--- Initializing Pyrust ---")
# Import our framework. Ensure pyrust.py is available in your PYTHONPATH or local directory.
import pyrust

# Enable the import hook.
# release=False ensures rapid compilation for development.
pyrust.enable(debug=True, release=False)

logger.info("\n--- Test 1: Multithreaded Math (Rayon) ---")
import fast_math
start = time.time()
n = 10_000_000
result = fast_math.parallel_sum_of_squares(n)
end = time.time()
logger.info(f"Sum of squares for {n}: {result}")
logger.info(f"Execution time: {end - start:.4f}s")

logger.info("\n--- Test 2: String Operations ---")
import string_utils
text = "Pyrust works brilliantly"
result_text = string_utils.reverse_and_uppercase(text)
logger.info(f"Original: {text}")
logger.info(f"Reversed and Uppercased: {result_text}")

logger.info("\n--- Test 3: Multi-file Project (Crate Directory) ---")
import my_crate
a, b = 5, 4
result_crate = my_crate.calculate(a, b)
logger.info(f"Calculation ( {a} * {b} + 10 ) from submodule: {result_crate}")

logger.info("\nFinished successfully. Check your folder for newly generated .pyi files!")