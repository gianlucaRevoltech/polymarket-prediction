import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WebAssetTests(unittest.TestCase):
    def test_flask_renders_dashboard(self):
        import dashboard
        response = dashboard.app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Attivazione Paper", response.data)

    @unittest.skipUnless(shutil.which("node"), "Node unavailable")
    def test_javascript_compiles_and_renders_quality_states(self):
        result = subprocess.run([shutil.which("node"), str(ROOT / "tests/dashboard_smoke.cjs")],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shell_syntax(self):
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        bash = str(git_bash) if git_bash.exists() else shutil.which("bash")
        if not bash:
            self.skipTest("Bash unavailable")
        result = subprocess.run([bash, "-n", "start_all.sh"], cwd=ROOT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_start_uses_manifest_without_scan_results(self):
        import tempfile
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        bash = str(git_bash) if git_bash.exists() else shutil.which("bash")
        if not bash:
            self.skipTest("Bash unavailable")
        source = (ROOT / "start_all.sh").read_text()
        function = source.split("start_services() {", 1)[1].split("\n}\n", 1)[0]
        harness = '''
DATA_DIR=data; LOGS_DIR=logs; SCAN_RESULTS=data/scan_results.json; PORT=5000
ensure_venv() { :; }
venv_py() { echo true; }
stop_services() { :; }
show_status() { :; }
sleep() { :; }
run_wallet_scan() { echo UNEXPECTED_SCAN; return 2; }
'''
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "data").mkdir()
            (Path(tmp) / "data/run_manifest.json").write_text("{}")
            result = subprocess.run([bash, "-c", harness + "\nstart_services() {" + function + "\n}\nstart_services 0\nwait"],
                                    cwd=tmp, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("UNEXPECTED_SCAN", result.stdout)
