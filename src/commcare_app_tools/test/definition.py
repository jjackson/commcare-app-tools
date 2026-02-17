"""Test definition parsing and replay string generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


# Special action keywords used in the answers section
ACTION_SKIP = "SKIP"
ACTION_NEW_REPEAT = "NEW_REPEAT"


@dataclass
class TestDefinition:
    """A CommCare form test definition loaded from YAML.

    Attributes:
        name: Human-readable test name.
        domain: CommCare domain (project space).
        app_id: Application ID.
        username: Mobile worker username (without @domain).
        navigation: Ordered list of menu/entity selections to reach the form.
        answers: Dict mapping question XPath to answer value or action.
        timeout: Maximum seconds to wait for the CLI process.
    """

    name: str
    domain: str
    app_id: str
    username: str
    navigation: list[str] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)
    timeout: int = 120

    # --- Loading ---

    @classmethod
    def from_file(cls, path: str | Path) -> "TestDefinition":
        """Load a test definition from a YAML file.

        Args:
            path: Path to the YAML file.

        Returns:
            Parsed TestDefinition.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If required fields are missing.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Test file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Test file must be a YAML mapping, got {type(data).__name__}")

        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TestDefinition":
        """Create a TestDefinition from a dictionary.

        Args:
            data: Dictionary with test definition fields.

        Returns:
            Parsed TestDefinition.

        Raises:
            ValueError: If required fields are missing.
        """
        missing = [k for k in ("name", "domain", "app_id", "username") if not data.get(k)]
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")

        # Ensure navigation items are strings
        navigation = [str(item) for item in (data.get("navigation") or [])]

        # Ensure answer values are strings
        raw_answers = data.get("answers") or {}
        answers: dict[str, str] = {}
        for xpath, value in raw_answers.items():
            answers[str(xpath)] = str(value)

        return cls(
            name=data["name"],
            domain=data["domain"],
            app_id=data["app_id"],
            username=data["username"],
            navigation=navigation,
            answers=answers,
            timeout=int(data.get("timeout", 120)),
        )

    # --- Replay string generation ---

    @staticmethod
    def _ensure_indexed_xpath(xpath: str) -> str:
        """Ensure an XPath reference has a [1] index on the leaf node.

        The CommCare CLI replay mechanism expects indexed references like
        ``/data/first_name[1]`` rather than ``/data/first_name``.
        If the xpath already ends with an index (e.g. ``[1]``), it is
        returned as-is.
        """
        import re
        if re.search(r"\[\d+\]$", xpath):
            return xpath
        return f"{xpath}[1]"

    def build_replay_string(self) -> str:
        """Convert the answers dict into a :replay session string.

        The format expected by XFormPlayer is:
            ((<xpath>[1]) (VALUE) (<answer>))
            ((<xpath>[1]) (SKIP))
            ((<xpath>[1]) (NEW_REPEAT))

        Note: XPath references must include a positional index (e.g. ``[1]``)
        to match the ``TreeReference.toString()`` format used internally by
        CommCare's ``FormEntrySession``.

        Returns:
            The replay session string (without the ':replay ' prefix).
        """
        parts: list[str] = []
        for xpath, value in self.answers.items():
            indexed = self._ensure_indexed_xpath(xpath)
            if value == ACTION_SKIP:
                parts.append(f"(({indexed}) (SKIP))")
            elif value == ACTION_NEW_REPEAT:
                parts.append(f"(({indexed}) (NEW_REPEAT))")
            else:
                parts.append(f"(({indexed}) (VALUE) ({value}))")
        return " ".join(parts)

    def build_stdin(self) -> str:
        """Build the complete stdin content to pipe to commcare-cli.jar.

        This combines:
        1. Navigation lines (menu/entity selections for ApplicationHost)
        2. An empty line to pass the "Form Start" screen
        3. A :replay line with the form answers (for XFormPlayer)
        4. Several empty lines to navigate through any remaining prompts
           (triggers, calculated fields, "Form End") to completion

        Returns:
            Multi-line string ready to pipe to the CLI process stdin.
        """
        lines: list[str] = []

        # Navigation steps (consumed by ApplicationHost)
        for step in self.navigation:
            lines.append(step)

        # Form answers via :replay (consumed by XFormPlayer)
        if self.answers:
            # Empty line to pass the "Form Start: Press Return to proceed" screen
            lines.append("")
            replay_string = self.build_replay_string()
            lines.append(f":replay {replay_string}")
            # Empty lines to navigate through remaining prompts and complete
            # the form (triggers, calculated fields, "Form End" screen).
            # Extra lines are harmless -- the CLI ignores input after exit.
            for _ in range(10):
                lines.append("")

        return "\n".join(lines) + "\n"

    # --- Overrides ---

    def with_overrides(
        self,
        domain: Optional[str] = None,
        env_name: Optional[str] = None,
    ) -> "TestDefinition":
        """Return a copy with optional field overrides.

        CLI flags can override values from the YAML file.

        Args:
            domain: Override domain if provided.
            env_name: Not stored on definition, but used by the runner.

        Returns:
            New TestDefinition with overrides applied.
        """
        return TestDefinition(
            name=self.name,
            domain=domain or self.domain,
            app_id=self.app_id,
            username=self.username,
            navigation=list(self.navigation),
            answers=dict(self.answers),
            timeout=self.timeout,
        )


# --- Skeleton generation ---

SKELETON_YAML = """\
# CommCare Test Definition
# Run with: cc test run <this-file.yaml>

# Test metadata
name: "My Test"

# CommCare project configuration
domain: my-project
app_id: your-app-id-here
username: mobile-worker-username

# Maximum time (seconds) to wait for the test to complete
timeout: 120

# Navigation steps to reach the form.
# These are the menu/entity selections in the CommCare app.
# Each entry is sent as a line of input to the CLI.
# Use the number corresponding to the menu item (1-indexed).
navigation:
  - "1"    # Select first menu item
  # - "2"  # Select sub-menu or entity, etc.

# Form answers keyed by question XPath reference.
# These are replayed using the :replay mechanism in commcare-cli,
# which matches answers by question reference (not position).
#
# Supported values:
#   - Any string/number value for text, integer, date, etc.
#   - A number for select questions (1-indexed option)
#   - SKIP       -- explicitly skip a question
#   - NEW_REPEAT -- add a new repeat group instance
#
answers:
  # /data/name: "Jane Doe"
  # /data/age: "32"
  # /data/gender: "1"
  # /data/village: "Kigali"
  # /data/consent: "1"
  # /data/repeat_group: NEW_REPEAT
  # /data/repeat_group/item: "first item"
  # /data/optional_field: SKIP
"""


def generate_skeleton() -> str:
    """Return a commented YAML skeleton for a test definition."""
    return SKELETON_YAML
