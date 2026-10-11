import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from verification_specialist import VerificationSpecialist, CheckResult

class TruthBoundaryTests(unittest.TestCase):
    def make(self, **kw):
        return VerificationSpecialist(**kw)

    def verdict(self, report):
        value = report.verdict
        return value.value if hasattr(value, 'value') else value

    def test_missing_target_is_partial(self):
        v = self.make()
        checks = v.adversarial_probe_idempotency('create', {'id': 1})
        self.assertIsNone(checks[0].passed)
        self.assertEqual(self.verdict(v.finalize()), 'PARTIAL')

    def test_partial_reason_overrides_success(self):
        v = self.make()
        v.report.checks.append(CheckResult(check_name="build", command="build", output="", passed=True))
        v.report.partial_reasons.append('independent observation unavailable')
        v.report.final_verdict()
        self.assertEqual(self.verdict(v.report), 'PARTIAL')

    def test_negative_check_cannot_be_hidden(self):
        v = self.make()
        result = CheckResult(check_name="build", command="build", output="", passed=True)
        result.passed = False
        v.report.checks.append(result)
        v.report.final_verdict()
        self.assertEqual(self.verdict(v.report), 'FAIL')

    def test_identical_responses_do_not_prove_no_duplicate(self):
        state = []
        def create(target, params):
            state.append(params['id'])
            return {'status': 'ok'}
        v = self.make(target_adapter=create, state_observer=lambda: len(state),
            probe_oracle=lambda kind, params, before, responses, after: after - before == 1)
        self.assertFalse(v.adversarial_probe_idempotency('create', {'id': 1})[0].passed)
        self.assertEqual(self.verdict(v.finalize()), 'FAIL')

    def test_independent_state_can_confirm_idempotency_and_concurrency(self):
        state = set()
        lock = threading.Lock()
        def create(target, params):
            with lock:
                state.add(params['id'])
            return {'status': 'ok'}
        v = self.make(target_adapter=create, state_observer=lambda: len(state),
            probe_oracle=lambda kind, params, before, responses, after: after == 1)
        self.assertTrue(v.adversarial_probe_idempotency('create', {'id': 1})[0].passed)
        self.assertTrue(v.adversarial_probe_concurrency('create', {'id': 1}, 8)[0].passed)
        self.assertEqual(self.verdict(v.finalize()), 'PASS')

    def test_unreachable_target_is_unknown(self):
        def unreachable(*args):
            raise ConnectionError()
        v = self.make(target_adapter=unreachable, state_observer=lambda: 0,
                      probe_oracle=lambda *args: True)
        self.assertIsNone(v.adversarial_probe_orphan_operation('delete', {'id': 1}).passed)
        self.assertEqual(self.verdict(v.finalize()), 'PARTIAL')

    def test_non_boolean_oracle_is_unknown(self):
        v = self.make(target_adapter=lambda *a: {'status': 'ok'}, state_observer=lambda: 0,
                      probe_oracle=lambda *a: 'probably')
        self.assertIsNone(v.adversarial_probe_idempotency('create', {'id': 1})[0].passed)
        self.assertEqual(self.verdict(v.finalize()), 'PARTIAL')

    def test_missing_command_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            v = self.make()
            self.assertIsNone(v.verify_tests(d).passed)
            self.assertEqual(self.verdict(v.finalize()), 'PARTIAL')

    def test_real_failed_command_is_fail(self):
        with tempfile.TemporaryDirectory() as d:
            v = self.make()
            self.assertFalse(v.verify_tests(d, 'python -c "raise SystemExit(3)"').passed)
            self.assertEqual(self.verdict(v.finalize()), 'FAIL')
