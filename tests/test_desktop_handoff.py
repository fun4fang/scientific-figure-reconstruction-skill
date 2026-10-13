"""Fixed artifact channel, integrity and partial-state reporting, without Office."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unified_figure.desktop_handoff import FILES, digest, read_handoff
from unified_figure.cli import main


class DesktopHandoffTests(unittest.TestCase):
    def packet(self, root):
        for name in FILES:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
        data={'version':1,'size':[600,260],'accepted':False,'files':{name:digest(root/name) for name in FILES}}
        (root/'artifact-handoff.json').write_text(json.dumps(data))
        return data

    def test_command_file_cannot_be_handoff(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);data=self.packet(root)
            data['command']='powershell malicious';(root/'artifact-handoff.json').write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'non-fixed'):read_handoff(root)

    def test_input_paths_cannot_be_selected_by_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);data=self.packet(root)
            data['files']['../private.png']=data['files'].pop('input.png')
            (root/'artifact-handoff.json').write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'non-fixed'):read_handoff(root)

    def test_mutated_file_rejected_before_native_application(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.packet(root);(root/'input.png').write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'integrity'):read_handoff(root)

    def test_no_desktop_privilege_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('unified_figure.desktop_handoff.interactive_desktop',side_effect=ValueError('no desktop')):
                self.assertEqual(main(['desktop-render','--workspace',d,'--artifacts','packet','--out','output']),2)
            self.assertFalse((Path(d)/'output').exists())


if __name__=='__main__':unittest.main()
