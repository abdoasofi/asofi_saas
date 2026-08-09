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

import frappe
from frappe.tests.utils import FrappeTestCase

from asofi_saas.asofi_saas.provisioning import drivers
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


class TestHandoverLeavesNoWizard(FrappeTestCase):
    """The first screen after signing up must not be a locked door.

    A school manager is created as a System User but holds no System Manager
    role. `bench new-site` writes "setup-wizard" into the desktop:home_page
    default, and only finishing the wizard moves it — so a site we marked
    setup_complete still sent that manager to the wizard, where Frappe
    answered "ليس لديك الأذونات الكافية".
    """

    def _commands(self):
        product = frappe._dict(
            name="_test_driver_product",
            bench_path="/tmp/bench_test",
            bench_executable="bench",
            apps_to_install="",
            secret_config_key="k",
            manager_role="Test Manager",
        )
        return drivers.BenchDriver(product).finalize_setup("acme.test")

    def test_the_wizard_is_marked_complete(self):
        joined = [" ".join(c) for c in self._commands()]
        self.assertTrue(
            any("setup_complete" in c and "System Settings" in c for c in joined),
            joined,
        )

    def test_the_desk_is_pointed_away_from_the_wizard(self):
        joined = " | ".join(" ".join(c) for c in self._commands())

        self.assertIn("desktop:home_page", joined)
        self.assertIn("workspace", joined)

    def test_every_command_targets_the_new_site(self):
        for cmd in self._commands():
            self.assertIn("--site", cmd)
            self.assertIn("acme.test", cmd)
