"""Offline regression tests for Android release decisions and publication."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json

spec = importlib.util.spec_from_file_location('release_android', Path(__file__).with_name('release_android.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
META = dict(package_name='com.server.auditor.ssh.client', version_name='7.10.0', version_code='937')


def release(code=937, name='7.10.0', draft=False, assets=True):
    return dict(tag_name=f'android-v{name}-{code}', draft=draft, prerelease=False,
                assets=[dict(name=f'Termius_v{name}_zh_CN.apk', state='uploaded', size=100)] if assets else [])


class ReleaseTests(unittest.TestCase):
    def test_first_release(self):
        self.assertEqual(r.decision(META, [], False), (True, True))

    def test_same_code_different_name_skips(self):
        self.assertEqual(r.decision(META, [release(name='7.10')], False), (False, False))

    def test_force_is_artifact_only(self):
        for items in ([], [release()]):
            self.assertEqual(r.decision(META, items, True), (True, False))

    def test_older_code_skips(self):
        self.assertEqual(r.decision(META, [release(code=938)], False), (False, False))

    def test_draft_missing_asset_and_desktop_not_success(self):
        desktop = release(); desktop['tag_name'] = 'v9.44.0'
        self.assertEqual(r.published_versions([release(draft=True), release(assets=False), desktop]), [])

    def test_metadata_validation(self):
        for field, value in [('package_name', 'wrong'), ('version_code', 'oops'),
                             ('version_name', 'bad\nname')]:
            with self.assertRaises(ValueError):
                r.identity(dict(META, **{field: value}))

    def test_api_errors_propagate(self):
        with patch.object(r, 'gh', side_effect=RuntimeError('API error')), patch.dict(r.os.environ, GITHUB_REPOSITORY='test/repo'):
            with self.assertRaises(RuntimeError):
                r.releases()

    def test_publish_upload_failure_leaves_draft(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); out = root / 'out'; out.mkdir()
            (out / 'release-metadata.json').write_text(json.dumps(META))
            (out / 'Termius_v7.10.0_zh_CN.apk').write_bytes(b'fake test apk')
            with patch.object(r, '__file__', str(root / 'release_android.py')), \
                 patch.object(r, 'releases', return_value=[]), \
                 patch.dict(r.os.environ, GITHUB_REPOSITORY='test/repo', GITHUB_SHA='abc'), \
                 patch.object(r, 'gh', side_effect=['', RuntimeError('upload failed')]) as gh:
                with self.assertRaises(RuntimeError):
                    r.publish()
                self.assertEqual(gh.call_count, 2)
                self.assertIn('--draft', gh.call_args_list[0].args)
                self.assertEqual(gh.call_args_list[1].args[:2], ('release', 'upload'))

    def test_existing_release_never_uploaded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); out = root / 'out'; out.mkdir()
            (out / 'release-metadata.json').write_text(json.dumps(META))
            with patch.object(r, '__file__', str(root / 'release_android.py')), \
                 patch.object(r, 'releases', return_value=[release()]), patch.object(r, 'gh') as gh:
                r.publish()
                gh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
