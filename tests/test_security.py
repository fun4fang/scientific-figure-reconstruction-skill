import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import shutil
import subprocess
import zipfile

from unified_figure.security import BoundaryError, checked_image, checked_proposal, checked_svg, checked_pptx, local_path
from unified_figure.analysis.visual_authoring import author_image, validate_proposal


class SecurityBoundaries(unittest.TestCase):
    def test_svg_rejects_active_or_external_content(self):
        attacks=[
            '<script>alert(1)</script>', '<foreignObject><div/></foreignObject>',
            '<rect onload="alert(1)"/>', '<rect style="fill:url(https://example.invalid/a)"/>',
            '<image href="file:///etc/passwd"/>', '<image href="https://example.invalid/a.png"/>',
            '<g xml:base="https://example.invalid/"/>', '<animate attributeName="x"/>',
            '<rect fill="url( https://example.invalid/a )"/>', '<use href="#x"/>',
            '<x:script xmlns:x="urn:evil"/>']
        for payload in attacks:
            with self.subTest(payload=payload), self.assertRaises(Exception):
                checked_svg(('<svg xmlns="http://www.w3.org/2000/svg">'+payload+'</svg>').encode())
        for payload in [b'<!DOCTYPE svg [<!ENTITY a "secret">]><svg/>',
                        b'<?xml-stylesheet href="file:///secret"?><svg/>',
                        '<svg/>'.encode('utf-16')]:
            with self.assertRaises(Exception): checked_svg(payload)

    def test_svg_passive_marker(self):
        checked_svg(b'<svg xmlns="http://www.w3.org/2000/svg"><defs><marker id="m"><path d="M 0 0 L 1 1"/></marker></defs><line marker-end="url(#m)"/></svg>')

    def test_scoped_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'input.png').write_bytes(b'x')
            self.assertEqual(local_path(root,'input.png'),root/'input.png')
            for value in ('../outside','/tmp/out','C:/out','a\\b','a/../b','a/./b','a//b','NUL','con.txt','a:stream','a.','x?'):
                with self.subTest(value=value),self.assertRaises(ValueError): local_path(root,value,must_exist=False)

    def test_image_resource_budget(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'large.png'; Image.new('RGB',(4097,1)).save(path)
            with self.assertRaises(ValueError): checked_image(path)
            Image.new('RGB',(8,8)).save(path); self.assertEqual(checked_image(path).size,(8,8))
            path.write_text('<svg/>')
            with self.assertRaises(Exception): checked_image(path)

    def test_proposal_budget(self):
        with self.assertRaises(ValueError): checked_proposal({'nodes':[{}]*257})
        with self.assertRaises(ValueError): checked_proposal({'nodes':[{'points':[{}]*65}]})
        with self.assertRaises(ValueError): checked_proposal({'review_notes':'x'*32769})

    def test_pptx_active_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.pptx'
            for name,data in [('ppt/vbaProject.bin',b'evil'),
                              ('ppt/embeddings/x.bin',b'evil'),
                              ('../outside',b'evil'),
                              ('ppt/_rels/x.rels',b'<Relationships><Relationship TargetMode="External" Target="https://example.invalid/"/></Relationships>')]:
                with zipfile.ZipFile(path,'w') as z: z.writestr(name,data)
                with self.subTest(name=name),self.assertRaises(ValueError): checked_pptx(path)

    def test_author_cannot_upload_without_consent(self):
        with patch.dict('os.environ',{},clear=True),patch('subprocess.run') as launch:
            with self.assertRaises(BoundaryError): author_image('unused.png','unused-output')
            launch.assert_not_called()

    def test_nonfinite_evidence(self):
        root=Path(__file__).resolve().parent.parent
        proposal=json.loads((root/'examples/proposal.json').read_text())
        proposal['nodes'][0]['bbox']['x']=float('nan')
        with self.assertRaises(ValueError): validate_proposal(proposal,(600,260))



if __name__=='__main__': unittest.main()
