use pyo3::prelude::*;

mod utils;

#[pyfunction]
fn calculate(a: i32, b: i32) -> i32 {
    utils::multiply(a, b) + 10
}

#[pymodule]
fn my_crate(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(calculate, m)?)?;
    Ok(())
}