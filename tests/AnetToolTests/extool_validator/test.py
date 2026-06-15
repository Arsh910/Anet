"""Unit tests for extool_validator — the contract checker behind /newtool.

Offline. Writes throwaway tool modules to temp dirs and asserts the verdict.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.core.extool_validator import validate


def _write(body: str) -> Path:
    d = Path(tempfile.mkdtemp()) / "good_tool"
    d.mkdir(parents=True)
    p = d / "__init__.py"
    p.write_text(body, encoding="utf-8")
    return p


_VALID = (
    "SCHEMA = {\n"
    "    'type': 'function',\n"
    "    'function': {\n"
    "        'name': 'good_tool',\n"
    "        'description': 'does a thing',\n"
    "        'parameters': {'type': 'object',\n"
    "            'properties': {'x': {'type': 'string', 'description': 'in'}},\n"
    "            'required': ['x']},\n"
    "    },\n"
    "}\n"
    "async def run(params: dict) -> dict:\n"
    "    return {'result': params.get('x')}\n"
)


def test_valid_tool_passes():
    ok, msgs = validate(_write(_VALID))
    assert ok, msgs


def test_missing_run_fails():
    body = _VALID.replace("async def run(params: dict) -> dict:\n    return {'result': params.get('x')}\n", "")
    ok, msgs = validate(_write(body))
    assert not ok and any("run" in m for m in msgs)


def test_missing_schema_fails():
    ok, msgs = validate(_write("async def run(params: dict) -> dict:\n    return {'result': 1}\n"))
    assert not ok and any("SCHEMA" in m for m in msgs)


def test_required_not_in_properties_fails():
    body = _VALID.replace("'required': ['x']", "'required': ['y']")
    ok, msgs = validate(_write(body))
    assert not ok and any("required" in m.lower() for m in msgs)


def test_import_error_fails():
    ok, msgs = validate(_write("import a_module_that_does_not_exist_xyz\n" + _VALID))
    assert not ok and any("import error" in m.lower() for m in msgs)


def test_dir_path_resolves_to_init():
    p = _write(_VALID)
    ok, _ = validate(p.parent)            # pass the folder, not the file
    assert ok


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: extool_validator")
