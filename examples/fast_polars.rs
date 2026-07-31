use pyo3::prelude::*;
use pyo3::exceptions::{PyValueError, PyRuntimeError};
use pyo3_polars::PyDataFrame;
use polars::prelude::*;

// 1. Return the first N rows
#[pyfunction]
fn process_dataframe(pydf: PyDataFrame, limit: usize) -> PyResult<PyDataFrame> {
    // Extract native Polars DataFrame from Python wrapper (O(1) operation)
    let df: DataFrame = pydf.into();

    // Take the top N rows
    let processed_df = df.head(Some(limit));

    // Wrap back into PyDataFrame
    Ok(PyDataFrame(processed_df))
}

// 2. Filter rows based on a float column and a threshold
#[pyfunction]
fn filter_by_value(pydf: PyDataFrame, column_name: &str, threshold: f64) -> PyResult<PyDataFrame> {
    let df: DataFrame = pydf.into();

    // Get the column. Map Polars error to Python ValueError if column is missing
    let s = df.column(column_name)
        .map_err(|e| PyErr::new::<PyValueError, _>(e.to_string()))?;

    // Create a boolean mask where value is greater than the threshold
    let mask = s.f64()
        .map_err(|e| PyErr::new::<PyValueError, _>(e.to_string()))?
        .gt(threshold);

    // Apply the mask to filter the DataFrame
    let filtered_df = df.filter(&mask)
        .map_err(|e| PyErr::new::<PyRuntimeError, _>(e.to_string()))?;

    Ok(PyDataFrame(filtered_df))
}

// 3. Sort the DataFrame by a specific column
#[pyfunction]
fn sort_by_column(pydf: PyDataFrame, column_name: &str, descending: bool) -> PyResult<PyDataFrame> {
    let df: DataFrame = pydf.into();

    // Configure sort options
    let sort_options = SortMultipleOptions::default()
        .with_order_descending(descending);

    // Execute the sorting operation
    let sorted_df = df.sort([column_name], sort_options)
        .map_err(|e| PyErr::new::<PyRuntimeError, _>(e.to_string()))?;

    Ok(PyDataFrame(sorted_df))
}

// 4. Calculate the sum of a specific float column and return a scalar value
#[pyfunction]
fn sum_column(pydf: PyDataFrame, column_name: &str) -> PyResult<f64> {
    let df: DataFrame = pydf.into();

    let s = df.column(column_name)
        .map_err(|e| PyErr::new::<PyValueError, _>(e.to_string()))?;

    let sum = s.f64()
        .map_err(|e| PyErr::new::<PyValueError, _>(e.to_string()))?
        .sum()
        .unwrap_or(0.0);

    Ok(sum)
}

#[pymodule]
fn fast_polars(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(process_dataframe, m)?)?;
    m.add_function(wrap_pyfunction!(filter_by_value, m)?)?;
    m.add_function(wrap_pyfunction!(sort_by_column, m)?)?;
    m.add_function(wrap_pyfunction!(sum_column, m)?)?;
    Ok(())
}