"""Run a command, stream its output, and record its exit code in <exitfile> (used by core.spawn)."""
import os
import subprocess
import sys

exitfile, argv = sys.argv[1], sys.argv[2:]
code = 1
timeout = int(os.environ.get("JOBHUB_RUN_TIMEOUT", "2700"))  # default 45 minutes

try:
    proc = subprocess.run(argv, timeout=timeout)
    code = proc.returncode
except subprocess.TimeoutExpired:
    print(f"\n[launcher] RUN TIMED OUT after {timeout}s: {' '.join(argv[:3])}", flush=True)
    code = 124
except OSError as e:
    print(f"launcher: cannot start {argv[:1]}: {e}", flush=True)
    code = 127
except Exception as e:
    print(f"launcher: unexpected error {e}", flush=True)
    code = 1
finally:
    try:
        with open(exitfile, "w") as f:
            f.write(str(code))
    except Exception as e:
        print(f"launcher: failed to write exitfile {exitfile}: {e}", file=sys.stderr)

sys.exit(code)
