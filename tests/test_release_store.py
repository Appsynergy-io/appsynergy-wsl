import json
import hashlib
import os
import tempfile
import runpy
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch, Mock


class ReleaseStoreTests(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/promote'), run_name='test')
        self.Store = self.module['GitHubStore']
        self.globals = self.Store.find_release.__globals__
        self.tag = 'appsynergy-wsl-v0.1.0'
        self.source = 'a' * 40
        self.draft = {'id': 42, 'tag_name': self.tag, 'target_commitish': self.source,
                      'draft': True, 'assets': []}

    def test_draft_not_visible_by_tag_is_reused_by_id(self):
        with patch.object(self.Store, 'optional', return_value=None), \
             patch.dict(self.globals, api=lambda path: [self.draft]), \
             patch('subprocess.run') as execute:
            self.assertEqual(self.Store().ensure_release(self.tag, self.source, self.source), 42)
            execute.assert_not_called()

    def test_creation_uses_post_response_without_a_tag_lookup(self):
        calls = []
        def api(path):
            calls.append(path)
            self.assertIn('releases?per_page=100', path)
            return []
        response = subprocess.CompletedProcess([], 0, stdout=json.dumps(self.draft))
        with patch.object(self.Store, 'optional', return_value=None), patch.dict(self.globals, api=api), \
             patch('subprocess.run', return_value=response) as execute:
            self.assertEqual(self.Store().ensure_release(self.tag, self.source, self.source), 42)
            self.assertEqual(len(calls), 1)
            request = json.loads(execute.call_args.kwargs['input'])
            self.assertTrue(request['draft'])
            self.assertEqual(request['target_commitish'], self.source)

    def test_only_empty_unpublished_draft_can_be_retargeted(self):
        old = self.draft | {'target_commitish': 'b' * 40}
        response = subprocess.CompletedProcess([], 0, stdout=json.dumps(self.draft))
        with patch.object(self.Store, 'optional', return_value=None), \
             patch.dict(self.globals, api=lambda path: [old]), patch('subprocess.run', return_value=response) as execute:
            self.assertEqual(self.Store().ensure_release(self.tag, self.source, self.source), 42)
            self.assertIn('PATCH', execute.call_args.args[0])
            self.assertIn(self.source, json.loads(execute.call_args.kwargs['input'])['body'])
        for changed in [old | {'assets': [{'id': 1}]}, old | {'draft': False}]:
            with self.subTest(changed=changed), patch.object(self.Store, 'optional', return_value=None), \
                 patch.dict(self.globals, api=lambda path: [changed]), patch('subprocess.run') as execute:
                with self.assertRaisesRegex(ValueError, 'conflicting draft source'):
                    self.Store().ensure_release(self.tag, self.source, self.source)
                execute.assert_not_called()

    def test_upload_uses_stable_id_and_streams_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'manifest.json'
            path.write_bytes(b'fixture')
            response = Mock(status=201)
            response.read.return_value = json.dumps({'name': path.name, 'size': 7,
                'digest': 'sha256:' + hashlib.sha256(b'fixture').hexdigest()}).encode()
            connection = Mock()
            connection.getresponse.return_value = response
            sent = []
            def request(method, endpoint, body, headers):
                self.assertEqual(method, 'POST')
                self.assertIn('/releases/42/assets?', endpoint)
                self.assertEqual(headers['Content-Length'], '7')
                sent.append(body.read())
            connection.request.side_effect = request
            url = 'https://uploads.github.com/repos/Appsynergy-io/appsynergy-wsl/releases/42/assets{?name,label}'
            with patch.dict(self.globals, api=lambda endpoint: {'upload_url': url}), \
                 patch.dict(os.environ, {'GH_TOKEN': 'fixture-only'}), \
                 patch('http.client.HTTPSConnection', return_value=connection):
                self.Store().upload(42, path.name, path)
            self.assertEqual(sent, [b'fixture'])
            connection.close.assert_called_once()

    def test_empty_starter_recovery_never_deletes_completed_assets(self):
        asset = {'id': 99, 'name': 'file', 'state': 'starter', 'size': 0}
        with patch.object(self.Store, 'assets', return_value=[asset]), patch('subprocess.run') as execute:
            self.assertIsNone(self.Store().asset_hash(42, 'file'))
            self.assertIn('DELETE', execute.call_args.args[0])
        for change in [{'state': 'uploaded'}, {'size': 1}]:
            with patch.object(self.Store, 'assets', return_value=[asset | change]), patch('subprocess.run') as execute:
                with self.assertRaises(ValueError):
                    self.Store().asset_hash(42, 'file')
                execute.assert_not_called()

    def test_upload_never_sends_credentials_to_another_origin(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'file'
            path.write_bytes(b'fixture')
            with patch.dict(self.globals, api=lambda endpoint: {'upload_url': 'https://example.invalid/upload'}), \
                 patch('http.client.HTTPSConnection') as connect:
                with self.assertRaisesRegex(ValueError, 'upload origin'):
                    self.Store().upload(42, path.name, path)
                connect.assert_not_called()
