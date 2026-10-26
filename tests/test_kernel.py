import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class KernelContractTests(unittest.TestCase):
    def test_boot_integration_and_product_contract_cannot_silently_degrade_to_modules(self):
        contract = json.loads((ROOT / "kernel/wsl2-contract.json").read_text())
        lines = {f"CONFIG_{s}=y" for group in contract["required_builtin"].values() for s in group}
        lines |= {f"CONFIG_{s}=m" for s in contract["required_loadable"]}
        lines |= {f"CONFIG_{s}={v}" for s, v in contract["required_values"].items()}
        lines |= {f"# CONFIG_{s} is not set" for s in contract["forbidden"]}
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config"
            delta = Path(temp) / "delta"
            delta.write_text("")
            command = [sys.executable, str(ROOT / "kernel/verify-config"), str(config), str(delta), str(ROOT / "kernel/wsl2-contract.json")]
            config.write_text("\n".join(sorted(lines)))
            good = subprocess.run(command, capture_output=True)
            self.assertEqual(good.returncode, 0)
            self.assertEqual(json.loads(good.stdout)["windows_acceptance"], "NOT RUN")
            for symbol in ["HYPERV_STORAGE", "OVERLAY_FS", "VIRTIO_FS", "NET_9P_FD", "HYPERV_VSOCKETS", "VIRTIO_MMIO", "VIRTIO_NET", "HYPERV_KEYBOARD", "LTO_CLANG_THIN", "FORTIFY_SOURCE"]:
                broken = lines - {f"CONFIG_{symbol}=y"} | {f"CONFIG_{symbol}=m"}
                config.write_text("\n".join(sorted(broken)))
                bad = subprocess.run(command, capture_output=True)
                with self.subTest(symbol=symbol):
                    self.assertNotEqual(bad.returncode, 0)
                    self.assertIn(symbol.encode(), bad.stderr)
