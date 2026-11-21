#!/usr/bin/env python3
"""Demo flaky command: fails twice, then succeeds.

State lives in a counter file so each invocation remembers how many
times it has run. Delete the counter file to start the demo over:

    rm -f /tmp/retrier-demo-count
    retrier --times 5 --delay 200ms -- python demo/flaky.py
"""

import os
import sys

STATE = os.environ.get("RETRIER_DEMO_STATE", "/tmp/retrier-demo-count")
FAIL_TIMES = int(os.environ.get("RETRIER_DEMO_FAIL", "2"))

count = int(open(STATE).read()) if os.path.exists(STATE) else 0
count += 1
with open(STATE, "w") as fh:
    fh.write(str(count))

if count <= FAIL_TIMES:
    print("flaky.py: attempt %d — simulated failure (connection refused)" % count)
    sys.exit(1)

print("flaky.py: attempt %d — success!" % count)
sys.exit(0)
