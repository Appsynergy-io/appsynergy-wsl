import copy
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wslbuild.candidate import REPOSITORY, release_candidate, validate_release_run
from wslbuild.evidence import ARTIFACTS


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.source = 'a' * 40
        self.run = {'workflow_id': 20, 'event': 'push', 'head_branch': 'main',
                    'head_sha': self.source, 'repository': {'full_name': REPOSITORY}}
        self.jobs = [{'name': name, 'status': 'completed', 'conclusion': 'success'} for name in
                     ('changes', 'gates', 'package-check', 'package-sign', 'kernel-build',
                      'rootfs-build', 'candidate-build', 'candidate-sign')]

    def test_unfinished_release_run_can_use_completed_builds(self):
        validate_release_run(self.run, {'id': 20}, self.jobs, self.source)

    def test_missing_failed_cancelled_or_skipped_gate_prevents_release(self):
        for name in ('gates', 'package-sign', 'kernel-build', 'rootfs-build', 'candidate-sign'):
            for status in ('failure', 'cancelled', 'skipped', None):
                jobs = copy.deepcopy(self.jobs)
                next(job for job in jobs if job['name'] == name)['conclusion'] = status
                with self.subTest(name=name, status=status), self.assertRaises(ValueError):
                    validate_release_run(self.run, {'id': 20}, jobs, self.source)
        with self.assertRaises(ValueError):
            validate_release_run(self.run, {'id': 20}, self.jobs[1:], self.source)
        with self.assertRaises(ValueError):
            validate_release_run(self.run, {'id': 20}, self.jobs + [self.jobs[0]], self.source)

    def test_pr_or_changed_source_cannot_publish(self):
        for key, value in [('event', 'pull_request'), ('head_branch', 'feature'),
                           ('workflow_id', 99), ('head_sha', 'b' * 40)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_release_run(self.run | {key: value}, {'id': 20}, self.jobs, self.source)

    def test_signed_artifacts_release_without_windows_report_and_reject_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            trust = root / 'windows/trust'
            trust.mkdir(parents=True)
            key = root / 'private.pem'
            subprocess.run(['openssl', 'genrsa', '-out', str(key), '3072'], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['openssl', 'rsa', '-in', str(key), '-pubout', '-out', str(trust / 'release.pem')],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            candidate = root / 'candidate'
            candidate.mkdir()
            for name in ARTIFACTS:
                (candidate / name).write_bytes(b'fixture')
            package = 'appsynergy-wsl-integration-0.1.0-1-any.pkg.tar.zst'
            for name in (package, package + '.sig', 'SHA256SUMS'):
                (candidate / name).write_bytes(b'fixture')
            (candidate / 'inventory.json').write_text(json.dumps({'integration': {'filename': package}}))
            manifest = {'schema': 1, 'source': self.source, 'inputs_sha256': 'b' * 64, 'version': '0.1.0',
                        'build': {'repository': REPOSITORY, 'event': 'push', 'ref': 'refs/heads/main',
                                  'run_id': 10, 'run_attempt': 1},
                        'artifacts': {name: {'size': (candidate / name).stat().st_size,
                                           'sha256': hashlib.sha256((candidate / name).read_bytes()).hexdigest()}
                                      for name in ARTIFACTS}}
            (candidate / 'manifest.json').write_text(json.dumps(manifest))
            subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(key), '-out',
                            str(candidate / 'manifest.sig'), str(candidate / 'manifest.json')], check=True)
            env = {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': REPOSITORY,
                   'GITHUB_SHA': self.source, 'GITHUB_RUN_ID': '10', 'GITHUB_RUN_ATTEMPT': '1'}
            def api(path):
                if '/jobs?' in path:
                    return {'total_count': len(self.jobs), 'jobs': self.jobs}
                return {'id': 20} if '/workflows/' in path else self.run
            with patch.dict(os.environ, env), patch('wslbuild.candidate.ROOT', root), \
                 patch('wslbuild.candidate.git', return_value=self.source), \
                 patch('wslbuild.candidate.candidate_version', return_value='0.1.0'), \
                 patch('wslbuild.candidate.input_hash', return_value='b' * 64), \
                 patch('wslbuild.candidate.api', side_effect=api):
                self.assertEqual(release_candidate(candidate)[1], manifest)
                (candidate / 'kernel').write_bytes(b'tampered')
                with self.assertRaises(ValueError):
                    release_candidate(candidate)
