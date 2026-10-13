"""Local release entry; explicit remote consent, fresh bounded job folders."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .security import BoundaryError, bounded_bytes, checked_image, checked_proposal, checked_pptx, checked_svg, local_path, MAX_PROPOSAL_BYTES


def worker(job):
    from .file_boundary import checked_absolute, install_write_guard
    root = checked_absolute(job).resolve(strict=True)
    install_write_guard(root)
    spec = json.loads((root/'job.json').read_text(encoding='utf-8'))
    remote = spec['mode'] == 'author'
    if not remote:
        def network_guard(event, args):
            if event in ('socket.connect','socket.__new__'):
                raise BoundaryError('offline realization cannot use network')
        sys.addaudithook(network_guard)
    if spec['mode'] == 'prepare':
        from .host_authoring import prepare_packet
        prepare_packet(root)
        return
    if spec['mode'] == 'desktop-render':
        from .desktop_handoff import render_handoff
        render_handoff(root)
        (root/'report.json').write_text(json.dumps(dict(status='RENDERED_REQUIRES_REVIEW',
            mode='desktop-render',accepted=False,model_authoring='NOT_EXECUTED',
            scientific_correctness='REQUIRES_HUMAN_REVIEW',native_editing='NOT_VERIFIED'),indent=2),encoding='utf-8')
        return
    from .reauthoring import realize_proposal, reconstruct_image
    if remote:
        if os.environ.get('UF_REMOTE_CONSENT') != '1':
            raise BoundaryError('worker requires explicit parent remote consent')
        result = reconstruct_image(root/'input.png', root/'result', repository_root=root,
            correct_once=spec['correct_once'], model=spec['model'])
        folder = Path(result)
    else:
        data = checked_proposal(json.loads(bounded_bytes(root/'proposal.json', MAX_PROPOSAL_BYTES)))
        folder = root/'result'
        realize_proposal(root/'input.png', data, folder, repository_root=root,render_outputs=spec['mode']!='serialize')
    checked_svg(bounded_bytes(folder/'reconstructed.svg', 32*1024*1024))
    checked_pptx(folder/'reconstructed.pptx')
    if spec['mode'] == 'serialize':
        from .desktop_handoff import write_handoff
        write_handoff(root)
        (root/'report.json').write_text(json.dumps(dict(status='SERIALIZED_REQUIRES_DESKTOP_REVIEW',
            mode='serialize',accepted=False,model_authoring='NOT_EXECUTED',result='result',
            scientific_correctness='REQUIRES_HUMAN_REVIEW',renders=[]),indent=2),encoding='utf-8')
        return
    report = dict(status='PASS', mode=spec['mode'], model_authoring=('EXECUTED_REQUIRES_VISUAL_REVIEW' if remote else 'NOT_EXECUTED'),
        result=folder.relative_to(root).as_posix(), scientific_correctness='REQUIRES_HUMAN_REVIEW',
        internal_raster_editability=False, renders=['rendered_pptx.png','rendered_svg.png'])
    (root/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare','realize','serialize','author'):
        command=sub.add_parser(name)
        command.add_argument('--workspace', required=True, help='explicit trusted local root')
        command.add_argument('--image', required=True, help='relative PNG/JPEG under workspace')
        command.add_argument('--out', required=True, help='new relative directory under workspace')
        command.add_argument('--timeout', type=int, default=600, help='total job deadline in seconds, 30..600')
        if name in ('realize','serialize'): command.add_argument('--proposal', required=True, help='relative cached/reviewed proposal JSON')
        elif name=='author':
            command.add_argument('--allow-remote', action='store_true', help='explicit image upload and model usage consent')
            command.add_argument('--model', required=True, help='explicit vision/structured-output model available to your separate OpenAI API key')
            command.add_argument('--correct-once', action='store_true')
    desktop=sub.add_parser('desktop-render', help='user-run fixed artifact rendering in a normal Windows desktop; never elevates')
    desktop.add_argument('--workspace',required=True)
    desktop.add_argument('--artifacts',required=True,help='relative serialize job directory; no command file')
    desktop.add_argument('--out',required=True,help='fresh relative desktop verification directory')
    desktop.add_argument('--timeout',type=int,default=600)
    internal=sub.add_parser('_worker', help=argparse.SUPPRESS)
    internal.add_argument('job')
    check=sub.add_parser('check-artifact', help='reject active SVG or PPTX without rendering')
    check.add_argument('path')
    args=parser.parse_args(argv)
    try:
        if args.command=='_worker':
            worker(args.job); return 0
        if args.command=='check-artifact':
            path=Path(args.path)
            if path.suffix.lower()=='.svg': checked_svg(bounded_bytes(path,32*1024*1024))
            elif path.suffix.lower()=='.pptx': checked_pptx(path)
            else: raise BoundaryError('artifact must be SVG/PPTX')
            print('PASS'); return 0
        if not 30<=args.timeout<=600: raise BoundaryError('deadline must be 30..600 seconds')
        if args.command=='author' and not args.allow_remote:
            raise BoundaryError('author requires --allow-remote: image upload and account usage may incur cost')
        from .file_boundary import checked_absolute
        if args.command=="author":
            from .analysis.authoring_client import api_key, model_name
            api_key(); model_name(args.model)
        root=checked_absolute(args.workspace).resolve(strict=True)
        handoff=None
        if args.command=='desktop-render':
            from .desktop_handoff import read_handoff, interactive_desktop
            interactive_desktop()
            packet=local_path(root,args.artifacts)
            handoff=read_handoff(packet)
            image=local_path(packet,'input.png')
        else:
            image=local_path(root,args.image)
            if image.suffix.lower() not in ('.png','.jpg','.jpeg'): raise BoundaryError('PNG/JPEG image required')
        # Validate before creating a job. Re-encode decoded pixels to remove EXIF/
        # comments/author metadata; image-only byte identity is recorded separately.
        pixels=checked_image(image)
        proposal=None
        if args.command in ('realize','serialize'):
            proposal=checked_proposal(json.loads(bounded_bytes(local_path(root,args.proposal),MAX_PROPOSAL_BYTES)))
        output=local_path(root,args.out,must_exist=False)
        if output.exists(): raise BoundaryError('output exists; choose a new job directory')
        output.mkdir(parents=True,exist_ok=False)
        from .file_boundary import install_write_guard
        install_write_guard(output)
        if handoff is None:
            pixels.save(output/'input.png')
        else:
            from .desktop_handoff import FILES, read_handoff
            from .file_boundary import new_file
            for name in FILES+('artifact-handoff.json',):
                with new_file(output,output/name) as stream:
                    stream.write(bounded_bytes(local_path(packet,name),32*1024*1024))
            read_handoff(output)
        if proposal is not None:
            (output/'proposal.json').write_text(json.dumps(proposal,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        spec=dict(mode=args.command,model=getattr(args,'model',None),correct_once=getattr(args,'correct_once',False))
        (output/'job.json').write_text(json.dumps(spec),encoding='utf-8')
        env={key:value for key,value in os.environ.items() if key.upper() in
             {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','HOME','LOCALAPPDATA','APPDATA','UF_OPENAI_API_KEY'}}
        if args.command=='author': env['UF_REMOTE_CONSENT']='1'
        else:
            env.pop('UF_OPENAI_API_KEY',None)
        env['PYTHONDONTWRITEBYTECODE']='1'
        with (output/'worker.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,'-I','-B','-m','unified_figure.cli','_worker',str(output)],
                cwd=output,env=env,stdout=log,stderr=subprocess.STDOUT)
            close_job = lambda: None
            if os.name=='nt':
                from .security import limit_windows_job
                try: close_job=limit_windows_job(process)
                except BoundaryError:
                    process.kill(); process.wait(timeout=15); raise
            start=time.monotonic()
            while process.poll() is None:
                if time.monotonic()-start>args.timeout or (output/'worker.log').stat().st_size>16*1024*1024:
                    if os.name=='nt':
                        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=15,check=False)
                    else: process.kill()
                    process.wait(timeout=15)
                    close_job()
                    (output/'failure.json').write_text(json.dumps({'status':'FAIL_CLOSED','reason':'job deadline/log budget exceeded'}),encoding='utf-8')
                    raise BoundaryError('job deadline/log budget exceeded; partial artifacts are not accepted')
                time.sleep(.1)
            close_job()
        if process.returncode:
            (output/'failure.json').write_text(json.dumps({'status':'FAIL_CLOSED','reason':'validation/provider/render failure','exit_code':process.returncode}),encoding='utf-8')
            raise BoundaryError('job failed closed; inspect private worker.log and failure.json')
        status=('SERIALIZED_REQUIRES_DESKTOP_REVIEW' if args.command=='serialize' else
            'RENDERED_REQUIRES_REVIEW' if args.command=='desktop-render' else 'PASS')
        print(json.dumps({'status':status,'job':args.out,'report':'report.json'})); return 0
    except (ValueError,OSError) as exc:
        print('FAIL_CLOSED: '+str(exc),file=sys.stderr); return 2


if __name__=='__main__':
    raise SystemExit(main())
