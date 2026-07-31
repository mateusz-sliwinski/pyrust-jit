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


import advanced_data
# 1. Instantiate the Rust struct
processor = advanced_data.DataProcessor(10)

# 2. Vector operations (Vec<i32> -> Vec<i32>)
my_list = [1, 2, 3, 4]
scaled = processor.scale_values(my_list)
print(f"Scaled values: {scaled}")
# Output: [10, 20, 30, 40]

# 3. Hash map operations (Vec<String> -> HashMap<String, i32>)
words = ["apple", "banana", "apple", "pear", "banana", "apple"]
counts = processor.count_words(words)
print(f"Word counts: {counts}")
# Output: {'apple': 3, 'banana': 2, 'pear': 1}

# 4. Handling Option types (Option<String> -> Optional[str])
print(f"Status (True): {processor.get_status(True)}")
# Output: 'Operation completed successfully!'
print(f"Status (False): {processor.get_status(False)}")
# Output: None

# 5. Merging dictionaries natively in Rust
team1 = {"Alice": 10, "Bob": 5}
team2 = {"Charlie": 15, "Bob": 5}
merged = advanced_data.merge_scores(team1, team2)
print(f"Merged scores: {merged}")
# Output: {'Alice': 10, 'Bob': 10, 'Charlie': 15}

import numpy as np
import fast_numpy

print("--- 1. Euclidean Distance Test ---")
n_points = 1_000_000
n_features = 5
print(f"Generating matrix: {n_points} points, {n_features} dimensions...")

# 1 million points, each with 5 coordinates
points = np.random.rand(n_points, n_features)
query_point = np.random.rand(n_features)

start = time.time()
distances = fast_numpy.euclidean_distances(points, query_point)
end = time.time()

print(f"Rust calculated {n_points} distances in: {end - start:.4f} seconds")
print(f"Sample results: {distances[:5]}\n")

print("--- 2. Min-Max Scaler Test ---")
rows, cols = 5000, 100
print(f"Generating data for scaling ({rows} x {cols})...")

# Data with mean 500 and deviation
raw_data = np.random.randn(rows, cols) * 100 + 500

start = time.time()
scaled_data = fast_numpy.min_max_scale(raw_data)
end = time.time()

print(f"Rust normalized the matrix in: {end - start:.4f} seconds")
print("Original (min/max of the first column):", np.min(raw_data[:, 0]), "/", np.max(raw_data[:, 0]))
print("After scaling (min/max of the first col.):", np.min(scaled_data[:, 0]), "/", np.max(scaled_data[:, 0]))

import polars as pl
import fast_polars

print("Generating Polars DataFrame (1 million rows)...")
df = pl.DataFrame({
    "id": range(1_000_000),
    "value": [x * 1.5 for x in range(1_000_000)]
})

print("\n--- 1. process_dataframe (limit = 3) ---")
print(fast_polars.process_dataframe(df, limit=3))

print("\n--- 2. filter_by_value (value > 1499990.0) ---")
start = time.time()
filtered = fast_polars.filter_by_value(df, column_name="value", threshold=1499990.0)
end = time.time()
print(f"Filtering took {end - start:.5f} seconds")
print(filtered)

print("\n--- 3. sort_by_column (descending = True) ---")
start = time.time()
sorted_df = fast_polars.sort_by_column(df, column_name="value", descending=True)
end = time.time()
print(f"Sorting took {end - start:.5f} seconds")
print(sorted_df.head(3))

print("\n--- 4. sum_column ---")
start = time.time()
total_sum = fast_polars.sum_column(df, column_name="value")
end = time.time()
print(f"Sum calculated in {end - start:.5f} seconds: {total_sum}")

print("\n--- Error Handling Test ---")
try:
    # Trying to sum a column that does not exist
    fast_polars.sum_column(df, column_name="non_existent_column")
except ValueError as e:
    print(f"Caught Python exception: {e}")

logger.info("\nFinished successfully. Check your folder for newly generated .pyi files!")
