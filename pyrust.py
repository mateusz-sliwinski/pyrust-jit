import sys
import os
import re
import subprocess
import shutil
import hashlib
import threading
import time
import argparse
import importlib.util
from importlib.abc import MetaPathFinder
from importlib.machinery import ExtensionFileLoader

if os.name == 'nt':
    import msvcrt
    def _lock_file(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
    def _unlock_file(f):
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl
    def _lock_file(f):
        fcntl.flock(f, fcntl.LOCK_EX)
    def _unlock_file(f):
        fcntl.flock(f, fcntl.LOCK_UN)

# We import logger directly from loguru instead of standard logging
from loguru import logger

CACHE_DIR = os.environ.get("PYRUST_CACHE_DIR", ".pyrust_cache")
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class PyrustConfig:
    """Global configuration for the Pyrust Library."""
    release_mode = True
    # Optional dict of [profile.release] overrides for the generated
    # Cargo.toml, e.g. {"lto": True, "codegen_units": 1, "panic": "abort"}.
    # None means "don't emit a [profile.release] section at all" (Cargo's
    # own defaults apply).
    release_profile: dict | None = None


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
    py_tag = f"py{sys.version_info.major}{sys.version_info.minor}"
    deps = [f'pyo3 = {{ version = "0.28.2", features = ["extension-module", "abi3-{py_tag}"] }}']
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


def render_profile_section(profile: dict | None) -> str:
    """
    Renders a user-supplied dict of Cargo release-profile overrides (passed
    via enable(release_profile={...})) into a [profile.release] TOML block.

    JIT-compiled extensions are usually rebuilt rarely but executed a lot,
    so trading compile time for runtime performance (lto, codegen-units,
    panic=abort) is often worth it — but it's a real tradeoff, so it's left
    as an explicit opt-in rather than a hardcoded default.

    Supports str, bool, and int values, e.g.:
        {"lto": True, "codegen_units": 1, "panic": "abort"}
    Underscores in keys are kept as-is since Cargo accepts both
    "codegen-units" and "codegen_units".
    """
    if not profile:
        return ""

    lines = ["[profile.release]"]
    for key, value in profile.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, int):
            rendered = str(value)
        elif isinstance(value, str):
            rendered = f'"{value}"'
        else:
            raise ValueError(
                f"[Pyrust] Unsupported release_profile value for '{key}': {value!r} "
                f"(expected str, bool, or int)"
            )
        lines.append(f"{key} = {rendered}")

    return "\n".join(lines) + "\n"


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


def _split_top_level(s: str, sep: str = ',') -> list[str]:
    """
    Splits a string on `sep` only at nesting depth 0, treating <...>, (...)
    and [...] as opaque groups. Used to pull apart generic type arguments
    (Vec<Option<i32>>, HashMap<K, V>) and tuple members ((i32, String))
    without getting confused by commas that belong to an inner type.
    """
    parts = []
    current = []
    depth = 0
    for char in s:
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth -= 1
        elif char == sep and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current).strip())
    return parts


def resolve_type(rust_type: str) -> str:
    """
    Converts a Rust type string into its Python stub equivalent, recursively.

    Handles, at any nesting depth: PyResult<T> and bare Result<T, E> (both
    collapse to T — the stub describes the successful return value, not the
    error path), Option<T>, Vec<T> (including Vec<Vec<T>>), HashMap<K, V>,
    Bound<'py, T>, NumPy/Polars PyO3 wrapper types, tuples like (i32, String),
    the unit type (), and falls back to returning unknown identifiers
    (including local #[pyclass] struct/enum names) unchanged, since those
    already match the generated Python class name as-is.
    """
    if not rust_type:
        return "Any"

    rust_type = rust_type.strip()

    if rust_type in ("Vec<u8>", "&[u8]"):
        return "bytes"

    if rust_type in ("PyObject", "PyAny"):
        return "Any"

    # Clean up references & lifetimes (e.g., &'a mut String -> String)
    rust_type = re.sub(r"&'?\w*\s*(mut\s+)?", "", rust_type).strip()

    type_map = {
        "i8": "int", "i16": "int", "i32": "int", "i64": "int", "i128": "int", "isize": "int",
        "u8": "int", "u16": "int", "u32": "int", "u64": "int", "u128": "int", "usize": "int",
        "f32": "float", "f64": "float",
        "bool": "bool", "String": "str", "str": "str",
    }

    # Unit type / empty tuple
    if rust_type == "()":
        return "None"

    # Tuple types: (T1, T2, ...)
    if rust_type.startswith("(") and rust_type.endswith(")"):
        inner = rust_type[1:-1].strip()
        members = [resolve_type(part) for part in _split_top_level(inner, ',') if part.strip()]
        if not members:
            return "None"
        return f"tuple[{', '.join(members)}]"

    # Generic wrapper: Name<Args> (also matches paths like std::result::Result<T, E>)
    generic_match = re.match(r'^([A-Za-z_][A-Za-z0-9_:]*)\s*<(.+)>$', rust_type, re.DOTALL)
    if generic_match:
        base = generic_match.group(1).split("::")[-1]
        args = [a for a in _split_top_level(generic_match.group(2), ',') if a]

        if base in ("PyResult", "Result"):
            # Both collapse to the Ok/success type — the stub describes what
            # you get back, not the error variant.
            return resolve_type(args[0]) if args else "Any"
        if base == "Option":
            return f"Optional[{resolve_type(args[0])}]" if args else "Any"
        if base == "Vec":
            return f"list[{resolve_type(args[0])}]" if args else "list[Any]"
        if base in ("HashMap", "BTreeMap"):
            if len(args) == 2:
                return f"dict[{resolve_type(args[0])}, {resolve_type(args[1])}]"
            return "dict[Any, Any]"
        if base == "Bound":
            # Bound<'py, T> — the lifetime arg is irrelevant to the stub
            return resolve_type(args[-1]) if args else "Any"
        if re.match(r'^Py(?:Readonly)?Array\d*$', base):
            return "np.ndarray"
        if base == "PyDataFrame":
            return "pl.DataFrame"
        if base == "PySeries":
            return "pl.Series"
        # Unknown generic wrapper (custom #[pyclass] generic, etc.):
        # best-effort passthrough with resolved args rather than dropping them.
        return f"{base}[{', '.join(resolve_type(a) for a in args)}]"

    # Plain base type, or an unrecognized identifier — which, for a local
    # #[pyclass] struct/enum, is exactly the name we want: PyO3 exposes it
    # to Python under that same name, so returning it unchanged is correct,
    # not just accidentally working.
    return type_map.get(rust_type, rust_type)


def generate_type_stubs(main_rs_path: str, pyi_out_path: str):
    """
    Parses Rust source to generate a .pyi stub file.
    Supports #[pyfunction], #[pyclass], #[pymethods], and advanced types (Vec, Option, HashMap, NumPy, Polars).
    """

    def split_args_smart(args_str: str) -> list[str]:
        """Splits arguments by comma, but ignores commas inside generics like <...>."""
        return _split_top_level(args_str, ',')

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


def _run_cargo_streaming(cmd: list[str], cwd: str) -> tuple[int, str]:
    """
    Runs `cargo build` while streaming its own output live, instead of
    buffering everything via capture_output=True and only showing it after
    the process exits. A release build pulling in numpy/polars can easily
    run well over a minute, and with the old capture_output approach the
    user just sees nothing and has no way to tell it isn't hung. We echo
    each line from cargo as it arrives, and during any gap where cargo
    itself goes quiet (e.g. the final link step, or dependency downloads)
    we show a small spinner with elapsed time on a single overwritten line
    so it's obvious the build is still progressing.

    Returns (returncode, full_captured_output) so the caller can still
    print the complete log if the build fails.
    """
    process = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )

    captured_lines: list[str] = []
    last_output_time = time.time()
    reader_done = threading.Event()

    def reader():
        nonlocal last_output_time
        for line in iter(process.stdout.readline, ""):
            captured_lines.append(line)
            last_output_time = time.time()
            # Clear the current spinner line before printing cargo's own
            # output, so the two don't get smeared together.
            sys.stderr.write("\r\033[K" + line)
            sys.stderr.flush()
        reader_done.set()

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    start_time = time.time()
    frame = 0
    while not reader_done.is_set():
        if time.time() - last_output_time > 0.5:
            elapsed = time.time() - start_time
            sys.stderr.write(
                f"\r[Pyrust] compiling {_SPINNER_FRAMES[frame % len(_SPINNER_FRAMES)]} ({elapsed:0.0f}s)"
            )
            sys.stderr.flush()
            frame += 1
        reader_done.wait(0.1)

    reader_thread.join()
    process.wait()
    sys.stderr.write("\r\033[K")
    sys.stderr.flush()
    return process.returncode, "".join(captured_lines)


def compile_rust_to_so(module_name: str, source_path: str, is_dir: bool) -> str:
    if shutil.which("cargo") is None:
        logger.error("'cargo' command not found.")
        logger.error("You must have Rust installed to use this library.")
        logger.error("Please visit: https://rustup.rs/")
        raise SystemExit(1)

    os.makedirs(CACHE_DIR, exist_ok=True)
    current_hash = get_source_hash(source_path)
    build_mode = "release" if PyrustConfig.release_mode else "debug"

    crate_name = module_name.split('.')[-1]

    # If it's a directory, parse dependencies from the lib.rs inside it
    main_rs = os.path.join(source_path, "lib.rs") if is_dir else source_path
    parsed_deps = parse_dependencies(main_rs)

    # The resolved dependency string now also encodes the abi3-pyXY tag for
    # whichever interpreter is running us, plus any pyrust-dep: lines. Folding
    # its hash into the cache key means a different Python version or a
    # changed dependency comment invalidates stale .so files instead of
    # silently reusing one built for a different, possibly incompatible, ABI.
    # The release profile is folded in too, since e.g. lto/codegen-units
    # changes produce a functionally-equivalent but differently-optimized
    # binary that a plain source+deps hash wouldn't catch.
    profile_section = render_profile_section(PyrustConfig.release_profile)
    deps_hash = hashlib.sha256((parsed_deps + profile_section).encode("utf-8")).hexdigest()[:12]

    # Cache format: module_sourcehash_depshash_mode.so
    cached_prefix = f"{module_name.replace('.', '_')}_{current_hash}_{deps_hash}_{build_mode}"
    for file in os.listdir(CACHE_DIR):
        if file.startswith(cached_prefix) and file.endswith((".so", ".pyd", ".dylib")):
            logger.debug(f"[Pyrust] Using cached {build_mode} version for '{module_name}'")
            return os.path.join(CACHE_DIR, file)

    logger.info(f"[Pyrust] Compiling '{source_path}' ({build_mode} mode)...")

    # Persistent build directory per module, instead of a fresh tempdir on
    # every compile. A brand-new tempdir means Cargo's incremental
    # compilation cache under target/ is thrown away each time, which is
    # painful once numpy/polars are dependencies (60s+ rebuilds instead of
    # a couple of seconds). We keep one build_dir per module across calls
    # and only ever wipe/rewrite src/ and Cargo.toml; target/ is left alone
    # so Cargo can reuse what it already compiled.
    build_dir = os.path.join(CACHE_DIR, "build", module_name.replace('.', '_'))
    os.makedirs(build_dir, exist_ok=True)

    # Two processes (or threads) compiling the same module at the same time
    # would otherwise race on the same src/ and target/ dirs. A simple
    # exclusive file lock serializes them, cross-platform via _lock_file.
    lock_path = build_dir + ".lock"
    with open(lock_path, "w") as lock_file:
        _lock_file(lock_file)
        try:
            cargo_toml = f"""[package]
name = "{crate_name}"
version = "0.1.0"
edition = "2021"

[lib]
name = "{crate_name}"
crate-type = ["cdylib"]

[dependencies]
{parsed_deps}

{profile_section}"""
            with open(os.path.join(build_dir, "Cargo.toml"), "w") as f:
                f.write(cargo_toml)

            src_dir = os.path.join(build_dir, "src")

            # Rewrite src/ fresh each time (cheap) while deliberately leaving
            # target/ untouched so Cargo's incremental cache survives.
            if os.path.isdir(src_dir):
                shutil.rmtree(src_dir)

            if is_dir:
                shutil.copytree(source_path, src_dir)
            else:
                os.makedirs(src_dir)
                shutil.copy(source_path, os.path.join(src_dir, "lib.rs"))

            cmd = ["cargo", "build"]
            if PyrustConfig.release_mode:
                cmd.append("--release")

            returncode, build_output = _run_cargo_streaming(cmd, build_dir)
            if returncode != 0:
                logger.error("\n[Pyrust] Rust compilation error:")
                logger.error(build_output)
                raise ImportError(f"Pyrust: Compilation failed for module '{crate_name}'. Check logs above.")

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
            _unlock_file(lock_file)


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


def enable(debug=False, release=True, release_profile: dict | None = None):
    """
    Enables the Pyrust import hook.
    :param debug: If True, prints verbose debug logs.
    :param release: If False, compiles Rust without optimizations (much faster build, slower execution).
    :param release_profile: Optional dict of [profile.release] overrides written into the
        generated Cargo.toml, e.g. {"lto": True, "codegen_units": 1, "panic": "abort"}.
        JIT extensions are typically compiled rarely but run a lot, so pushing further on
        runtime performance at the cost of compile time is often worth it here — but since
        it IS a real tradeoff (and panic="abort" changes unwinding behavior), it's opt-in
        rather than a silent default. Only applies to release builds. Values may be str,
        bool, or int.
    """
    PyrustConfig.release_mode = release
    PyrustConfig.release_profile = release_profile

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


# --------------------------------------------------------------------------
# Command-line interface: `pyrust --purge` / `python -m pyrust_jit --purge`
# --------------------------------------------------------------------------

def _cli_purge() -> None:
    """
    Deletes the local .pyrust_cache directory — both the compiled
    .so/.pyd/.dylib artifacts and the persistent per-module Cargo build
    dirs (including their target/ incremental-compilation state) added for
    faster rebuilds. Use this when you want a guaranteed clean rebuild,
    e.g. after a toolchain upgrade or if the cache is suspected corrupted.
    """
    if os.path.isdir(CACHE_DIR):
        size = sum(
            os.path.getsize(os.path.join(root, f))
            for root, _, files in os.walk(CACHE_DIR)
            for f in files
        )
        shutil.rmtree(CACHE_DIR)
        print(f"[Pyrust] Purged '{CACHE_DIR}' ({size / (1024 * 1024):.1f} MB freed).")
    else:
        print(f"[Pyrust] Nothing to purge — '{CACHE_DIR}' does not exist.")


def _cli_rustc_version() -> None:
    """Prints the rustc toolchain version that `cargo build` will use."""
    if shutil.which("rustc") is None:
        print("[Pyrust] 'rustc' not found on PATH. Install it from https://rustup.rs/")
        return
    result = subprocess.run(["rustc", "--version"], capture_output=True, text=True)
    output = (result.stdout or result.stderr).strip()
    print(f"[Pyrust] {output}")


def _cli_main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="pyrust", description="pyrust-jit command-line utilities.")
    parser.add_argument(
        "--purge", action="store_true",
        help="Delete the local .pyrust_cache directory (compiled binaries and persistent Cargo build dirs).",
    )
    parser.add_argument(
        "--rustc-version", action="store_true",
        help="Print the rustc toolchain version that will be used to compile.",
    )
    args = parser.parse_args(argv)

    if not (args.purge or args.rustc_version):
        parser.print_help()
        return

    if args.rustc_version:
        _cli_rustc_version()
    if args.purge:
        _cli_purge()


if __name__ == "__main__":
    _cli_main()
