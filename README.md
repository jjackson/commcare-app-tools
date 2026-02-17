# CommCare App Tools (`cc`)

## What is this?

CommCare App Tools is a proof of concept that makes it easier to work with CommCare from your computer. If you build apps on CommCare HQ, you normally do everything through the website -- looking up cases, checking mobile worker data, downloading apps, and so on. This project wraps all of those actions into a single command-line tool called `cc` so you can do them quickly from a terminal without clicking through the web interface.

Beyond just talking to CommCare HQ, this tool also lets you **test your forms locally**. It uses a piece of CommCare's own code (the commcare-cli) to run your app on your machine, fill out a form with test data you define, and check that the result is what you expected. You write a simple YAML file that says "open this form, answer these questions with these values" and the tool does the rest -- downloading the app, grabbing the right user data, running the form, and telling you if it passed or failed.

There is also a **web UI** you can launch with `cc web start`. It gives you a visual way to browse your domains, pick an app and user, and set up test configurations without needing to know any command-line syntax. Think of it as a helper that walks you through the setup, then hands off to the CLI to do the actual work.

This project is intended to support Windows, Linux, and macOS but was initially developed and tested on windows.

## Setup

### 1. Install

```bash
git clone https://github.com/dimagi/commcare-app-tools.git
cd commcare-app-tools
git submodule update --init  # Required for local form testing
python -m venv venv

# Activate: Linux/macOS
source venv/bin/activate
# Activate: Windows PowerShell
.\venv\Scripts\Activate.ps1

pip install -e ".[dev]"
```

### 2. Authenticate

```bash
cc auth login   # Opens browser for OAuth
cc auth status  # Verify authentication
```

### 3. Build CommCare CLI (for local form testing)

Requires Java 17+. On Windows with Android Studio:
```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr" #Example
```

```bash
cc cli build    # Downloads Gradle, builds JAR (~1-2 min first time)
cc cli status   # Verify build
```

## Usage

### Web UI (Recommended for Test Config Creation)

```bash
cc web start    # Opens browser at http://localhost:8080
```

The web UI provides:
- Wizard to select domain/app/user/case for testing
- Interactive terminal for running forms
- Workspace management for downloaded artifacts

### CLI Commands

**API Access:**
```bash
cc domain list                              # List your domains
cc --domain my-project app list             # List apps
cc --domain my-project case list --limit 10 # List cases
cc --domain my-project user list            # List mobile workers
cc api get /a/my-project/api/case/v2/       # Raw API request
```

**User Restores:**
```bash
cc --domain my-project user restore worker1                    # Check restore access
cc --domain my-project user restore worker1 --output-xml r.xml # Save restore XML
```

**Automated Form Testing:**
```bash
cc test init --output tests/my-test.yaml   # Generate a test template
cc test run tests/my-test.yaml             # Run a test end-to-end
cc test run tests/my-test.yaml --output-xml result.xml  # Save form output
```

**Local Form Testing (interactive):**
```bash
cc cli validate ./my-app.ccz               # Validate app
cc cli play ./my-app.ccz --demo            # Run with demo user
cc cli play ./my-app.ccz --restore data.xml # Run with restore file
```

**Workspace Management:**
```bash
cc workspace list                # List downloaded artifacts
cc workspace stats               # Show disk usage
cc workspace clean               # Remove all cached data
cc workspace path my-domain app1 # Show path for debugging
```

**Environment Management:**
```bash
cc env list                      # List environments (* = active)
cc env use staging               # Switch environment
cc --env production domain list  # One-off environment override
```

### Output Formats

All commands support `--format` and `--output`:
```bash
cc --format table domain list              # Rich table
cc --format csv --output cases.csv case list  # CSV file
```

## Commands Reference

```
# Authentication
cc auth login|logout|status|whoami

# Environments  
cc env list|add|use|remove

# Config
cc config set|get|show

# API Resources (use with --domain)
cc domain list
cc app list|get
cc case list|get
cc form list|get
cc user list|get|restore
cc lookup-table list|get|items
cc report list|data
cc api get|post

# Automated Form Testing
cc test init|run

# Local Form Testing (interactive)
cc cli build|status|clean|validate|play

# Web UI
cc web start|status

# Workspace
cc workspace list|stats|clean|path

# FormPlayer Docker (advanced)
cc formplayer start|stop|status|logs|restart|pull|destroy|connect|disconnect
```

### Global Options

| Flag | Env Var | Description |
|------|---------|-------------|
| `--env` | `CC_ENV` | Override active environment |
| `--domain` | `CC_DOMAIN` | CommCare domain (project space) |
| `--format` | `CC_FORMAT` | Output format: json, table, csv |
| `--output` | - | Write output to file |

## File Locations

| Path | Purpose |
|------|---------|
| `~/.commcare/config.json` | Environments, settings |
| `~/.commcare/credentials.json` | OAuth tokens (per-environment) |
| `~/.commcare/test-configs.json` | Saved test configurations |
| `~/.commcare/workspaces/` | Downloaded apps, restores, session data |
| `~/.commcare/cli/` | Cached commcare-cli.jar |

Override with `CC_CONFIG_DIR` environment variable.

## Development

```bash
pytest              # Run tests
ruff check src/     # Lint
ruff format src/    # Format

# Run web UI in dev mode (hot reload)
cc web start --reload
cd src/commcare_app_tools/web/frontend && npm run dev  # React dev server
```

## License

MIT - see [LICENSE](LICENSE).

## Testing

Run form tests with:

```bash
cc test run tests/beginner-test.yaml --output-xml result.xml
```
