"""Test runner -- orchestrates setup, execution, and result capture."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import click

from ..api.client import CommCareAPI
from ..api.endpoints import USER_LIST
from ..commcare_cli.runner import CommCareCLIRunner
from ..config.environments import ConfigManager
from ..workspace.manager import WorkspaceManager

from .definition import TestDefinition


@dataclass
class TestResult:
    """Result of a test execution.

    Attributes:
        test_name: Name from the test definition.
        passed: Whether the form completed successfully.
        form_xml: Captured form XML output (if completed).
        stdout: Raw stdout from the CLI process.
        stderr: Raw stderr from the CLI process.
        exit_code: Process exit code.
        duration_seconds: How long the test took.
        error: Error message if the test failed.
    """

    test_name: str
    passed: bool
    form_xml: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration_seconds: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to a dictionary for JSON output."""
        result = {
            "test_name": self.test_name,
            "passed": self.passed,
            "exit_code": self.exit_code,
            "duration_seconds": round(self.duration_seconds, 2),
        }
        if self.error:
            result["error"] = self.error
        if self.form_xml:
            result["form_xml_size_bytes"] = len(self.form_xml.encode("utf-8"))
        return result


class TestRunner:
    """Orchestrates CommCare form test execution.

    Handles three phases:
    1. Setup -- download app CCZ and user restore if not already present.
    2. Execution -- pipe navigation + :replay input to commcare-cli.jar.
    3. Result -- parse output, determine pass/fail, extract form XML.
    """

    def __init__(
        self,
        config: ConfigManager,
        env_name: Optional[str] = None,
    ):
        self.config = config
        self.env_name = env_name
        self.workspace = WorkspaceManager()
        self.cli_runner = CommCareCLIRunner()

    # ------------------------------------------------------------------
    # Phase 1: Setup
    # ------------------------------------------------------------------

    def ensure_app_downloaded(self, definition: TestDefinition) -> Path:
        """Download the app CCZ if not already in the workspace.

        Args:
            definition: Test definition with domain and app_id.

        Returns:
            Path to the app CCZ file.

        Raises:
            RuntimeError: If the download fails.
        """
        domain = definition.domain
        app_id = definition.app_id

        ccz_path = self.workspace.get_app_ccz_path(domain, app_id)
        if ccz_path.exists():
            click.echo(f"  App CCZ already downloaded: {ccz_path}", err=True)
            return ccz_path

        click.echo(f"  Downloading app CCZ for {app_id}...", err=True)
        try:
            with CommCareAPI(self.config, domain=domain, env_name=self.env_name) as api:
                # Get app info
                response = api.get(f"api/application/v1/{app_id}/")
                response.raise_for_status()
                app_data = response.json()
                app_name = app_data.get("name", "Unknown")
                app_version = app_data.get("version")

                # Download CCZ (try release -> build -> save)
                ccz_url = "apps/api/download_ccz/"
                ccz_response = api.get(
                    ccz_url, params={"app_id": app_id, "latest": "release"}
                )
                if ccz_response.status_code == 404:
                    ccz_response = api.get(
                        ccz_url, params={"app_id": app_id, "latest": "build"}
                    )
                if ccz_response.status_code == 404:
                    ccz_response = api.get(
                        ccz_url, params={"app_id": app_id, "latest": "save"}
                    )
                ccz_response.raise_for_status()

                ccz_path = self.workspace.save_app_ccz(
                    domain=domain,
                    app_id=app_id,
                    ccz_content=ccz_response.content,
                    app_name=app_name,
                    version=app_version,
                )
                click.echo(f"  App downloaded: {ccz_path}", err=True)
                return ccz_path
        except Exception as e:
            raise RuntimeError(f"Failed to download app CCZ: {e}") from e

    def ensure_restore_downloaded(self, definition: TestDefinition) -> Path:
        """Download the user restore if not already in the workspace.

        Uses the 'login as' mechanism (?as= parameter) to get the
        restore for the specified mobile worker.

        Args:
            definition: Test definition with domain, app_id, username.

        Returns:
            Path to the restore XML file.

        Raises:
            RuntimeError: If the download fails.
        """
        domain = definition.domain
        app_id = definition.app_id
        username = definition.username

        # We need the user_id to store in the workspace. Look up the user first.
        try:
            with CommCareAPI(self.config, domain=domain, env_name=self.env_name) as api:
                # Look up user
                click.echo(f"  Looking up user '{username}'...", err=True)
                user_response = api.list(
                    USER_LIST, params={"username": username}, limit=1
                )
                users = user_response.get("objects", [])
                if not users:
                    raise RuntimeError(
                        f"User '{username}' not found in domain '{domain}'"
                    )

                user_data = users[0]
                user_id = user_data.get("id")
                full_username = user_data.get("username", username)

                # Check if already downloaded
                restore_path = self.workspace.get_restore_path(domain, app_id, user_id)
                if restore_path.exists():
                    click.echo(
                        f"  Restore already downloaded: {restore_path}", err=True
                    )
                    return restore_path

                # Download restore using ?as= parameter
                click.echo(
                    f"  Downloading restore for '{full_username}'...", err=True
                )
                restore_response = api.get(
                    "phone/restore/",
                    params={"version": "2.0", "as": full_username},
                )
                restore_response.raise_for_status()

                restore_path = self.workspace.save_restore(
                    domain=domain,
                    app_id=app_id,
                    user_id=user_id,
                    restore_content=restore_response.content,
                    username=full_username,
                )
                click.echo(f"  Restore downloaded: {restore_path}", err=True)
                return restore_path
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to download restore: {e}") from e

    # ------------------------------------------------------------------
    # Phase 2: Execution
    # ------------------------------------------------------------------

    def execute(
        self,
        definition: TestDefinition,
        ccz_path: Path,
        restore_path: Path,
    ) -> TestResult:
        """Execute the test by piping input to commcare-cli.jar.

        Args:
            definition: Test definition with navigation and answers.
            ccz_path: Path to the app CCZ file.
            restore_path: Path to the restore XML file.

        Returns:
            TestResult with pass/fail status and captured output.
        """
        stdin_input = definition.build_stdin()
        start_time = datetime.now()

        try:
            result = self.cli_runner.play_with_input(
                app_path=str(ccz_path),
                restore_file=str(restore_path),
                stdin_input=stdin_input,
                timeout=definition.timeout,
            )
            duration = (datetime.now() - start_time).total_seconds()

            return self._parse_result(
                definition=definition,
                process=result,
                duration=duration,
            )

        except subprocess.TimeoutExpired:
            duration = (datetime.now() - start_time).total_seconds()
            return TestResult(
                test_name=definition.name,
                passed=False,
                duration_seconds=duration,
                error=f"Test timed out after {definition.timeout} seconds",
            )
        except FileNotFoundError as e:
            duration = (datetime.now() - start_time).total_seconds()
            return TestResult(
                test_name=definition.name,
                passed=False,
                duration_seconds=duration,
                error=f"CLI not found: {e}. Run 'cc cli build' first.",
            )

    # ------------------------------------------------------------------
    # Phase 3: Result parsing
    # ------------------------------------------------------------------

    def _parse_result(
        self,
        definition: TestDefinition,
        process: subprocess.CompletedProcess,
        duration: float,
    ) -> TestResult:
        """Parse the CLI process output into a TestResult.

        Looks for completed form XML in stdout. The XFormPlayer prints
        the serialized form instance XML when a form completes.

        Args:
            definition: The test definition.
            process: Completed subprocess result.
            duration: Elapsed time in seconds.

        Returns:
            TestResult with parsed data.
        """
        stdout = process.stdout or ""
        stderr = process.stderr or ""

        # Try to extract form XML from stdout.
        # The completed form XML is an XML document that starts with <?xml
        # or <data or similar root element.
        form_xml = self._extract_form_xml(stdout)

        # Determine pass/fail
        if process.returncode != 0:
            return TestResult(
                test_name=definition.name,
                passed=False,
                form_xml=form_xml,
                stdout=stdout,
                stderr=stderr,
                exit_code=process.returncode,
                duration_seconds=duration,
                error=f"Process exited with code {process.returncode}",
            )

        if form_xml:
            return TestResult(
                test_name=definition.name,
                passed=True,
                form_xml=form_xml,
                stdout=stdout,
                stderr=stderr,
                exit_code=process.returncode,
                duration_seconds=duration,
            )

        # No form XML found -- might still be a success if the process
        # exited cleanly, but we can't confirm form completion.
        return TestResult(
            test_name=definition.name,
            passed=False,
            stdout=stdout,
            stderr=stderr,
            exit_code=process.returncode,
            duration_seconds=duration,
            error="Form XML not found in output. The form may not have completed.",
        )

    @staticmethod
    def _extract_form_xml(stdout: str) -> Optional[str]:
        """Extract completed form XML from CLI stdout.

        The XFormPlayer prints the form instance XML when a form completes.
        We look for XML content that looks like a form submission.

        Args:
            stdout: Raw stdout from the CLI process.

        Returns:
            The form XML string, or None if not found.
        """
        if not stdout:
            return None

        # Look for XML that starts with <?xml or a root element like <data
        # The form XML is typically printed as a contiguous block.
        # Use greedy match up to </data> to capture the full form XML.
        xml_pattern = re.compile(
            r"(<\?xml\s.*?\?>.*</data>|<data\s[^>]*>.*</data>)",
            re.DOTALL,
        )
        match = xml_pattern.search(stdout)
        if match:
            return match.group(0).strip()

        # Fallback: look for any substantial XML block
        # (lines that start with < and end with >)
        lines = stdout.split("\n")
        xml_lines: list[str] = []
        in_xml = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("<?xml") or (
                stripped.startswith("<") and not stripped.startswith("<!")
                and len(stripped) > 10 and not in_xml
            ):
                in_xml = True
            if in_xml:
                xml_lines.append(line)
                # Check if we've reached a closing root tag
                if stripped.startswith("</") and stripped.endswith(">"):
                    # Might be the end of the XML
                    candidate = "\n".join(xml_lines).strip()
                    if candidate.count("<") > 3:  # Looks like real XML
                        return candidate

        return None

    # ------------------------------------------------------------------
    # Convenience: run everything
    # ------------------------------------------------------------------

    def run_test(self, definition: TestDefinition) -> TestResult:
        """Run a complete test: setup, execute, and return results.

        Args:
            definition: The test definition to run.

        Returns:
            TestResult with pass/fail status and captured output.
        """
        click.echo(f"Running test: {definition.name}", err=True)
        click.echo("", err=True)

        # Phase 1: Setup
        click.echo("[1/3] Setup", err=True)
        try:
            ccz_path = self.ensure_app_downloaded(definition)
            restore_path = self.ensure_restore_downloaded(definition)
        except RuntimeError as e:
            return TestResult(
                test_name=definition.name,
                passed=False,
                error=f"Setup failed: {e}",
            )

        # Phase 2: Execute
        click.echo("", err=True)
        click.echo("[2/3] Executing test", err=True)
        click.echo(
            f"  Navigation steps: {len(definition.navigation)}", err=True
        )
        click.echo(f"  Form answers: {len(definition.answers)}", err=True)
        result = self.execute(definition, ccz_path, restore_path)

        # Phase 3: Report
        click.echo("", err=True)
        click.echo("[3/3] Results", err=True)
        if result.passed:
            click.echo(f"  PASSED ({result.duration_seconds:.1f}s)", err=True)
            if result.form_xml:
                xml_size = len(result.form_xml.encode("utf-8"))
                click.echo(f"  Form XML captured ({xml_size} bytes)", err=True)
        else:
            click.echo(f"  FAILED ({result.duration_seconds:.1f}s)", err=True)
            if result.error:
                click.echo(f"  Error: {result.error}", err=True)

        return result
