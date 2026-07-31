# Pyrust: Zero-Boilerplate Rust in Python

Pyrust is a Python import hook that lets you import `.rs` files directly into your Python scripts. It eliminates the need for manual C-extension compilation, `setup.py` scripts, or external build configuration — your Rust code is compiled on the fly, the first time it's imported.

```python
import pyrust
pyrust.enable()

import my_module  # compiles my_module.rs "on the fly"
print(my_module.add(2, 2))
```

## Features

- **Import `.rs` like a regular Python module** — either a single file (`module.rs`) or a whole crate directory (`module/lib.rs` plus the rest of its files).
- **Zero-config NumPy and Polars support** — if the code contains `PyArray`/`PyReadonlyArray` or `PyDataFrame`/`PySeries` types, the matching dependencies (`numpy`, `ndarray`, `pyo3-polars`, `polars`) are added to `Cargo.toml` automatically.
- **Inline dependency declarations** — via `// pyrust-dep: ...` comments directly in the `.rs` file.
- **SHA-256-based caching** — a hash of the source (file or whole directory) determines whether a rebuild is needed; subsequent imports of unchanged code are instant.
- **Automatic `.pyi` stubs** — generated next to the source from `#[pyfunction]`, `#[pyclass]`, and `#[pymethods]`, with Rust → Python type translation (`Vec<T>` → `list[T]`, `Option<T>` → `Optional[T]`, `HashMap<K, V>` → `dict[K, V]`, NumPy/Polars types, etc.), so your IDE can autocomplete your Rust functions.
- **Safe `PyInit_` symbol matching** — the name of the function annotated with `#[pymodule]` is read straight from the source, and the compiled module is loaded under that name (not the filename or the Python import name), preventing "dynamic module does not define module export function" errors.
- **Release/debug mode** — controlled with a single `enable()` argument.

## Prerequisites

You need the Rust toolchain installed (including `cargo`).
Install it from: https://rustup.rs/

## Installation

```bash
pip install pyrust-jit
```

## Quick Start

**`math_ops.rs`**
```rust
use pyo3::prelude::*;

#[pyfunction]
fn add(a: i64, b: i64) -> i64 {
    a + b
}

#[pymodule]
fn math_ops(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(add, m)?)?;
    Ok(())
}
```

**`main.py`**
```python
import pyrust
pyrust.enable()

import math_ops
print(math_ops.add(2, 3))  # 5
```

On first import, Pyrust generates a `Cargo.toml`, compiles the crate in release mode, and copies the resulting shared library into `.pyrust_cache/`. A `math_ops.pyi` stub file is also generated next to `math_ops.rs`.

## How It Works

1. Python imports are intercepted by `PyrustFinder`, inserted at the front of `sys.meta_path`.
2. Pyrust looks for a `<name>.rs` file or a `<name>/lib.rs` directory.
3. The source is scanned for dependencies (`// pyrust-dep:`) and for NumPy/Polars types.
4. A `Cargo.toml` is generated in a temporary directory and the crate is built with `cargo build` (release or debug).
5. The compiled shared library is copied into `.pyrust_cache/`, named with a hash of the source and the build mode — so subsequent imports of the same code skip compilation entirely.
6. A `.pyi` stub file is generated next to the original source.
7. The module is loaded via `ExtensionFileLoader` under the name of the function found in the source's `#[pymodule]` attribute.

## Declaring Dependencies in Rust Code

Instead of a separate `Cargo.toml`, dependencies are declared directly in the `.rs` file:

```rust
// pyrust-dep: rand = "0.8"
// pyrust-dep: serde = { version = "1.0", features = ["derive"] }
```

`pyo3` is always added automatically, and `numpy`/`ndarray` or `pyo3-polars`/`polars` are added automatically whenever matching types are detected in the code.

## Multi-File Projects

Instead of a single file, you can use a directory with `lib.rs` as the entry point — the whole directory is copied as the crate's `src/`:

```
my_crate/
├── lib.rs
└── utils.rs
```

```python
import my_crate  # compiles the entire directory as a crate
```

## Configuration

```python
import pyrust

pyrust.enable(debug=True, release=False)
```

- `release` (default `True`) — `False` compiles without optimizations (faster build, slower runtime).
- `debug` (default `False`) — enables verbose Pyrust logging (via `loguru`) on `stderr`.

## Caching

Compiled libraries are kept in a `.pyrust_cache/` directory in the current working directory, named as `<module>_<source_hash>_<mode>.<extension>`. Any change to the source (the file itself, or any file in a crate directory) invalidates the cache and triggers a rebuild.

## Troubleshooting

- **`'cargo' command not found`** — install Rust from https://rustup.rs/ and make sure `cargo` is on your `PATH`.
- **Rust compilation error** — the full `stderr`/`stdout` from `cargo build` is logged; fixing it usually comes down to an error in the `.rs` code itself or a missing dependency (`pyrust-dep:`).
- **Warning about `#[pymodule]` name mismatch** — Pyrust still loads the module correctly (it matches the real function name), but for clarity it's a good idea to name the `#[pymodule]` function the same as the import name.
