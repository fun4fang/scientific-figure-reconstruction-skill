import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image, PngImagePlugin

from unified_figure.host_authoring import prepare_packet
from unified_figure.analysis.visual_authoring import SCHEMA, RICH_SCHEMA, validate_proposal
from unified_figure.security import checked_image, BoundaryError


class HostHandoff(unittest.TestCase):
    def test_packet_is_offline_and_uses_existing_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            Image.new('RGB',(600,260)).save(root/'input.png')
            with (patch('socket.create_connection',side_effect=AssertionError('network')),
                  patch('subprocess.Popen',side_effect=AssertionError('nested host'))):
                prepare_packet(root)
            self.assertEqual(json.loads((root/'schema.json').read_text()),SCHEMA)
            self.assertEqual(json.loads((root/'rich-schema.json').read_text()),RICH_SCHEMA)
            report=json.loads((root/'report.json').read_text())
            self.assertFalse(report['network_or_model_request'])
            self.assertEqual(report['model_authoring'],'NOT_EXECUTED')
            self.assertEqual(report['proposal_target'],'proposal.json')
            self.assertFalse((root/'proposal.json').exists())
            proposal=json.loads((Path(__file__).resolve().parents[1]/'examples/proposal.json').read_text())
            validate_proposal(proposal,(600,260))
            proposal['nodes'][0]['id']='a:stream'
            with self.assertRaises(BoundaryError):validate_proposal(proposal,(600,260))

    def test_metadata_normalization_for_host_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);info=PngImagePlugin.PngInfo();info.add_text('Comment','private-canary')
            Image.new('RGB',(8,8)).save(root/'original.png',pnginfo=info)
            checked_image(root/'original.png').save(root/'input.png')
            prepare_packet(root)
            self.assertNotIn(b'private-canary',(root/'input.png').read_bytes())
            with Image.open(root/'input.png') as source:self.assertFalse(source.info)

    def test_existing_packet_refused_and_no_extra_host_document_read(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);Image.new('RGB',(8,8)).save(root/'input.png')
            (root/'AGENTS.md').write_text('not authoring data')
            original=Path.open
            def scoped_open(path,*args,**kwargs):
                if path.name=='AGENTS.md':raise AssertionError('implicit host document read')
                return original(path,*args,**kwargs)
            with patch.object(Path,'open',scoped_open):prepare_packet(root)
            with self.assertRaises((BoundaryError,OSError)):prepare_packet(root)


if __name__=='__main__':unittest.main()
