use pyo3::prelude::*;
use numpy::{PyReadonlyArray1, PyReadonlyArray2, IntoPyArray, PyArray1, PyArray2};
use ndarray::{Array1, Array2, Axis};

// 1. Calculating Euclidean distance (point vs matrix of points)
// Takes: 2D matrix (points), 1D vector (query)
// Returns: 1D vector (distances)
#[pyfunction]
fn euclidean_distances<'py>(
    py: Python<'py>,
    points: PyReadonlyArray2<'py, f64>,
    query: PyReadonlyArray1<'py, f64>,
) -> Bound<'py, PyArray1<f64>> {
    let points_view = points.as_array();
    let query_view = query.as_array();

    // Prepare an empty 1D vector for the results (filled with zeros)
    let mut distances = Array1::<f64>::zeros(points_view.nrows());

    // Iterate over the rows (Axis(0) represents rows in ndarray)
    for (i, row) in points_view.axis_iter(Axis(0)).enumerate() {
        // Zip the row with the query vector, calculate the squared difference and the square root
        let dist = row.iter()
            .zip(query_view.iter())
            .map(|(a, b)| (a - b).powi(2))
            .sum::<f64>()
            .sqrt();

        distances[i] = dist;
    }

    distances.into_pyarray(py)
}

// 2. Min-Max normalization by columns (scaling to the 0-1 range)
// Takes: 2D matrix
// Returns: a new 2D matrix after transformation
#[pyfunction]
fn min_max_scale<'py>(
    py: Python<'py>,
    data: PyReadonlyArray2<'py, f64>,
) -> Bound<'py, PyArray2<f64>> {
    let data_view = data.as_array();

    // Create a new matrix with exactly the same dimensions
    let mut result = Array2::<f64>::zeros(data_view.raw_dim());

    // Iterate over the columns (Axis(1))
    for (j, col) in data_view.axis_iter(Axis(1)).enumerate() {
        // Find the minimum and maximum in the column
        let min_val = col.iter().copied().fold(f64::INFINITY, f64::min);
        let max_val = col.iter().copied().fold(f64::NEG_INFINITY, f64::max);

        // Safeguard against division by zero (if the column has constant values)
        let range = if max_val - min_val == 0.0 { 1.0 } else { max_val - min_val };

        // Update the cells in the new matrix for the given column
        for i in 0..data_view.nrows() {
            result[[i, j]] = (data_view[[i, j]] - min_val) / range;
        }
    }

    result.into_pyarray(py)
}

#[pymodule]
fn fast_numpy(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(euclidean_distances, m)?)?;
    m.add_function(wrap_pyfunction!(min_max_scale, m)?)?;
    Ok(())
}