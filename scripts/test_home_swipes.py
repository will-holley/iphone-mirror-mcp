#!/usr/bin/env python3
"""Opt-in live MCP regression. Requires a connected phone with two Home pages.

Navigates Home pages only. Checks page changes, return alignment, and a working
Home command after each set so an unfinished gesture cannot pass merely by
changing pixels. Python standard library only; does not require XCTest.
"""
import argparse
import base64
import json
from pathlib import Path
import re
import signal
import struct
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path(__file__).resolve().parents[1] / ".build/release/iphone-mirror-mcp")
    parser.add_argument("--evidence", type=Path, help="Optional local screenshot directory; contains personal Home Screen content")
    args = parser.parse_args()
    if args.evidence:
        args.evidence.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen([str(args.binary.resolve())], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True)
    serial = 0

    def timed_out(signum, frame):
        raise TimeoutError("Controller did not respond within 30 seconds")

    signal.signal(signal.SIGALRM, timed_out)

    def request(method, params):
        nonlocal serial
        serial += 1
        process.stdin.write(json.dumps(dict(jsonrpc="2.0", id=serial, method=method, params=params)) + "\n")
        process.stdin.flush()
        signal.alarm(30)
        for line in process.stdout:
            message = json.loads(line)
            if message.get("id") == serial:
                signal.alarm(0)
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message["result"]
        raise RuntimeError("Controller exited before responding")

    def call(name, **arguments):
        result = request("tools/call", dict(name=name, arguments=arguments))
        if result.get("isError"):
            raise RuntimeError(result)
        return result["content"]

    def screenshot(label):
        content = call("screenshot")
        png = base64.b64decode(next(item["data"] for item in content if item["type"] == "image"))
        if args.evidence:
            (args.evidence / f"{label}.png").write_bytes(png)
        return struct.unpack(">II", png[16:24])

    def landmarks(label, width, height):
        time.sleep(0.8)
        screenshot(label)
        text = "\n".join(item.get("text", "") for item in call("read_screen", fast=True))
        result = {}
        for line in text.splitlines():
            match = re.match(r'^"(.*)" — center=\((\d+), (\d+)\)', line)
            if match:
                word, x, y = match.groups()
                if len(word) >= 3 and any(c.isalpha() for c in word) and height * 0.14 < int(y) < height * 0.80:
                    result[word] = (int(x) / width, int(y) / height)
        return result

    def aligned_fraction(baseline, current):
        return sum(word in current and abs(x - current[word][0]) < 0.025
                   and abs(y - current[word][1]) < 0.02
                   for word, (x, y) in baseline.items()) / len(baseline)

    try:
        request("initialize", dict(protocolVersion="2025-06-18", capabilities={},
                                    clientInfo=dict(name="home-swipe-regression", version="1.0")))
        process.stdin.write(json.dumps(dict(jsonrpc="2.0", method="notifications/initialized")) + "\n")
        process.stdin.flush()
        call("home")
        time.sleep(0.5)
        call("home")
        width, height = screenshot("initial")
        baseline = landmarks("baseline", width, height)
        assert len(baseline) >= 8, "Need at least eight readable Home Screen landmarks"

        def swipe(left, duration):
            call("swipe", from_x=width * (0.85 if left else 0.15), from_y=height * 0.79,
                 to_x=width * (0.15 if left else 0.85), to_y=height * 0.79,
                 duration_ms=duration)

        for duration in (180, 350, 900):
            swipe(True, duration)
            page = landmarks(f"{duration}-left", width, height)
            overlap = len(baseline.keys() & page.keys()) / len(baseline)
            assert overlap < 0.60, f"Left swipe did not reveal another page: overlap={overlap:.2f}"
            swipe(False, duration)
            returned = landmarks(f"{duration}-right", width, height)
            aligned = aligned_fraction(baseline, returned)
            assert aligned >= 0.80, f"Right swipe did not settle at original positions: aligned={aligned:.2f}"
            swipe(True, duration)
            landmarks(f"{duration}-before-home", width, height)
            call("home")
            restored = landmarks(f"{duration}-home", width, height)
            home_aligned = aligned_fraction(baseline, restored)
            assert home_aligned >= 0.80, f"Home stopped responding after swipe: aligned={home_aligned:.2f}"
            print(f"PASS {duration}ms: changed page, reverse aligned={aligned:.0%}, Home aligned={home_aligned:.0%}", flush=True)
        print("PASS: 9 horizontal swipes and 3 post-swipe Home actions", flush=True)
    finally:
        signal.alarm(0)
        process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    main()
