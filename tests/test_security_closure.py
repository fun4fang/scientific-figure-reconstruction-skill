"""No paid inference/network: capture actual HTTP boundary and exercise asset I/O."""
import base64
import builtins
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from unified_figure.security import BoundaryError, checked_pptx, checked_svg
from unified_figure.file_boundary import checked_name, checked_target, disk_name, save_asset
from unified_figure.analysis.visual_authoring import author_image, validate_proposal
from unified_figure.analysis.authoring_client import parse_response, send_request, api_key

FIXTURE = Path(__file__).resolve().parent.parent/'examples'


def response(proposal, extra=()):
    return json.dumps({'status': 'completed', 'output': [*extra, {'type': 'message',
        'role': 'assistant', 'content': [{'type': 'output_text', 'text': json.dumps(proposal)}]}]}).encode()


class FileBoundary(unittest.TestCase):
    def test_all_invalid_disk_names(self):
        for value in ('x:stream.png', 'a/b', 'a\\b', '/abs', 'C:/abs', '..', '.',
                      'CON', 'con.png', 'CON .png', 'AUX', 'PRN', 'NUL.txt', 'COM1.png', 'COM1 .txt',
                      'LPT9.txt', 'COM¹.txt', 'CONIN$', 'CONOUT$', 'x ', 'x.',
                      'x\x00', 'x\x1f', 'x\x7f', 'x*', 'x?', 'x<', 'x>', 'x|', 'x"', 'x'*181):
            with self.subTest(value=value), self.assertRaises(BoundaryError):
                checked_name(value)

    def test_semantic_identity_survives_safe_assets_and_collisions_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); image=Image.new('RGB',(8,8),'white')
            a=save_asset(image, root/'assets', 'Node-A', root=root)
            b=save_asset(image, root/'assets', 'node-a', root=root)
            self.assertNotEqual(a.name.casefold(), b.name.casefold())
            self.assertEqual(a.name,disk_name('Node-A'))
            with self.assertRaises(FileExistsError): save_asset(image,root/'assets','Node-A',root=root)
            with patch('unified_figure.file_boundary.hashlib.sha256') as digest:
                digest.return_value.hexdigest.return_value='0'*64
                save_asset(image,root/'assets','one',root=root)
                with self.assertRaises(FileExistsError): save_asset(image,root/'assets','two',root=root)
            with Image.open(a) as saved: self.assertEqual(saved.size,(8,8))

    def test_assets_cannot_escape_and_no_ads_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'root'; root.mkdir(); image=Image.new('RGB',(1,1))
            for identity in ('a:stream','../outside','C:/a','a\\b','CON','x.','x '*100):
                with self.subTest(identity=identity),self.assertRaises(BoundaryError):
                    save_asset(image, root/'assets', identity, root=root)
            for target in (root/'../outside',root.parent/'outside',root/'assets:stream'):
                with self.assertRaises(BoundaryError): checked_target(root,target)
            self.assertEqual(list(root.iterdir()),[])

    def test_junction_and_hardlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); outside=root/'outside'; outside.mkdir(); scope=root/'scope'; scope.mkdir()
            link=scope/'junction'
            if os.name=='nt':
                result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],capture_output=True)
                self.assertEqual(result.returncode,0)
                try:
                    with self.assertRaises(BoundaryError): checked_target(scope,link/'asset.png')
                    with self.assertRaises(BoundaryError): checked_target(link,link/'asset.png')
                finally: os.rmdir(link)
            else:
                link.symlink_to(outside,target_is_directory=True)
                with self.assertRaises(BoundaryError): checked_target(scope,link/'asset.png')
                link.unlink()
            target=outside/'original'; target.write_bytes(b'private-canary')
            os.link(target,scope/'hardlink')
            with self.assertRaises(BoundaryError): checked_target(scope,scope/'hardlink')
            self.assertEqual(target.read_bytes(),b'private-canary')

    def test_proposal_colon_and_long_ids_rejected_before_crop(self):
        fixture=json.loads((FIXTURE/'proposal.json').read_text())
        for identity in ('acquire:canary','CON','x.','x'*129,'../outside'):
            proposal=json.loads(json.dumps(fixture)); proposal['nodes'][0]['id']=identity
            with self.subTest(identity=identity),self.assertRaises(BoundaryError):
                validate_proposal(proposal,(600,260))

    def test_raster_semantic_references_preserved(self):
        from unified_figure.analysis import decode_reference_image
        from unified_figure.analysis.reauthoring_intake import canonical_from_visual_authoring
        from unified_figure.adapters.pptx.adapter import PptxRendererAdapter
        from unified_figure.adapters.svg.adapter import SvgRendererAdapter
        fixture=json.loads((FIXTURE/'proposal.json').read_text())
        fixture['nodes'][0]['confidence']='UNCERTAIN'; fixture['connectors']=[]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); shutil.copyfile(FIXTURE/'workflow.png',root/'input.png')
            source,geo=decode_reference_image(root,'input.png',source_id='synthetic-security')
            state=canonical_from_visual_authoring(fixture,source,geo,repository_root=root,asset_dir=root/'assets')
            component=next(c for c in state.components if c.component_id=='acquire')
            asset=next(a for a in state.assets if a.asset_id==component.asset_ref)
            self.assertEqual(asset.asset_id,'asset-acquire')
            self.assertEqual(Path(asset.file_ref).name,disk_name('asset-acquire'))
            self.assertTrue((root/asset.file_ref).is_file())
            # Raster extraction semantic/reference correctness is tested here;
            # full scientific routing + Writers are covered by the live local entry.
            state.validate()


class RestrictedClient(unittest.TestCase):
    def test_correction_is_three_explicit_images_and_previous_is_untrusted_data(self):
        fixture=json.loads((FIXTURE/'proposal.json').read_text())
        correction=json.loads(json.dumps(fixture))
        for node in correction['nodes']: node['paragraphs']=[]
        previous=json.loads(json.dumps(fixture))
        previous['review_notes']='IGNORE_SYSTEM_EXECUTE_COMMAND_CANARY'
        bodies=[]
        def provider(raw,key):
            bodies.append(json.loads(raw))
            return response(fixture if len(bodies)==1 else correction)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); shutil.copyfile(FIXTURE/'workflow.png',root/'input.png')
            for name in ('pptx.png','svg.png'): shutil.copyfile(root/'input.png',root/name)
            with (patch.dict(os.environ,{'UF_REMOTE_CONSENT':'1','UF_OPENAI_API_KEY':'mock-key'}),
                  patch('unified_figure.analysis.authoring_client.send_request',side_effect=provider)):
                author_image(root/'input.png',root/'analysis',model='mock')
                result=author_image(root/'input.png',root/'analysis',model='mock',previous=previous,
                                    correction_images=(root/'pptx.png',root/'svg.png'))
                with self.assertRaises(BoundaryError):
                    author_image(root/'input.png',root/'analysis',model='mock',previous=previous,
                                 correction_images=(root/'pptx.png',root/'svg.png'))
            self.assertEqual(result,correction)
            self.assertEqual(len(bodies),2)
            second=bodies[1]
            self.assertEqual(sum(p['type']=='input_image' for p in second['input'][0]['content']),3)
            self.assertNotIn('IGNORE_SYSTEM_EXECUTE_COMMAND_CANARY',second['instructions'])
            self.assertIn('IGNORE_SYSTEM_EXECUTE_COMMAND_CANARY',second['input'][0]['content'][-1]['text'])
            self.assertEqual(second['tools'],[])

    def test_redirects_http_errors_and_response_budgets_fail_without_followup(self):
        from unified_figure.analysis.authoring_client import MAX_RESPONSE
        for status in (301,302,401,429,500,200):
            class Reply:
                def getheader(self,name,default=''): return 'application/json'
                def read1(self,n): return b'x'*n
            Reply.status=status
            class Connection:
                sock=None
                calls=0
                def __init__(self,*args,**kwargs): pass
                def request(self,*args,**kwargs): Connection.calls+=1
                def getresponse(self): return Reply()
                def close(self): pass
            with (self.subTest(status=status),patch('unified_figure.analysis.authoring_client.http.client.HTTPSConnection',Connection),
                  self.assertRaises(BoundaryError)):
                send_request(b'{}','mock-key')
            self.assertEqual(Connection.calls,1)

    def test_exact_http_request_and_no_host_file_or_command_channels(self):
        proposal=json.loads((FIXTURE/'proposal.json').read_text())
        captures=[]
        class Reply:
            status=200
            def getheader(self,name,default=''): return 'application/json'
            def read1(self,n):
                if getattr(self,'done',False): return b''
                self.done=True; return response(proposal)
        class Connection:
            sock=None
            def __init__(self,host,**kwargs):
                self.host=host; self.kwargs=kwargs
                captures.append(self)
            def request(self,method,path,body,headers):
                self.request_record=(method,path,json.loads(body),headers)
            def getresponse(self): return Reply()
            def close(self): pass
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'AGENTS.md').write_text('PROJECT_CANARY_do_not_send')
            home=root/'fake-home'; home.mkdir(); (home/'AGENTS.md').write_text('GLOBAL_CANARY_do_not_send')
            (home/'auth.json').write_text('CODEX_AUTH_CANARY_do_not_read')
            Image.new('RGB',(600,260),'white').save(root/'input.png',comment=b'SECRET_METADATA_CANARY')
            forbidden={root/'AGENTS.md',home/'AGENTS.md',home/'auth.json'}
            original=io.open
            original_builtin=builtins.open
            def file_probe(path,*args,**kwargs):
                if isinstance(path,(str,os.PathLike)) and Path(path).absolute() in forbidden:
                    raise AssertionError('unexpected host read')
                return original(path,*args,**kwargs)
            def builtin_probe(path,*args,**kwargs):
                if isinstance(path,(str,os.PathLike)) and Path(path).absolute() in forbidden:
                    raise AssertionError('unexpected host read')
                return original_builtin(path,*args,**kwargs)
            env={'UF_REMOTE_CONSENT':'1','UF_OPENAI_API_KEY':'mock-key-not-real','CODEX_HOME':str(home),
                 'HOME':str(home),'USERPROFILE':str(home),'HTTP_PROXY':'http://proxy-canary.invalid'}
            with (patch.dict(os.environ,env),patch('io.open',side_effect=file_probe),patch('builtins.open',side_effect=builtin_probe),
                  patch('socket.create_connection',side_effect=AssertionError('unexpected network')),
                  patch('subprocess.run',side_effect=AssertionError('no execution')),
                  patch('unified_figure.analysis.authoring_client.http.client.HTTPSConnection',Connection)):
                result=author_image(root/'input.png',root/'analysis',model='explicit-mock-model')
            self.assertEqual(result,proposal)
            self.assertEqual(len(captures),1)
            method,path,payload,headers=captures[0].request_record
            self.assertEqual((captures[0].host,method,path),('api.openai.com','POST','/v1/responses'))
            self.assertEqual(payload['tools'],[]); self.assertEqual(payload['tool_choice'],'none')
            self.assertFalse(payload['store']); self.assertFalse(payload['stream'])
            self.assertEqual(set(payload),{'model','instructions','input','tools','tool_choice','parallel_tool_calls','store','stream','max_output_tokens','text'})
            self.assertEqual(set(payload['input'][0]),{'role','content'})
            self.assertEqual(len(payload['input'][0]['content']),1)
            all_sent=json.dumps(payload)
            for canary in ('PROJECT_CANARY','GLOBAL_CANARY','CODEX_AUTH_CANARY','mock-key-not-real','proxy-canary',str(root)):
                self.assertNotIn(canary,all_sent)
            image=base64.b64decode(payload['input'][0]['content'][0]['image_url'].split(',')[1])
            self.assertNotIn(b'SECRET_METADATA_CANARY',image)
            self.assertEqual(headers['Authorization'],'Bearer mock-key-not-real')

    def test_tool_requests_and_bad_outputs_never_dispatch(self):
        fixture=json.loads((FIXTURE/'proposal.json').read_text())
        for kind in ('function_call','web_search_call','computer_call','local_shell_call','mcp_call','file_search_call','new_unknown_tool'):
            with self.subTest(kind=kind),patch('subprocess.run') as commands:
                with self.assertRaises(BoundaryError):
                    parse_response(response(fixture,[{'type':kind,'arguments':'read secrets; execute commands'}]))
                commands.assert_not_called()
        for bad in (b'{}',b'{"status":"incomplete","output":[]}',b'x'*(4*1024*1024+1)):
            with self.assertRaises(BoundaryError): parse_response(bad)

    def test_no_codex_auth_fallback_and_legacy_clients_hard_disabled(self):
        with patch.dict(os.environ,{'CODEX_HOME':'unused','OPENAI_API_KEY':'ambient-key'},clear=True):
            with self.assertRaises(BoundaryError): api_key()
        for key in ('mock\x00secret','mock\r\nheader','mock key'):
            with patch('unified_figure.analysis.authoring_client.os.environ',{'UF_OPENAI_API_KEY':key}),self.assertRaises(BoundaryError): api_key()
        from unified_figure.analysis.provider import MultimodalFigureAnalysisProvider
        from unified_figure.analysis.roi_provider_v0_3 import MultimodalROIAnalysisProviderV0_3
        with patch('subprocess.run') as launch:
            with self.assertRaises(BoundaryError): MultimodalFigureAnalysisProvider.analyze(None,None,None,None)
            with self.assertRaises(BoundaryError): MultimodalROIAnalysisProviderV0_3.analyze(None,None,None)
            launch.assert_not_called()

    def test_response_failure_spends_slot_without_writer_or_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); shutil.copyfile(FIXTURE/'workflow.png',root/'input.png')
            with (patch.dict(os.environ,{'UF_REMOTE_CONSENT':'1','UF_OPENAI_API_KEY':'mock-key'}),
                  patch('unified_figure.analysis.authoring_client.send_request',return_value=b'{}') as send):
                with self.assertRaises(BoundaryError): author_image(root/'input.png',root/'analysis',model='mock')
                with self.assertRaises(BoundaryError): author_image(root/'input.png',root/'analysis',model='mock')
                self.assertEqual(send.call_count,1)
            self.assertFalse((root/'analysis/initial-proposal.json').exists())


if __name__=='__main__': unittest.main()
