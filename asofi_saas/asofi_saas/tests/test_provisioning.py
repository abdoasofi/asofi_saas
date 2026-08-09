"""اختبارات تنفيذ أوامر التهيئة.

One rule, learned the hard way: a provisioning command must never be able to
ask a question. The worker that runs it has inherited the terminal `bench
start` was launched from, so `isatty()` is True and a prompt looks answerable
— but nobody is reading that terminal. The job blocks forever, the visitor's
progress bar stops mid-way, and no error is ever recorded.
"""

import io
import subprocess
from unittest.mock import MagicMock, patch

from frappe.tests.utils import FrappeTestCase

from asofi_saas.asofi_saas.provisioning import provision as prov


def _proc(returncode=0, output=""):
    proc = MagicMock()
    proc.stdout = io.StringIO(output)
    proc.returncode = returncode
    return proc


class TestCommandsCannotBeAskedAQuestion(FrappeTestCase):
    def test_streaming_commands_run_with_no_stdin(self):
        """`bench new-site` prompts "do you want to rollback the site?" when
        creation fails, gated on isatty(). With a terminal inherited from
        `bench start`, that prompt hung provisioning at 30% indefinitely."""
        with patch.object(subprocess, "Popen", return_value=_proc()) as popen:
            prov._run_streaming(["bench", "new-site", "x"], "/tmp", "op", "new-site", None)

        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_plain_commands_run_with_no_stdin_too(self):
        """set-config and add-user are shorter, not safer: any of them can hit
        a confirmation and would block the same way."""
        result = MagicMock()
        result.returncode = 0

        with patch.object(subprocess, "run", return_value=result) as run:
            prov._run(["bench", "--site", "x", "set-config", "k", "v"], "/tmp", "op", "s", None)

        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_a_failing_command_raises_instead_of_waiting(self):
        """The failure must reach the caller. Marking the company Failed and
        showing the visitor an error is the whole point of not hanging."""
        failed = _proc(returncode=1, output="Traceback (most recent call last):\n")

        with patch.object(subprocess, "Popen", return_value=failed):
            with self.assertRaises(prov.ProvisionError):
                prov._run_streaming(["bench", "new-site", "x"], "/tmp", "op", "new-site", None)

    def test_the_failing_output_is_published_not_swallowed(self):
        """The traceback is the only description of what went wrong. It was
        being written into a pipe that nothing drained past the hang."""
        failed = _proc(returncode=1, output="MySQLdb.OperationalError: Access denied\n")

        with patch.object(subprocess, "Popen", return_value=failed):
            with patch.object(prov, "_publish") as published:
                with self.assertRaises(prov.ProvisionError):
                    prov._run_streaming(["bench", "new-site", "x"], "/tmp", "op", "new-site", None)

        messages = " ".join(str(c.args[3]) for c in published.call_args_list)
        self.assertIn("Access denied", messages)
