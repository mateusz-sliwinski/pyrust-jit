use pyo3::prelude::*;

#[pyfunction]
fn reverse_and_uppercase(s: String) -> String {
    s.chars().rev().collect::<String>().to_uppercase()
}

#[pymodule]
fn string_utils(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(reverse_and_uppercase, m)?)?;
    Ok(())
}