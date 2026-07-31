import sys
import os
import re
import tempfile
import subprocess
import shutil
import hashlib
import importlib.util
from importlib.abc import MetaPathFinder
from importlib.machinery import ExtensionFileLoader

# We import logger directly from loguru instead of standard logging
from loguru import logger

CACHE_DIR = ".pyrust_cache"


class PyrustConfig:
    """Global configuration for the Pyrust Library."""
    release_mode = True


def get_source_hash(source_path: str) -> str:
    """Calculates the SHA-256 hash of a file or an entire directory."""
    hasher = hashlib.sha256()

    if os.path.isfile(source_path):
        with open(source_path, 'rb') as f:
            hasher.update(f.read())
    else:
        # If it's a directory, hash all inner files (useful for multi-file projects)
        for root, _, files in os.walk(source_path):
            for file in sorted(files):
                file_path = os.path.join(root, file)
                with open(file_path, 'rb') as f:
                    hasher.update(f.read())

    return hasher.hexdigest()


def parse_dependencies(rs_path: str) -> str:
    """Scans the .rs file for special dependency comments and auto-detects features like NumPy and Polars."""
    # We use PyO3 version 0.28.2 for compatibility with rust-numpy and pyo3-polars
    deps = ['pyo3 = { version = "0.28.2", features = ["extension-module", "abi3-py314"] }']
    pattern = re.compile(r'^\s*//\s*pyrust-dep:\s*(.+)$')

    has_numpy = False
    has_polars = False

    with open(rs_path, 'r', encoding='utf-8') as f:
        content = f.read()

        # Zero-Config Magic: Detect NumPy types in the source code
        if "PyArray" in content or "PyReadonlyArray" in content:
            has_numpy = True

        # Zero-Config Magic: Detect Polars types in the source code
        if "PyDataFrame" in content or "PySeries" in content:
            has_polars = True

        for line in content.splitlines():
            match = pattern.match(line)
            if match:
                deps.append(match.group(1).strip())

    # If NumPy keywords are found, automatically inject dependencies
    if has_numpy:
        logger.info("[Pyrust] NumPy detected. Automatically adding 'numpy' and 'ndarray' dependencies.")
        deps.append('numpy = "0.28.0"')
        deps.append('ndarray = "0.17.2"')

    # If Polars keywords are found, automatically inject dependencies
    if has_polars:
        logger.info("[Pyrust] Polars detected. Automatically adding 'pyo3-polars' and 'polars' dependencies.")
        deps.append('pyo3-polars = "0.27.0"')
        deps.append('polars = { version = "0.54.4", features = ["strings", "temporal", "lazy"] }')

    return "\n".join(deps)


def find_pymodule_name(rs_path: str) -> str | None:
    """
    Extracts the name of the function annotated with #[pymodule].

    PyO3 generates a C export symbol named `PyInit_<that_function_name>`.
    This symbol name is what actually has to be passed as `name` to
    `importlib.util.spec_from_file_location` / used by `ExtensionFileLoader` —
    NOT the arbitrary module name the user typed after `%%rust`, and NOT
    a hash, and NOT the .so filename. Those three things are independent
    of each other, so we must read the real name straight from the source.
    """
    try:
        with open(rs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    match = re.search(
        r'#\[pymodule\]\s*(?:#\[.*?\]\s*)*(?:pub\s+)?fn\s+([a-zA-Z0-9_]+)',
        content,
        re.DOTALL
    )
    return match.group(1) if match else None


def generate_type_stubs(main_rs_path: str, pyi_out_path: str):
    """
    Parses Rust source to generate a .pyi stub file.
    Supports #[pyfunction], #[pyclass], #[pymethods], and advanced types (Vec, Option, HashMap, NumPy, Polars).
    """

    def resolve_type(rust_type: str) -> str:
        if not rust_type:
            return "Any"

        type_map = {
            "i8": "int", "i16": "int", "i32": "int", "i64": "int", "i128": "int", "isize": "int",
            "u8": "int", "u16": "int", "u32": "int", "u64": "int", "u128": "int", "usize": "int",
            "f32": "float", "f64": "float",
            "bool": "bool", "String": "str", "&str": "str", "str": "str",
        }

        # Clean up references & lifetimes (e.g., &'a mut String -> String)
        rust_type = re.sub(r"&'?\w*\s*(mut\s+)?", "", rust_type.strip())

        # Unwrap PyResult<T> -> T
        pyresult_match = re.match(r'PyResult<(.+)>', rust_type)
        if pyresult_match:
            rust_type = pyresult_match.group(1)

        res = rust_type
        # Handle Generics and library specific types via Regex substitution
        res = re.sub(r'Bound<.+?,\s*(.+?)>', r'\1', res)
        res = re.sub(r'Py(?:Readonly)?Array\d*<.+?>', 'np.ndarray', res)
        res = re.sub(r'\bPyDataFrame\b', 'pl.DataFrame', res)
        res = re.sub(r'\bPySeries\b', 'pl.Series', res)
        res = re.sub(r'Vec<(.+?)>', r'list[\1]', res)
        res = re.sub(r'Option<(.+?)>', r'Optional[\1]', res)
        res = re.sub(r'HashMap<(.+?),\s*(.+?)>', r'dict[\1, \2]', res)

        # Replace base types
        for rs_t, py_t in type_map.items():
            res = re.sub(rf'\b{re.escape(rs_t)}\b', py_t, res)

        return res

    def split_args_smart(args_str: str) -> list[str]:
        """Splits arguments by comma, but ignores commas inside generics like <...>."""
        args = []
        current = []
        depth = 0
        for char in args_str:
            if char == '<':
                depth += 1
            elif char == '>':
                depth -= 1
            elif char == ',' and depth == 0:
                args.append("".join(current).strip())
                current = []
                continue
            current.append(char)
        if current:
            args.append("".join(current).strip())
        return args

    def parse_args(args_str: str, is_method: bool = False) -> str:
        py_args = ["self"] if is_method else []
        if not args_str:
            return ", ".join(py_args)

        # Use the smart split instead of simple .split(',')
        for arg in split_args_smart(args_str):
            arg = arg.strip()
            if not arg or arg.startswith("py:") or arg in ("py", "_py", "&self", "&mut self", "mut self", "self"):
                continue

            parts = arg.split(':')
            if len(parts) == 2:
                arg_name = parts[0].strip()
                r_type = parts[1].strip()
                py_args.append(f"{arg_name}: {resolve_type(r_type)}")
            else:
                py_args.append(arg)

        return ", ".join(py_args)

    stubs = []
    try:
        with open(main_rs_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 1. Parse #[pyfunction]
        func_pattern = re.compile(
            r'#\[pyfunction\]\s*(?:#\[.*?\]\s*)*(?:pub\s+)?fn\s+([a-zA-Z0-9_]+)(?:<.*?>)?\s*\((.*?)\)(?:\s*->\s*([^{]+))?',
            re.DOTALL
        )
        for match in func_pattern.finditer(content):
            func_name = match.group(1)
            parsed_args = parse_args(match.group(2), is_method=False)
            ret_type = resolve_type(match.group(3)) if match.group(3) else "None"
            stubs.append(f"def {func_name}({parsed_args}) -> {ret_type}: ...")

        # 2. Parse #[pyclass]
        class_pattern = re.compile(r'#\[pyclass(?:.*?)?\]\s*(?:pub\s+)?(?:struct|enum)\s+([a-zA-Z0-9_]+)')
        classes = {match.group(1): [] for match in class_pattern.finditer(content)}

        # 3. Parse #[pymethods] inside impl blocks
        impl_pattern = re.compile(r'#\[pymethods\]\s*impl\s+([a-zA-Z0-9_]+)\s*\{(.*?)^\}', re.DOTALL | re.MULTILINE)
        for match in impl_pattern.finditer(content):
            class_name = match.group(1)
            impl_body = match.group(2)

            if class_name not in classes:
                classes[class_name] = []

            method_pattern = re.compile(
                r'(#\[new\]\s*)?(?:#\[.*?\]\s*)*(?:pub\s+)?fn\s+([a-zA-Z0-9_]+)(?:<.*?>)?\s*\((.*?)\)(?:\s*->\s*([^{]+))?',
                re.DOTALL
            )
            for m_match in method_pattern.finditer(impl_body):
                is_new = bool(m_match.group(1))
                method_name = "__init__" if is_new else m_match.group(2)

                parsed_args = parse_args(m_match.group(3), is_method=True)
                ret_str = m_match.group(4)
                ret_type = resolve_type(ret_str) if ret_str and not is_new else "None"

                classes[class_name].append(f"    def {method_name}({parsed_args}) -> {ret_type}: ...")

        # Combine class stubs
        for class_name, methods in classes.items():
            stubs.append(f"class {class_name}:")
            if not methods:
                stubs.append("    pass")
            else:
                stubs.extend(methods)
            stubs.append("")

        if stubs:
            with open(pyi_out_path, "w", encoding="utf-8") as f:
                f.write("from typing import Any, Optional\n")
                if any("np.ndarray" in stub for stub in stubs):
                    f.write("import numpy as np\n")
                if any("pl.DataFrame" in stub or "pl.Series" in stub for stub in stubs):
                    f.write("import polars as pl\n")
                f.write("\n")
                f.write("\n".join(stubs) + "\n")
            logger.debug(f"[Pyrust] Generated type stubs at '{pyi_out_path}'")

    except Exception as e:
        logger.debug(f"[Pyrust] Could not generate stubs: {e}")


def compile_rust_to_so(module_name: str, source_path: str, is_dir: bool) -> str:
    if shutil.which("cargo") is None:
        logger.error("'cargo' command not found.")
        logger.error("You must have Rust installed to use this library.")
        logger.error("Please visit: https://rustup.rs/")
        raise SystemExit(1)

    os.makedirs(CACHE_DIR, exist_ok=True)
    current_hash = get_source_hash(source_path)
    build_mode = "release" if PyrustConfig.release_mode else "debug"

    # Cache format: module_hash_release.so
    cached_prefix = f"{module_name.replace('.', '_')}_{current_hash}_{build_mode}"
    for file in os.listdir(CACHE_DIR):
        if file.startswith(cached_prefix) and file.endswith((".so", ".pyd", ".dylib")):
            logger.debug(f"[Pyrust] Using cached {build_mode} version for '{module_name}'")
            return os.path.join(CACHE_DIR, file)

    logger.info(f"[Pyrust] Compiling '{source_path}' ({build_mode} mode)...")
    build_dir = tempfile.mkdtemp(prefix="pyrust_")

    try:
        crate_name = module_name.split('.')[-1]

        # If it's a directory, parse dependencies from the lib.rs inside it
        main_rs = os.path.join(source_path, "lib.rs") if is_dir else source_path
        parsed_deps = parse_dependencies(main_rs)

        cargo_toml = f"""[package]
name = "{crate_name}"
version = "0.1.0"
edition = "2021"

[lib]
name = "{crate_name}"
crate-type = ["cdylib"]

[dependencies]
{parsed_deps}
"""
        with open(os.path.join(build_dir, "Cargo.toml"), "w") as f:
            f.write(cargo_toml)

        src_dir = os.path.join(build_dir, "src")

        # Handle copying a single file vs an entire directory
        if is_dir:
            shutil.copytree(source_path, src_dir)
        else:
            os.makedirs(src_dir)
            shutil.copy(source_path, os.path.join(src_dir, "lib.rs"))

        cmd = ["cargo", "build"]
        if PyrustConfig.release_mode:
            cmd.append("--release")

        try:
            subprocess.run(cmd, cwd=build_dir, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            logger.error("\n[Pyrust] Rust compilation error:")
            logger.error(e.stderr.decode('utf-8'))
            logger.error(e.stdout.decode('utf-8'))
            raise SystemExit(1)

        # Generate type stubs (.pyi) right next to the original source code
        base_dir = os.path.dirname(source_path) if is_dir else os.path.dirname(source_path)
        pyi_out_path = os.path.join(base_dir, f"{crate_name}.pyi")
        generate_type_stubs(main_rs, pyi_out_path)

        # Find compiled binary in correct directory (debug or release)
        target_dir = os.path.join(build_dir, "target", build_mode)
        for file in os.listdir(target_dir):
            if file.endswith((".so", ".pyd", ".dylib")):
                ext = os.path.splitext(file)[1]
                cached_lib_path = os.path.join(CACHE_DIR, f"{cached_prefix}{ext}")

                source_lib = os.path.join(target_dir, file)
                shutil.copy(source_lib, cached_lib_path)
                return cached_lib_path

        raise FileNotFoundError("Pyrust Error: Compiled library not found in the target directory.")

    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


class PyrustFinder(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        base_path = fullname.replace('.', os.sep)
        rs_file = base_path + '.rs'
        rs_dir = base_path

        # Support both 'module.rs' file and 'module/' directory structure
        is_dir = False
        source_path = None

        if os.path.isfile(rs_file):
            source_path = rs_file
        elif os.path.isdir(rs_dir) and os.path.isfile(os.path.join(rs_dir, "lib.rs")):
            source_path = rs_dir
            is_dir = True
        else:
            return None

        lib_path = compile_rust_to_so(fullname, source_path, is_dir)

        # IMPORTANT: PyO3 exports a C symbol called PyInit_<pymodule_fn_name>,
        # which depends only on the name of the function annotated with
        # #[pymodule] in the Rust source. It has nothing to do with `fullname`
        # (the dotted Python import path). If the two differ, the import
        # will fail with a cryptic "dynamic module does not define module
        # export function" error. So we resolve the real symbol name from
        # the source and load under that name instead of blindly trusting
        # `fullname`.
        main_rs = os.path.join(source_path, "lib.rs") if is_dir else source_path
        pymodule_name = find_pymodule_name(main_rs)

        if pymodule_name is None:
            logger.error(
                f"[Pyrust] Could not find a '#[pymodule] fn ...' in '{main_rs}'. "
                f"Cannot determine which PyInit_ symbol to load."
            )
            raise ImportError(
                f"Pyrust: no #[pymodule] function found in '{main_rs}'"
            )

        if pymodule_name != fullname.split('.')[-1]:
            logger.warning(
                f"[Pyrust] The #[pymodule] function is named '{pymodule_name}', "
                f"but the Python import name is '{fullname}'. "
                f"Loading under '{pymodule_name}' to match the compiled PyInit_ symbol. "
                f"Consider renaming the #[pymodule] function to match the module name "
                f"to avoid confusion."
            )

        loader = ExtensionFileLoader(pymodule_name, lib_path)
        return importlib.util.spec_from_file_location(pymodule_name, lib_path, loader=loader)


def enable(debug=False, release=True):
    """
    Enables the Pyrust import hook.
    :param debug: If True, prints verbose debug logs.
    :param release: If False, compiles Rust without optimizations (much faster build, slower execution).
    """
    PyrustConfig.release_mode = release

    if debug:
        logger.add(
            sys.stderr,
            level="DEBUG",
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
            filter=lambda record: "[Pyrust]" in record["message"]
        )

    # Prevent adding the finder multiple times if enable() is called twice
    if not any(isinstance(f, PyrustFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, PyrustFinder())
