#!/usr/bin/env python3
"""
Quick smoke test for the field status parser.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from field_status_bot import FieldStatusBot


def summarize_closed_fields(statuses):
    return {
        f"{park} {number}"
        for park, fields in statuses.items()
        for number, state in fields.items()
        if state == "closed"
    }


def run_samples():
    bot = FieldStatusBot()

    samples = [
        {
            "label": "All open",
            "content": "All fields are open today.",
            "expected_status": "open",
            "expected_closed": set(),
        },
        {
            "label": "Palmer closed",
            "content": "All fields at Palmer Park are closed due to weather.",
            "expected_status": "partial",
            "expected_closed": {f"Palmer {i}" for i in range(1, 11)},
        },
        {
            "label": "Palmer range",
            "content": "Palmer Soccer 7-10 closed. Dublin Soccer 1 open.",
            "expected_status": "partial",
            "expected_closed": {f"Palmer {i}" for i in range(7, 11)},
        },
        {
            "label": "Dublin list",
            "content": "Dublin Soccer 1, 2, 3 closed while all other fields are open.",
            "expected_status": "partial",
            "expected_closed": {"Dublin 1", "Dublin 2", "Dublin 3"},
        },
    ]

    passed = 0
    failed = 0

    for sample in samples:
        statuses = bot.parse_field_statuses(sample["content"])
        actual_status = bot.summarize_statuses(statuses)
        actual_closed = summarize_closed_fields(statuses)

        status_ok = actual_status == sample["expected_status"]
        closed_ok = actual_closed == sample["expected_closed"]
        ok = status_ok and closed_ok

        if ok:
            passed += 1
            result = "PASS"
        else:
            failed += 1
            result = "FAIL"

        print(f"{sample['label']}: {result}")
        print(f"  Content: {sample['content']}")
        print(f"  Expected: {sample['expected_status']}, {sorted(sample['expected_closed'])}")
        print(f"  Actual:   {actual_status}, {sorted(actual_closed)}")
        print()

    print("=" * 50)
    print(f"Results: {passed} passed, {failed} failed")


if __name__ == "__main__":
    run_samples()
