"""Local page authorization regression; no browser or service is launched."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import music


class MusicPageTests(unittest.TestCase):
    def test_credential_uses_fragment_and_is_not_printed(self):
        with TemporaryDirectory() as temp,patch.object(music,'DIR',Path(temp)):
            (Path(temp)/'bridge.token').write_text('example-local-token\n',encoding='ascii')
            with patch.object(music.webbrowser,'open',return_value=True) as browser, \
                 redirect_stdout(StringIO()) as output:
                music.open_management_page()
            browser.assert_called_once_with('http://127.0.0.1:8007/#token=example-local-token')
            self.assertEqual(output.getvalue(),'')

    def test_missing_or_empty_token_does_not_open_unauthorized_page(self):
        with TemporaryDirectory() as temp,patch.object(music,'DIR',Path(temp)):
            for content in (None,'  \n'):
                if content is not None:
                    (Path(temp)/'bridge.token').write_text(content,encoding='ascii')
                with patch.object(music.webbrowser,'open') as browser,self.assertRaises(RuntimeError):
                    music.open_management_page()
                browser.assert_not_called()


if __name__=='__main__':
    unittest.main()
