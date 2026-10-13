"""Fixed passive-artifact protocol; no model commands or desktop permission broker.

The user runs desktop-render independently in an ordinary Windows desktop.
Native applications remain trusted user processes, not an OS zero-read sandbox.
"""
import hashlib
import json
import os
from pathlib import Path

from .file_boundary import checked_target, write_json
from .security import BoundaryError, local_path, bounded_bytes, checked_image, checked_pptx, checked_svg

FILES = ('input.png', 'result/canonical.json', 'result/reconstructed.pptx', 'result/reconstructed.svg')


def digest(path):
    return hashlib.sha256(bounded_bytes(path, 32 * 1024 * 1024)).hexdigest()


def write_handoff(root):
    image = checked_image(local_path(root, 'input.png'))
    checked_pptx(local_path(root, 'result/reconstructed.pptx'))
    checked_svg(bounded_bytes(local_path(root, 'result/reconstructed.svg'), 32 * 1024 * 1024))
    write_json(root, Path(root)/'artifact-handoff.json', {
        'version': 1, 'size': list(image.size), 'accepted': False,
        'files': {name: digest(local_path(root, name)) for name in FILES},
    })


def read_handoff(root):
    data = json.loads(bounded_bytes(local_path(root, 'artifact-handoff.json'), 16384))
    if (not isinstance(data, dict) or set(data) != {'version', 'size', 'accepted', 'files'} or
            data['version'] != 1 or data['accepted'] is not False or
            not isinstance(data['files'], dict) or set(data['files']) != set(FILES)):
        raise BoundaryError('unsupported or non-fixed artifact handoff')
    for name in FILES:
        if data['files'][name] != digest(local_path(root, name)):
            raise BoundaryError('handoff artifact integrity mismatch')
    image = checked_image(local_path(root, 'input.png'))
    if data['size'] != list(image.size):
        raise BoundaryError('handoff image dimensions mismatch')
    from .model.figure import CanonicalFigure
    state = CanonicalFigure.from_dict(json.loads(bounded_bytes(local_path(root, 'result/canonical.json'), 2*1024*1024)))
    if (state.input_geometry.source_hash != 'sha256:' + data['files']['input.png'] or
            (state.input_geometry.decoded_width_px, state.input_geometry.decoded_height_px) != image.size):
        raise BoundaryError('handoff canonical/source identity mismatch')
    checked_pptx(local_path(root, 'result/reconstructed.pptx'))
    checked_svg(bounded_bytes(local_path(root, 'result/reconstructed.svg'), 32 * 1024 * 1024))
    return data


def interactive_desktop():
    """Fail closed unless current, non-elevated token has an interactive desktop."""
    if os.name != 'nt':
        raise BoundaryError('trusted desktop rendering verified on Windows only')
    import ctypes as C
    from ctypes import wintypes as W
    kernel=C.WinDLL('kernel32',use_last_error=True)
    user=C.WinDLL('user32',use_last_error=True)
    adv=C.WinDLL('advapi32',use_last_error=True)
    session=W.DWORD()
    if not kernel.ProcessIdToSessionId(os.getpid(),C.byref(session)) or session.value==0:
        raise BoundaryError('interactive Windows session required')
    user.OpenInputDesktop.argtypes=[W.DWORD,W.BOOL,W.DWORD]; user.OpenInputDesktop.restype=W.HANDLE
    user.CloseDesktop.argtypes=[W.HANDLE]
    desktop=user.OpenInputDesktop(0,False,1)  # DESKTOP_READOBJECTS, no switching.
    if not desktop:
        raise BoundaryError('normal input desktop unavailable; no elevation/fallback')
    user.CloseDesktop(desktop)
    kernel.GetCurrentProcess.restype=W.HANDLE
    adv.OpenProcessToken.argtypes=[W.HANDLE,W.DWORD,C.POINTER(W.HANDLE)]
    adv.GetTokenInformation.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.POINTER(W.DWORD)]
    kernel.CloseHandle.argtypes=[W.HANDLE]
    token=W.HANDLE(); elevated=W.DWORD(); length=W.DWORD()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(),8,C.byref(token)):
        raise BoundaryError('desktop token verification unavailable')
    try:
        if not adv.GetTokenInformation(token,20,C.byref(elevated),C.sizeof(elevated),C.byref(length)) or elevated.value:
            raise BoundaryError('elevated or unverifiable desktop token rejected')
    finally: kernel.CloseHandle(token)
    return {'session_id':session.value, 'elevated':False, 'input_desktop_accessible':True}


def render_handoff(root):
    data=read_handoff(root)
    desktop=interactive_desktop()
    from .visual import rasterize_pptx, rasterize_svg, build_comparison_artifacts
    width,height=data['size']
    receipts={}
    render_dir=checked_target(root,Path(root)/'desktop')
    render_dir.mkdir(exist_ok=False)
    for ext,renderer in (('pptx',rasterize_pptx),('svg',rasterize_svg)):
        source=local_path(root,'result/reconstructed.'+ext)
        target=checked_target(root,render_dir/('rendered_'+ext+'.png'))
        receipts[ext]=renderer(source,target,width_px=width,height_px=height).to_dict()
        build_comparison_artifacts(local_path(root,'input.png'),target,
            checked_target(root,render_dir/('comparison_'+ext)),width_px=width,height_px=height,label='ACTUAL '+ext.upper())
    if read_handoff(root)!=data:
        raise BoundaryError('inputs changed during native rendering')
    write_json(root,Path(root)/'desktop-receipts.json',{'desktop':desktop,'renders':receipts,
        'native_editing':'REQUIRES_SEPARATE_OFFICE_ACCEPTANCE','scientific_correctness':'REQUIRES_RENDER_REVIEW',
        'model_calls':0,'model_command_channel':False})
