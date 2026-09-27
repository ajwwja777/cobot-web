#!/usr/bin/env python3
"""Idempotently check or add the std_srvs dependency required by Task2."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import xml.etree.ElementTree as ET


REQUIRED_PACKAGE_TAGS = ("build_depend", "build_export_depend", "exec_depend")


def package_xml_has_dependencies(text: str) -> bool:
    root = ET.fromstring(text)
    present = {(element.tag, (element.text or "").strip()) for element in root}
    return all((tag, "std_srvs") in present for tag in REQUIRED_PACKAGE_TAGS)


def ensure_package_xml(text: str) -> str:
    if package_xml_has_dependencies(text):
        return text
    missing = []
    root = ET.fromstring(text)
    present = {(element.tag, (element.text or "").strip()) for element in root}
    for tag in REQUIRED_PACKAGE_TAGS:
        if (tag, "std_srvs") not in present:
            missing.append("  <{}>std_srvs</{}>\n".format(tag, tag))
    marker = "  <export>"
    if marker not in text:
        raise ValueError("package.xml has no <export> insertion point")
    return text.replace(marker, "".join(missing) + "\n" + marker, 1)


def _cmake_block(text: str, marker: str) -> tuple[int, int, str]:
    start = text.find(marker)
    if start < 0:
        raise ValueError("CMakeLists.txt has no {!r} block".format(marker))
    end = text.find("\n)", start)
    if end < 0:
        raise ValueError("unterminated {!r} block".format(marker))
    return start, end, text[start:end]


def cmake_has_dependencies(text: str) -> bool:
    _, _, find_block = _cmake_block(text, "find_package(catkin REQUIRED COMPONENTS")
    _, _, catkin_block = _cmake_block(text, "catkin_package(")
    find_tokens = set(re.findall(r"[A-Za-z0-9_]+", find_block))
    dep_match = re.search(r"^[ \t]*CATKIN_DEPENDS\s+([^\n#]*)", catkin_block, re.M)
    catkin_tokens = set(dep_match.group(1).split()) if dep_match else set()
    return "std_srvs" in find_tokens and "std_srvs" in catkin_tokens


def ensure_cmake(text: str) -> str:
    _, find_end, find_block = _cmake_block(
        text, "find_package(catkin REQUIRED COMPONENTS"
    )
    if "std_srvs" not in set(re.findall(r"[A-Za-z0-9_]+", find_block)):
        text = text[:find_end] + "\n  std_srvs" + text[find_end:]

    catkin_start, catkin_end, catkin_block = _cmake_block(text, "catkin_package(")
    dep_match = re.search(r"^([ \t]*CATKIN_DEPENDS\s+)([^\n#]*)(.*)$", catkin_block, re.M)
    if dep_match is None:
        text = text[:catkin_end] + "\n CATKIN_DEPENDS std_srvs" + text[catkin_end:]
    elif "std_srvs" not in dep_match.group(2).split():
        replacement = dep_match.group(1) + dep_match.group(2).rstrip() + " std_srvs" + dep_match.group(3)
        block_updated = catkin_block[:dep_match.start()] + replacement + catkin_block[dep_match.end():]
        text = text[:catkin_start] + block_updated + text[catkin_end:]
    return text


def check(package_dir: Path) -> bool:
    return package_xml_has_dependencies(
        (package_dir / "package.xml").read_text(encoding="utf-8")
    ) and cmake_has_dependencies(
        (package_dir / "CMakeLists.txt").read_text(encoding="utf-8")
    )


def install(package_dir: Path) -> None:
    package_path = package_dir / "package.xml"
    cmake_path = package_dir / "CMakeLists.txt"
    package_path.write_text(
        ensure_package_xml(package_path.read_text(encoding="utf-8")), encoding="utf-8"
    )
    cmake_path.write_text(
        ensure_cmake(cmake_path.read_text(encoding="utf-8")), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("check", "install"))
    parser.add_argument("package_dir", type=Path)
    args = parser.parse_args()
    if args.mode == "install":
        install(args.package_dir)
    if not check(args.package_dir):
        raise SystemExit("std_srvs dependency is not fully declared")
    print("Task2 std_srvs dependency is declared.")


if __name__ == "__main__":
    main()
