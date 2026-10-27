"""Runs the vendored LinkedIn Easy Apply bot non-interactively inside its own directory."""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "tools" / "multi-platform-job-apply-bot"
os.chdir(D)
sys.path.insert(0, str(D))
os.environ.setdefault("JOBHUB_LINKEDIN_PROFILE", str(ROOT / ".profiles" / "linkedin"))
import linkedin_bot  # noqa: E402
linkedin_bot.run_linkedin_bot()
