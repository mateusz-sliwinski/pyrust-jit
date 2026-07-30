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
    """Global configuration for the Pyrust framework."""
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
    """Scans the .rs file for special dependency comments (// pyrust-dep: ...)."""
    deps = ['pyo3 = { version = "0.29.0", features = ["extension-module", "abi3-py310"] }']
    pattern = re.compile(r'^\s*//\s*pyrust-dep:\s*(.+)$')

    with open(rs_path, 'r', encoding='utf-8') as f:
        for line in f:
            match = pattern.match(line)
            if match:
                deps.append(match.group(1).strip())

    return "\n".join(deps)


def generate_type_stubs(main_rs_path: str, pyi_out_path: str):
    """
    Naively parses Rust source to generate a .pyi stub file.
    This gives PyCharm and VSCode instant autocompletion for #[pyfunction].
    """
    type_map = {
        "i8": "int", "i16": "int", "i32": "int", "i64": "int", "isize": "int",
        "u8": "int", "u16": "int", "u32": "int", "u64": "int", "usize": "int",
        "f32": "float", "f64": "float",
        "bool": "bool", "String": "str", "&str": "str",
    }

    stubs = []
    try:
        with open(main_rs_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Match: #[pyfunction] fn my_func(a: i32) -> String {
        pattern = re.compile(r'#\[pyfunction\]\s*(?:#\[.*?\]\s*)*fn\s+([a-zA-Z0-9_]+)\s*\((.*?)\)(?:\s*->\s*([^{]+))?')

        for match in pattern.finditer(content):
            func_name = match.group(1)
            args_str = match.group(2)
            ret_str = match.group(3)

            py_args = []
            for arg in args_str.split(','):
                arg = arg.strip()
                # Skip PyO3 internal types like 'py: Python'
                if not arg or arg.startswith("py:") or arg == "py" or arg == "_py":
                    continue

                parts = arg.split(':')
                if len(parts) == 2:
                    arg_name = parts[0].strip()
                    r_type = parts[1].strip()
                    p_type = type_map.get(r_type, "Any")
                    py_args.append(f"{arg_name}: {p_type}")
                else:
                    py_args.append(arg)

            ret_type = "Any"
            if ret_str:
                r_type = ret_str.strip()
                ret_type = type_map.get(r_type, "Any")
            else:
                ret_type = "None"

            stubs.append(f"def {func_name}({', '.join(py_args)}) -> {ret_type}: ...")

        # Write the .pyi file only if we found functions
        if stubs:
            with open(pyi_out_path, "w", encoding="utf-8") as f:
                f.write("from typing import Any\n\n")
                f.write("\n".join(stubs) + "\n")
            logger.debug(f"[Pyrust] Generated type stubs at '{pyi_out_path}'")

    except Exception as e:
        logger.debug(f"[Pyrust] Could not generate stubs: {e}")


def compile_rust_to_so(module_name: str, source_path: str, is_dir: bool) -> str:
    if shutil.which("cargo") is None:
        logger.error("❌ 'cargo' command not found!")
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
            logger.error("\n[Pyrust] ❌ Rust compilation error:")
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
        loader = ExtensionFileLoader(fullname, lib_path)
        return importlib.util.spec_from_file_location(fullname, lib_path, loader=loader)


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