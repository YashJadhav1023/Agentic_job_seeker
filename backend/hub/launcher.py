"""Run a command, stream its output, and record its exit code in <exitfile> (used by core.spawn)."""
import subprocess, sys

exitfile, argv = sys.argv[1], sys.argv[2:]
try:
    code = subprocess.call(argv)
except OSError as e:
    print(f"launcher: cannot start {argv[:1]}: {e}", flush=True)
    code = 127
open(exitfile, "w").write(str(code))
sys.exit(code)
