"""Shared release file policy. Semantic IDs never become literal disk paths.

Trusted single-user workspace; not an OS sandbox against hostile concurrent
directory replacement. All existing ancestors are checked before resolution.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import stat
import sys

from .security import BoundaryError

MAX_NAME = 180
MAX_PATH = 240
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {
    n + i for n in ('COM', 'LPT') for i in '123456789¹²³'}


def checked_name(value):
    if (not isinstance(value, str) or not value or len(value) > MAX_NAME or
            value in ('.', '..') or value.endswith((' ', '.')) or
            re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', value) or
            value.split('.')[0].rstrip(' ').upper() in RESERVED):
        raise BoundaryError('unsafe or ambiguous disk filename')
    return value


def checked_identity(value):
    # Preserve accepted IDs verbatim in all scientific records. Unsafe identities
    # fail closed, never get silently cleaned or merged.
    checked_name(value)
    if len(value) > 128 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
        raise BoundaryError('unsafe or overlong asset/element identity')
    return value


def disk_name(identity, *, kind='asset', extension='png'):
    checked_name(identity)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', identity):
        raise BoundaryError('unsafe asset identity')
    if kind not in ('asset', 'label-background') or extension != 'png':
        raise BoundaryError('unsupported generated asset type')
    # Full digest + exclusive create: no case-folding or sanitization collisions.
    return checked_name(kind + '-' + hashlib.sha256(identity.encode('utf-8')).hexdigest() + '.png')


def checked_absolute(path):
    raw = os.fspath(path)
    if not isinstance(raw, str) or '\x00' in raw:
        raise BoundaryError('invalid local path')
    original = Path(raw)
    if '..' in original.parts:
        raise BoundaryError('directory traversal rejected')
    absolute = Path(os.path.abspath(raw))
    if len(str(absolute)) > MAX_PATH or (os.name == 'nt' and str(absolute).startswith('\\\\')):
        raise BoundaryError('path length/UNC boundary exceeded')
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        checked_name(part)
        current /= part
        try:
            record = current.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(record.st_mode) or
                getattr(record, 'st_file_attributes', 0) & 0x400):
            raise BoundaryError('symlink/junction/reparse-point path rejected')
        if stat.S_ISREG(record.st_mode) and record.st_nlink > 1:
            raise BoundaryError('hard-linked file rejected')
    return absolute


def checked_target(root, target):
    root = checked_absolute(root)
    target = checked_absolute(target)
    if target == root or not target.is_relative_to(root):
        raise BoundaryError('output escapes authorized directory')
    return target


def make_directory(root, target, *, fresh=False):
    target = checked_target(root, target)
    target.mkdir(parents=True, exist_ok=not fresh)
    return checked_target(root, target)


@contextmanager
def new_file(root, target):
    target = checked_target(root, target)
    # Do not issue a redundant mkdir for the existing authorized root itself:
    # the write audit guard permits descendants, not mutations of the root.
    if not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    checked_target(root, target)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(target, flags, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        yield stream


def save_asset(image, directory, identity, *, root, kind='asset', **options):
    directory = checked_target(root, directory)
    target = checked_target(root, directory / disk_name(identity, kind=kind))
    with new_file(root, target) as stream:
        image.save(stream, format='PNG', **options)
    return target


def write_json(root, target, data):
    import json
    with new_file(root, target) as stream:
        stream.write((json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8'))


def install_write_guard(root):
    """Cover *all* Python writes in a release worker, including legacy serializers.

    This is defense in depth for trusted local code, not a Python-code sandbox.
    Model text is never evaluated; native renderers receive prechecked fixed paths.
    """
    root = checked_absolute(root)
    def guard(event, args):
        if event == 'open':
            path, mode, flags = args
            writing = ((isinstance(mode, str) and any(c in mode for c in 'wax+')) or
                       (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)))
            if writing and not isinstance(path, int):
                checked_target(root, path)
        elif event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod', 'os.utime', 'os.truncate'):
            checked_target(root, args[0])
        elif event in ('os.rename', 'os.link', 'os.symlink'):
            if event in ('os.link', 'os.symlink'):
                raise BoundaryError('link creation is forbidden in release jobs')
            checked_target(root, args[0]); checked_target(root, args[1])
    sys.addaudithook(guard)
