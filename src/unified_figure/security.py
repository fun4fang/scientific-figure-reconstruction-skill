"""Conservative release boundaries; no scientific reconstruction decisions."""
from __future__ import annotations
import base64
import io
import json
from pathlib import Path
import re
import warnings
from xml.etree import ElementTree as ET
import zipfile

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 4_000_000
MAX_DIMENSION = 4096
MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
MAX_PROPOSAL_BYTES = 2 * 1024 * 1024
MAX_OBJECTS = 256
MAX_POINTS = 64


class BoundaryError(ValueError):
    pass


def local_path(root, relative, *, must_exist=True):
    from .file_boundary import checked_absolute, checked_name
    root = checked_absolute(root).resolve(strict=True)
    value = str(relative)
    if not value or '\\' in value or ':' in value or value.startswith('/'):
        raise BoundaryError('use a relative POSIX path within the selected root')
    parts = value.split('/')
    reserved = {'CON','PRN','AUX','NUL'} | {f'{name}{i}' for name in ('COM','LPT') for i in range(1,10)}
    if any(not part or part in ('.', '..') or part.endswith((' ', '.')) or
           part.split('.')[0].upper() in reserved or re.search(r'[<>"|?*\x00-\x1f]', part) for part in parts):
        raise BoundaryError('ambiguous or traversing path')
    path = root
    for part in parts:
        checked_name(part)
        path = path / part
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise BoundaryError('symlinks and junctions are not accepted')
    checked_absolute(path)
    resolved = path.resolve(strict=must_exist)
    if not resolved.is_relative_to(root) or resolved == root:
        raise BoundaryError('path escapes selected root')
    return resolved


def bounded_bytes(path, maximum):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise BoundaryError('missing file or file-size budget exceeded')
    with path.open('rb') as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise BoundaryError('file-size budget exceeded')
    return data


def checked_image(path):
    from PIL import Image
    data = bounded_bytes(path, MAX_IMAGE_BYTES)
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('PNG', 'JPEG') or getattr(image, 'n_frames', 1) != 1:
                raise BoundaryError('only single-frame PNG/JPEG inputs are supported')
            width, height = image.size
            if min(width, height) < 1 or max(width, height) > MAX_DIMENSION or width * height > MAX_PIXELS:
                raise BoundaryError('decoded image budget exceeded')
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            if image.getexif().get(274, 1) != 1:
                raise BoundaryError('normalize EXIF orientation before reconstruction')
            image.load()
            return image.convert('RGB')


def checked_proposal(data):
    if not isinstance(data, dict):
        raise BoundaryError('proposal must be an object')
    if len(json.dumps(data, ensure_ascii=False).encode('utf-8')) > MAX_PROPOSAL_BYTES:
        raise BoundaryError('proposal byte budget exceeded')
    for name in ('nodes','connectors','preserve_regions'):
        if not isinstance(data.get(name, []), list):
            raise BoundaryError('proposal collections must be arrays')
    objects = data.get('nodes', []) + data.get('connectors', []) + data.get('preserve_regions', [])
    if len(objects) > MAX_OBJECTS:
        raise BoundaryError('object budget exceeded')
    for obj in objects:
        if not isinstance(obj,dict): raise BoundaryError('proposal records must be objects')
        if len(obj.get('points', [])) > MAX_POINTS or len(obj.get('paragraphs', [])) > 32:
            raise BoundaryError('route or paragraph budget exceeded')
    def inspect(value, depth=0):
        if depth > 24:
            raise BoundaryError('proposal nesting budget exceeded')
        if isinstance(value, str) and len(value) > 32768:
            raise BoundaryError('text budget exceeded')
        if isinstance(value, dict):
            for child in value.values(): inspect(child, depth + 1)
        elif isinstance(value, list):
            if len(value) > 1024: raise BoundaryError('array budget exceeded')
            for child in value: inspect(child, depth + 1)
    inspect(data)
    return data


def checked_svg(data):
    if len(data) > MAX_DOCUMENT_BYTES:
        raise BoundaryError('SVG budget exceeded')
    # This strict subset accepts UTF-8 XML only, not DTDs/entities or PIs.
    text = data.decode('utf-8', errors='strict')
    if '\x00' in text or re.search(r'<!\s*(DOCTYPE|ENTITY)|<\?(?!xml\s)', text, re.I):
        raise BoundaryError('SVG declarations/processing instructions rejected')
    root = ET.fromstring(text)
    svg_ns = 'http://www.w3.org/2000/svg'
    if root.tag != '{' + svg_ns + '}svg':
        raise BoundaryError('SVG namespace/root required')
    allowed = {'svg','g','defs','marker','path','rect','ellipse','circle','line','polyline','polygon','text','tspan','title','desc','image'}
    count = 0
    pending = [(root, 0)]
    while pending:
        node, depth = pending.pop(); count += 1
        if depth > 32 or count > 5000:
            raise BoundaryError('SVG tree budget exceeded')
        if not isinstance(node.tag, str) or not node.tag.startswith('{' + svg_ns + '}') or node.tag.split('}',1)[1] not in allowed:
            raise BoundaryError('SVG element is outside the passive subset')
        for name, value in node.attrib.items():
            local = name.rsplit('}', 1)[-1].lower()
            if local.startswith('on') or local in ('style','base'):
                raise BoundaryError('SVG event/style/base attribute rejected')
            if name.startswith('{') and not name.startswith('{http://www.w3.org/1999/xlink}'):
                raise BoundaryError('SVG namespaced attribute rejected')
            if local == 'href':
                if node.tag.endswith('}image'):
                    match = re.fullmatch(r'data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)', value)
                    if not match: raise BoundaryError('SVG image must be embedded PNG/JPEG')
                    payload = base64.b64decode(match.group(2), validate=True)
                    if len(payload) > MAX_IMAGE_BYTES: raise BoundaryError('embedded raster budget exceeded')
                    from PIL import Image
                    with Image.open(io.BytesIO(payload)) as raster:
                        if raster.format not in ('PNG','JPEG') or raster.width*raster.height > MAX_PIXELS:
                            raise BoundaryError('embedded raster format/pixel budget exceeded')
                        raster.verify()
                elif not re.fullmatch(r'#[A-Za-z_][A-Za-z0-9_.:-]*', value):
                    raise BoundaryError('external SVG reference rejected')
            if re.search(r'url\s*\(', value, re.I) and not re.fullmatch(r'url\(#[A-Za-z_][A-Za-z0-9_.:-]*\)', value):
                raise BoundaryError('external or ambiguous SVG URL rejected')
        pending.extend((child, depth+1) for child in node)
    return root


def checked_pptx(path):
    bounded_bytes(path, MAX_DOCUMENT_BYTES)
    with zipfile.ZipFile(path) as package:
        entries = package.infolist()
        if len(entries) > 1024 or sum(e.file_size for e in entries) > 64*1024*1024:
            raise BoundaryError('PPTX expanded-size budget exceeded')
        names = [e.filename for e in entries]
        if len(names) != len(set(names)) or package.testzip() is not None:
            raise BoundaryError('PPTX duplicate/corrupt parts')
        for entry in entries:
            name = entry.filename
            if name.startswith('/') or '\\' in name or '..' in Path(name).parts:
                raise BoundaryError('PPTX traversal rejected')
            lowered = name.lower()
            if any(token in lowered for token in ('vbaproject', 'activex', 'embeddings/', 'externallinks/')):
                raise BoundaryError('PPTX active/embedded object rejected')
            if name.endswith(('.xml','.rels')):
                data = package.read(name)
                if re.search(br'<!\s*(DOCTYPE|ENTITY)', data, re.I):
                    raise BoundaryError('PPTX XML declaration rejected')
                xml = ET.fromstring(data)
                if any(n.attrib.get('TargetMode','').lower() == 'external' for n in xml.iter()):
                    raise BoundaryError('PPTX external relationship rejected')
                if lowered == '[content_types].xml' and b'macroenabled' in data.lower():
                    raise BoundaryError('macro content type rejected')
    return True


def limit_windows_job(process):
    """2 GiB aggregate job memory and descendant cleanup; Windows only.

    A pre-existing Office COM server is not a child and is not claimed bounded.
    Application-owned model/browser/Python child processes are bounded.
    """
    import ctypes as C
    from ctypes import wintypes as W
    class Basic(C.Structure):
        _fields_=[('process_time',C.c_longlong),('job_time',C.c_longlong),('flags',W.DWORD),
            ('min_ws',C.c_size_t),('max_ws',C.c_size_t),('active',W.DWORD),('affinity',C.c_size_t),
            ('priority',W.DWORD),('scheduling',W.DWORD)]
    class IO(C.Structure):
        _fields_=[(name,C.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
    class Extended(C.Structure):
        _fields_=[('basic',Basic),('io',IO),('process_memory',C.c_size_t),('job_memory',C.c_size_t),
            ('peak_process',C.c_size_t),('peak_job',C.c_size_t)]
    kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[C.c_void_p,W.LPCWSTR]; kernel.CreateJobObjectW.restype=W.HANDLE
    kernel.SetInformationJobObject.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD]; kernel.SetInformationJobObject.restype=W.BOOL
    kernel.AssignProcessToJobObject.argtypes=[W.HANDLE,W.HANDLE]; kernel.AssignProcessToJobObject.restype=W.BOOL
    kernel.CloseHandle.argtypes=[W.HANDLE]; kernel.CloseHandle.restype=W.BOOL
    handle=kernel.CreateJobObjectW(None,None)
    if not handle: raise BoundaryError('Windows job creation failed')
    limits=Extended(); limits.basic.flags=0x2000|0x200; limits.job_memory=2*1024**3
    if not kernel.SetInformationJobObject(handle,9,C.byref(limits),C.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle,W.HANDLE(int(process._handle))):
        kernel.CloseHandle(handle)
        raise BoundaryError('Windows memory/job containment unavailable; no unbounded fallback')
    return lambda: kernel.CloseHandle(handle)
