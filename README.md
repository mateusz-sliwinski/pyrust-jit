# Pyrust: Zero-Boilerplate Rust in Python

Pyrust is a Python import hook that allows you to import `.rs` files directly into your Python scripts. It eliminates the need for manual C-extension compilation, setup scripts, or external build configurations by compiling your Rust code on the fly.

## How it works

When you import a Rust module in Python, Pyrust:
1. Intercepts the import call.
2. Scans the `.rs` file for inline Rust dependencies.
3. Dynamically generates a `Cargo.toml` file in a temporary directory.
4. Compiles the Rust code into a shared library using Cargo and PyO3.
5. Caches the compiled binary using a SHA-256 hash to ensure subsequent imports are instantaneous.
6. Automatically generates `.pyi` type stubs so your IDE provides autocompletion for your Rust functions.

## Prerequisites

You must have the Rust toolchain installed on your system. 
You can install it from: https://rustup.rs/

## Installation

```bash
pip install pyrust-jit