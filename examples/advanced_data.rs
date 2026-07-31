use pyo3::prelude::*;
use std::collections::HashMap;

#[pyclass]
pub struct DataProcessor {
    multiplier: i32,
}

#[pymethods]
impl DataProcessor {
    // Constructor (maps to __init__ in Python)
    #[new]
    fn new(multiplier: i32) -> Self {
        DataProcessor { multiplier }
    }

    // Process a vector and return a new vector (list[int] -> list[int])
    fn scale_values(&self, values: Vec<i32>) -> Vec<i32> {
        values.into_iter().map(|v| v * self.multiplier).collect()
    }

    // Count word occurrences (list[str] -> dict[str, int])
    fn count_words(&self, words: Vec<String>) -> HashMap<String, i32> {
        let mut counts = HashMap::new();
        for word in words {
            *counts.entry(word).or_insert(0) += 1;
        }
        counts
    }

    // Return an optional value (bool -> Optional[str])
    fn get_status(&self, success: bool) -> Option<String> {
        if success {
            Some("Operation completed successfully!".to_string())
        } else {
            None
        }
    }
}

// A standalone function merging two dictionaries (dict + dict -> dict)
#[pyfunction]
fn merge_scores(scores1: HashMap<String, i32>, scores2: HashMap<String, i32>) -> HashMap<String, i32> {
    let mut result = scores1;
    for (k, v) in scores2 {
        *result.entry(k).or_insert(0) += v;
    }
    result
}

#[pymodule]
fn advanced_data(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<DataProcessor>()?;
    m.add_function(wrap_pyfunction!(merge_scores, m)?)?;
    Ok(())
}