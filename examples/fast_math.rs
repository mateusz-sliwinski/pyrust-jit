// pyrust-dep: rayon = "1.10"

use pyo3::prelude::*;
use rayon::prelude::*;

#[pyfunction]
fn parallel_sum_of_squares(n: u64) -> u128 {
    (1..=n as u128).into_par_iter().map(|x| x * x).sum()
}

#[pymodule]
fn fast_math(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parallel_sum_of_squares, m)?)?;
    Ok(())
}