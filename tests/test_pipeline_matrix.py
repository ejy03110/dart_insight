"""Keep all native Chroma handles in the child for Windows-compatible cleanup."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PipelineMatrixTests(unittest.TestCase):
    def test_actual_zip_multicompany_gms_and_failure_recovery_in_real_db_graph(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('pipeline_matrix_worker.py')), temp],
                                    capture_output=True, text=True, encoding='utf-8', timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PIPELINE_MATRIX_PASSED', result.stdout)
