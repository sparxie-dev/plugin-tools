import copy
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_plugin import cross_check  # noqa: E402
from manifest_utils import is_allowed_url, validate_manifest  # noqa: E402
from validate_plugin import check_expectations, check_zip  # noqa: E402

GOOD = {
    "id": "ext.acme.demo", "displayName": "演示", "abiMajor": 1, "version": "1.2.3",
    "exeNames": ["Demo.exe"], "caps": ["createsProcess"],
}


def errors_with(**changes):
    data = copy.deepcopy(GOOD)
    data.update(changes)
    return validate_manifest(data)


class ManifestTests(unittest.TestCase):
    def test_minimal_manifest_passes(self):
        self.assertEqual([], validate_manifest(GOOD))

    def test_sample_repo_manifest_passes(self):
        sample = Path(__file__).parent / "sample_plugin_repo" / "plugin.json"
        self.assertEqual([], validate_manifest(json.loads(sample.read_text(encoding="utf-8"))))

    def test_rejections(self):
        cases = {
            "id": "Bad Id", "displayName": "", "abiMajor": 2, "version": "1.0", "exeNames": ["cmd.exe"], "caps": ["fly"],
            "icon": "..\\x.png", "backgroundUrl": "http://insecure", "registryHooks": {"keys": ["Software\\Microsoft\\Windows"]},
            "links": [{"title": "x", "url": "https://127.0.0.1/"}],
            "autoFind": {"registry": [{"hive": "HKCR", "key": "a", "value": "b"}]},
            "backgroundApi": {"url": "https://a.example/x", "path": "a", "body": "text"},
            "settingsSchema": {"fields": [{"key": "a", "label": "A", "type": "int", "default": 5, "min": 10, "max": 20}]},
        }
        for name, value in cases.items():
            with self.subTest(name):
                self.assertTrue(errors_with(**{name: value}), name)

    def test_registry_wildcard_rules(self):
        def lookup(key):
            return errors_with(autoFind={"registry": [{"hive": "HKCU", "key": key, "value": "install_path"}]})
        self.assertEqual([], lookup("Software\\Vendor\\Launcher\\*"))
        self.assertTrue(lookup("Software\\Vend*\\Launcher"))
        self.assertTrue(lookup("Software\\*\\*\\*"))

    def test_url_policy(self):
        self.assertTrue(is_allowed_url("https://cdn.example.com/a.png"))
        for url in ("http://cdn.example.com", "https://localhost/a", "https://10.0.0.1/a", "https://u:p@example.com/a"):
            self.assertFalse(is_allowed_url(url), url)

    def test_show_when_must_reference_earlier_field(self):
        fields = [{"key": "a", "label": "A", "type": "bool", "default": True, "showWhen": {"key": "b", "equals": True}},
                  {"key": "b", "label": "B", "type": "bool", "default": True}]
        self.assertTrue(errors_with(settingsSchema={"fields": fields}))

    def test_tag_must_match_version(self):
        errors = []
        check_expectations(GOOD, errors, None, "v9.9.9")
        self.assertEqual(1, len(errors))

    def test_cross_check_reports_every_mismatch(self):
        info = {"abiMajor": 1, "id": "ext.acme.other", "version": "0.0.1", "caps": 2, "exeNames": ["X.exe"]}
        self.assertEqual(4, len(cross_check(GOOD, info)))
        good = {"abiMajor": 1, "id": GOOD["id"], "version": GOOD["version"], "caps": 1, "exeNames": ["demo.EXE"]}
        self.assertEqual([], cross_check(GOOD, good))

    def test_disable_touch_ui_cap_is_accepted_and_mapped_to_bit_16(self):
        manifest = copy.deepcopy(GOOD)
        manifest["caps"] = ["createsProcess", "disableTouchUi"]
        self.assertEqual([], validate_manifest(manifest))
        info = {"abiMajor": 1, "id": manifest["id"], "version": manifest["version"], "caps": 17, "exeNames": ["Demo.exe"]}
        self.assertEqual([], cross_check(manifest, info))

    def test_zip_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            for layout, ok in (("ext.acme.demo/", True), ("wrong/", False), ("", False)):
                path = Path(temp) / "p.zip"
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(layout + "plugin.json", json.dumps(GOOD))
                    archive.writestr(layout + "module.dll", b"x")
                errors = []
                try:
                    check_zip(path, errors)
                except Exception as error:  # noqa: BLE001
                    errors.append(str(error))
                self.assertEqual(ok, not errors, f"{layout!r}: {errors}")


if __name__ == "__main__":
    unittest.main()
