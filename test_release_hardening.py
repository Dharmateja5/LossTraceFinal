"""Release hardening regressions; no network, model calls, or production writes."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from gateway import RuntimeGateway


DOSSIER = 'd-11cd8b30545e'
RUNTIME = Path('data/application_runtime')


class GatewayRegisteredRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'manifests').mkdir(parents=True)
        dossier_dir = self.root / 'world' / DOSSIER / 'records'
        dossier_dir.mkdir(parents=True)
        source_manifest = RUNTIME / 'manifests' / f'{DOSSIER}.json'
        self.manifest_path = self.root / 'manifests' / source_manifest.name
        manifest = json.loads(source_manifest.read_text(encoding='utf-8'))
        # Only register one record in this isolated fixture.
        manifest['artifacts'] = [a for a in manifest['artifacts']
                                 if a['path'].endswith('records/claim.json')]
        self.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        self.record_path = dossier_dir / 'claim.json'
        shutil.copy2(RUNTIME / 'world' / DOSSIER / 'records' / 'claim.json', self.record_path)
        self.gateway = RuntimeGateway(str(self.root))

    def test_registered_record_succeeds(self):
        self.assertEqual(self.gateway.get_claim_record(DOSSIER)['claim_id'], 'CLM-26-876731')

    def test_modified_record_fails_closed(self):
        data = json.loads(self.record_path.read_text(encoding='utf-8'))
        data['claim_id'] = 'CLM-FAKE'
        self.record_path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.gateway.get_claim_record(DOSSIER)

    def test_unregistered_record_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'manifest'):
            self.gateway.get_policy_record(DOSSIER)

    def test_duplicate_manifest_entry_fails_closed(self):
        data = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        data['artifacts'].append(data['artifacts'][0])
        self.manifest_path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.gateway.get_claim_record(DOSSIER)

    def test_invalid_dossier_id_blocked(self):
        with self.assertRaisesRegex(ValueError, 'Invalid dossier'):
            self.gateway.get_claim_record('../etc/passwd')


if __name__ == '__main__':
    unittest.main()
