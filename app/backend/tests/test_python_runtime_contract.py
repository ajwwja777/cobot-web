from __future__ import annotations

import ast
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python before 3.11
    import tomli as tomllib


ROOT = Path(__file__).parents[1]


def test_cobot_runtime_declares_python38_and_avoids_newer_runtime_constructs():
    project = tomllib.loads((ROOT.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == ">=3.8,<3.9"
    assert project["tool"]["ruff"]["target-version"] == "py38"
    assert set(project["tool"]["ruff"]["lint"]["ignore"]) >= {"UP006", "UP045"}

    violations: list[str] = []
    for path in sorted(
        [*(ROOT / "capture_core").glob("*.py"), *(ROOT / "scripts").glob("*.py")]
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(
                keyword.arg == "slots" for keyword in node.keywords
            ):
                violations.append(f"{path.name}:{node.lineno}: dataclass slots")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "zip"
                and any(keyword.arg == "strict" for keyword in node.keywords)
            ):
                violations.append(f"{path.name}:{node.lineno}: zip strict")
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "datetime"
                and any(alias.name == "UTC" for alias in node.names)
            ):
                violations.append(f"{path.name}:{node.lineno}: datetime.UTC")
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "typing"
                and any(alias.name == "Annotated" for alias in node.names)
            ):
                violations.append(f"{path.name}:{node.lineno}: typing.Annotated")
    assert violations == []


def test_cobot_launcher_defaults_to_ros_compatible_environment():
    launcher = (ROOT / "scripts/start_task5_v1.sh").read_text(encoding="utf-8")

    assert "/home/agilex/miniconda3/envs/cobot-station/bin/python" in launcher
    assert (
        "/home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash"
        in launcher
    )
    assert "set +u" in launcher
    assert 'source "$ROS_SETUP"' in launcher


def test_fastapi_annotations_are_evaluable_by_python38_pydantic():
    api_path = ROOT / "capture_core" / "api.py"
    tree = ast.parse(api_path.read_text(encoding="utf-8"), filename=str(api_path))

    assert not any(
        isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"dict", "list", "set", "tuple"}
        for node in ast.walk(tree)
    )
