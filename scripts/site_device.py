#!/usr/bin/env python3
"""Keep a stable supervised PID for a site's foreground script or ROS launch."""
import argparse
import signal
import subprocess

def command(path, setup, arguments):
    # User paths are positional argv, never interpolated into shell source.
    program = 'set -e; setup="$1"; entry="$2"; shift 2; if [ -n "$setup" ]; then source "$setup"; fi; '
    if path.endswith(".launch"):
        program += 'exec roslaunch "$entry" "$@"'
    else:
        program += 'exec bash -- "$entry" "$@"'
    return ["/bin/bash", "-c", program, "site-device", setup, path, *arguments]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", choices=("arms", "cameras"), required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--setup", default="")
    parser.add_argument("--cwd", required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    child = subprocess.Popen(command(args.path, args.setup, arguments), cwd=args.cwd)
    interrupted = False
    def stopping(signum, frame):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass
    signal.signal(signal.SIGINT, stopping)
    signal.signal(signal.SIGTERM, stopping)
    code = child.wait()
    return 128 - code if code < 0 else code

if __name__ == "__main__":
    raise SystemExit(main())
