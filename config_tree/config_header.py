# SPDX-FileCopyrightText: 2026 ETH Zurich and University of Bologna and EssilorLuxottica SAS
#
# SPDX-License-Identifier: Apache-2.0
#
# Authors: Germain Haugou (germain.haugou@gmail.com)

"""C header of the fields of a config tree that software can read.

A field marked ``read=True`` (and not ``write=True``) is a fixed fact of the
target, such as a memory size or a base address. Each one becomes::

    #define CONFIG_<PATH>_<FIELD> <value>

where ``<PATH>`` is the path of its config relative to the root of the tree,
in upper case, e.g. ``CONFIG_GAP_SOC_L2_SIZE`` for the field ``size`` of
``<root>/gap/soc/l2``. These are also the names of the make variables of the
make flow (build_tree), so a value has the same name in the Makefiles and in
C, in both the gvrun and the make flow.

Writable fields are left out: their value is only known once make runs. So
are the fields set when the simulation runs (``Annotated[T, Runtime]``, see
gvrun.runtime), and the fields whose value is not a scalar (lists, configs,
objects).

Two fields giving the same name (e.g. ``soc/mem_size`` and ``soc/mem/size``)
are both left out, with a comment, so that a source using the name fails to
compile rather than getting the value of the other field.
"""

from __future__ import annotations

import enum
import os
import re
from collections.abc import Iterable
from dataclasses import fields
from typing import get_type_hints

from .config import Config


def _root(config: Config) -> Config:
    while config.parent is not None:
        config = config.parent
    return config


def _relative_path(config: Config, root: Config) -> str:
    """Path of *config* below *root*, without leading or trailing '/'."""
    path = config.path or ''
    root_path = root.path or ''
    if root_path not in ('', '/') and (path == root_path or path.startswith(root_path + '/')):
        path = path[len(root_path):]
    return path.strip('/')


def config_define_name(relative_field_path: str) -> str:
    """Name of the define (and make variable) of a field, from its path
    relative to the root of the tree, e.g. ``gap/soc/l2/size``."""
    token = re.sub(r'[^A-Za-z0-9]+', '_', relative_field_path).strip('_').upper()
    return f'CONFIG_{token}'


def _c_value(value: object, fmt: str | None) -> str | None:
    """Value as a C literal, or None if it has none."""
    if isinstance(value, enum.Enum):
        value = value.value
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, int):
        # In hex from 2^31, so that the literal is unsigned rather than long
        if value < 0:
            return f'({value})'
        if fmt == 'hex' or value > 0x7fffffff:
            return f'0x{value:x}'
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
        return f'"{escaped}"'
    return None


def _is_runtime(type_hint: object) -> bool:
    """Whether a field type is ``Annotated[T, Runtime]``. The marker belongs to
    gvrun, which config_tree does not depend on, so it is found by name."""
    for marker in getattr(type_hint, '__metadata__', ()):
        if isinstance(marker, type) and any(c.__name__ == 'Runtime' for c in marker.__mro__):
            return True
    return False


def _subtree(config: Config) -> list[Config]:
    """*config* and the configs registered below it, sorted by path."""
    result = {id(config): config}
    for node in Config.get_configs_by_path('*'):
        parent = node
        while parent is not None and parent is not config:
            parent = parent.parent
        if parent is config:
            result[id(node)] = node
    return sorted(result.values(), key=lambda c: c.path or '')


def config_defines(config: Config, nodes: Iterable[Config] | None = None
        ) -> list[tuple[str, str, str]]:
    """(name, C value, description) of the readable fields of *config* and of
    the configs below it, named after their path from the root of the tree.
    *nodes*, if given, restricts them to these configs. A name given by several
    fields has the value None."""
    root = _root(config)
    subtree = _subtree(config)
    if nodes is not None:
        kept = {id(node) for node in nodes}
        subtree = [node for node in subtree if id(node) in kept]
    defines: dict[str, tuple[str, str, str]] = {}
    owners: dict[str, list[str]] = {}
    for node in subtree:
        flags = getattr(node, '_field_flags', {})
        node_path = _relative_path(node, root)
        try:
            hints = get_type_hints(type(node), include_extras=True)
        except Exception:
            hints = {}
        for f in fields(node):
            if not f.init or _is_runtime(hints.get(f.name)):
                continue
            flag = flags.get(f.name, {})
            if not flag.get('read', False) or flag.get('write', False):
                continue
            value = getattr(node, f.name, None)
            c_value = _c_value(value, f.metadata.get('format'))
            if c_value is None:
                continue
            field_path = f'{node_path}/{f.name}' if node_path else f.name
            name = config_define_name(field_path)
            owners.setdefault(name, []).append(field_path)
            if len(owners[name]) > 1:
                fields_text = ', '.join(owners[name])
                defines[name] = (name, None, f'Not defined: given by several fields ({fields_text})')
            else:
                defines[name] = (name, c_value, f.metadata.get('description', '') or '')
    return list(defines.values())


def generate_config_header(config: Config, nodes: Iterable[Config] | None = None,
        guard: str = 'TARGET_CONFIG_H') -> str:
    """Content of the header of the readable fields of *config* and below
    (restricted to *nodes* if given)."""
    defines = config_defines(config, nodes)
    width = max((len(name) for name, _, _ in defines), default=0) + 2
    lines = [
        '/* Generated from the config tree of the target, do not edit. */',
        '',
        f'#ifndef {guard}',
        f'#define {guard}',
        '',
    ]
    for name, value, desc in defines:
        if desc:
            comment = ' '.join(desc.split()).replace('*/', '* /')
            lines.append(f'/* {comment} */')
        if value is not None:
            lines.append(f'#define {name:<{width}}{value}')
    lines += ['', f'#endif /* {guard} */', '']
    return '\n'.join(lines)


def write_config_header(config: Config, path: str, nodes: Iterable[Config] | None = None,
        guard: str = 'TARGET_CONFIG_H') -> None:
    """Write the header to *path*, only if its content changed, so that the
    sources including it are not rebuilt for nothing."""
    content = generate_config_header(config, nodes, guard)
    try:
        with open(path, encoding='utf-8') as file:
            if file.read() == content:
                return
    except OSError:
        pass
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as file:
        file.write(content)
