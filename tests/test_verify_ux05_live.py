"""Cross-platform live-probe lifecycle regression, including Windows file locks."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REAL_RUN=subprocess.run

SPEC=importlib.util.spec_from_file_location('live_probe',Path(__file__).resolve().parents[1]/'scripts/verify_ux05_live.py')
probe=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class LiveProbeCleanupTests(unittest.TestCase):
    def run_probe(self, worker, *, cleanup_error=False):
        self.directory=None
        def runner(command, **kwargs):
            self.directory=Path(command[command.index('--worker-directory')+1])
            code="""
import json, pathlib, sys
root=pathlib.Path(sys.argv[1])
"""+worker+"""
report={'status':'verified','timings':{'first_analysis_seconds':0.1,'repeat_analysis_seconds':0.01},'runs':[
 {'kind':'initially_unindexed','status':'passed'},
 {'kind':'already_indexed','status':'passed'}]}
(root/'result.json').write_text(json.dumps(report),encoding='utf-8')
"""
            return REAL_RUN([sys.executable,'-c',code,str(self.directory)],**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'report.json'
            try:
                with contextlib.ExitStack() as stack:
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    if cleanup_error:
                        stack.enter_context(patch.object(probe.shutil,'rmtree',side_effect=PermissionError('SECRET_PROVIDER_RESPONSE')))
                    stack.enter_context(patch.object(probe.subprocess,'run',side_effect=runner))
                    result=probe.run_isolated([],output)
                report=json.loads(output.read_text(encoding='utf-8'))
                return result,report,self.directory.exists()
            finally:
                if self.directory and self.directory.exists():
                    probe.shutil.rmtree(self.directory)

    def test_real_chroma_and_open_sqlite_handles_released_before_cleanup(self):
        result,report,exists=self.run_probe("""
import chromadb, sqlite3
client=chromadb.PersistentClient(path=str(root/'chroma'))
collection=client.get_or_create_collection('live_cleanup')
collection.add(ids=['original'],embeddings=[[1.0,0.0]],documents=['public fixture'])
connection=sqlite3.connect(root/'ledger.sqlite3')
connection.execute('create table test (id integer)')
# Intentionally retain native Chroma/SQLite handles until child process exit.
""")
        self.assertEqual(result,0)
        self.assertEqual(report['status'],'passed')
        self.assertFalse(exists)

    @unittest.skipUnless(os.name == 'nt','Windows file-delete locking semantics')
    def test_windows_open_file_prevents_delete_until_worker_exits(self):
        result,report,exists=self.run_probe("""
handle=(root/'locked.bin').open('wb')
try:
    (root/'locked.bin').unlink()
except PermissionError:
    pass
else:
    raise AssertionError('Windows file lock was not reproduced')
# Keep the handle open; the parent must clean up only after process exit.
""")
        self.assertEqual((result,report['status'],exists),(0,'passed',False))

    def test_cleanup_permission_error_keeps_both_passes_but_fails_final_status(self):
        result,report,exists=self.run_probe('',cleanup_error=True)
        self.assertEqual(result,1)
        self.assertEqual(report['status'],'failed')
        self.assertEqual(len(report['runs']),2)
        self.assertEqual(report['error_type'],'PermissionError')
        self.assertEqual(report['error_stage'],'temporary_directory_cleanup')
        self.assertNotIn('SECRET_PROVIDER_RESPONSE',json.dumps(report))
        self.assertTrue(exists)

    def test_nonzero_exit_cannot_promote_verified_report(self):
        def crash(command,**kwargs):
            root=Path(command[command.index('--worker-directory')+1])
            (root/'result.json').write_text(json.dumps({'status':'verified','runs':[]}),encoding='utf-8')
            return subprocess.CompletedProcess(command,1)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            output=Path(directory)/'report.json'
            with patch.object(probe.subprocess,'run',side_effect=crash):
                self.assertEqual(probe.run_isolated([],output),1)
            report=json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(report['error_stage'],'worker_exit')
            self.assertEqual(report['status'],'failed')

    def test_incomplete_checks_cannot_pass_even_with_zero_exit(self):
        def incomplete(command,**kwargs):
            root=Path(command[command.index('--worker-directory')+1])
            (root/'result.json').write_text(json.dumps({'status':'verified','runs':[]}),encoding='utf-8')
            return subprocess.CompletedProcess(command,0)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            output=Path(directory)/'report.json'
            with patch.object(probe.subprocess,'run',side_effect=incomplete):
                self.assertEqual(probe.run_isolated([],output),1)
            self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['status'],'failed')
