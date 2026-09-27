"""Static safety guards for the recorder's read-only Python package."""

from __future__ import annotations

import ast
from pathlib import Path

_ROS_OUTPUT_FACTORIES = frozenset(("Publisher", "ServiceProxy"))
_ROS_OUTPUT_METHOD = "publish"


class _RosWriteVisitor(ast.NodeVisitor):
    """Track simple ROS aliases and reject only ROS-derived output calls."""

    def __init__(self) -> None:
        self.violations: list[tuple[int, str]] = []
        self._rospy_modules = {"rospy"}
        self._output_factories: set[str] = set()
        self._output_instances: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "rospy":
                self._rospy_modules.add(alias.asname or alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module != "rospy":
            return
        for alias in node.names:
            if alias.name in _ROS_OUTPUT_FACTORIES:
                self._output_factories.add(alias.asname or alias.name)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        self._bind_targets(node.targets, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is None:
            return
        self.visit(node.value)
        self._bind_targets((node.target,), node.value)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self._output_factories:
            self.violations.append((node.lineno, "ROS output factory alias reference"))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self._is_output_factory(node):
            self.violations.append((node.lineno, "ROS output factory reference"))
        elif self._is_output_method(node):
            self.violations.append(
                (node.lineno, "ROS-derived publisher output reference")
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        reason = self._dynamic_reference_reason(node)
        if reason is not None:
            self.violations.append((node.lineno, reason))
        self.generic_visit(node)

    def _bind_targets(self, targets: list[ast.expr], value: ast.expr) -> None:
        names = [name for target in targets for name in self._target_names(target)]
        for name in names:
            self._rospy_modules.discard(name)
            self._output_factories.discard(name)
            self._output_instances.discard(name)
        if self._is_rospy_module(value):
            self._rospy_modules.update(names)
        elif self._is_output_factory(value):
            self._output_factories.update(names)
        elif isinstance(value, ast.Call) and self._is_output_factory(value.func):
            self._output_instances.update(names)

    def _dynamic_reference_reason(self, node: ast.Call) -> str | None:
        if not self._is_getattr_call(node):
            return None
        target, attribute = node.args[:2]
        if self._is_rospy_module(target):
            if not isinstance(attribute, ast.Constant) or not isinstance(
                attribute.value, str
            ):
                return "dynamic ROS output factory lookup"
            if attribute.value in _ROS_OUTPUT_FACTORIES:
                return "dynamic ROS output factory reference"
        if (
            isinstance(target, ast.Name)
            and target.id in self._output_instances
            and isinstance(attribute, ast.Constant)
            and attribute.value == _ROS_OUTPUT_METHOD
        ):
            return "ROS-derived publisher output reference"
        return None

    def _is_rospy_module(self, expression: ast.expr) -> bool:
        return isinstance(expression, ast.Name) and expression.id in self._rospy_modules

    def _is_output_factory(self, expression: ast.expr) -> bool:
        if isinstance(expression, ast.Name):
            return expression.id in self._output_factories
        if isinstance(expression, ast.Attribute):
            return (
                self._is_rospy_module(expression.value)
                and expression.attr in _ROS_OUTPUT_FACTORIES
            )
        if self._is_getattr_call(expression):
            target, attribute = expression.args[:2]
            return self._is_rospy_module(target) and (
                not isinstance(attribute, ast.Constant)
                or not isinstance(attribute.value, str)
                or attribute.value in _ROS_OUTPUT_FACTORIES
            )
        return False

    def _is_output_method(self, expression: ast.expr) -> bool:
        if isinstance(expression, ast.Attribute):
            return (
                isinstance(expression.value, ast.Name)
                and expression.value.id in self._output_instances
                and expression.attr == _ROS_OUTPUT_METHOD
            )
        if self._is_getattr_call(expression):
            target, attribute = expression.args[:2]
            return (
                isinstance(target, ast.Name)
                and target.id in self._output_instances
                and isinstance(attribute, ast.Constant)
                and attribute.value == _ROS_OUTPUT_METHOD
            )
        return False

    @staticmethod
    def _is_getattr_call(expression: ast.expr) -> bool:
        return (
            isinstance(expression, ast.Call)
            and isinstance(expression.func, ast.Name)
            and expression.func.id == "getattr"
            and len(expression.args) >= 2
        )

    @staticmethod
    def _target_names(target: ast.expr) -> tuple[str, ...]:
        if isinstance(target, ast.Name):
            return (target.id,)
        if isinstance(target, (ast.Tuple, ast.List)):
            return tuple(
                name
                for item in target.elts
                for name in _RosWriteVisitor._target_names(item)
            )
        return ()


def find_ros_write_violations(package_root: Path) -> list[str]:
    """Recursively return static ROS output hazards below ``package_root``.

    The scanner neither imports nor executes target code.  It follows simple
    assignment chains for ``rospy`` modules, output factories, and the
    publisher/service values those factories return; unrelated ``publish``
    methods are deliberately outside the ban.
    """
    root = Path(package_root)
    violations: list[str] = []
    for source_path in sorted(root.rglob("*.py")):
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"), filename=str(source_path)
        )
        visitor = _RosWriteVisitor()
        visitor.visit(tree)
        relative_path = source_path.relative_to(root).as_posix()
        violations.extend(
            f"{relative_path}:{line}: {reason}" for line, reason in visitor.violations
        )
    return violations
