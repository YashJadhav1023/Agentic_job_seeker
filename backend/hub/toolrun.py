"""Run one hub tool as a background run: python hub/toolrun.py <tool> '<json args>'.

Slow tools that work synchronously (send_hr_emails sleeps between mails) run through this so they get a log, a place in
the run queue and no HTTP timeout."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    import server

    name = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    res = server.HANDLERS[name](args)
    print("RESULT " + json.dumps(res, indent=2, default=str), flush=True)
    sys.exit(1 if isinstance(res, dict) and res.get("error") else 0)
