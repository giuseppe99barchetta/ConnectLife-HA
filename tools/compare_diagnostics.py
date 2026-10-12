"""Offline comparison of two anonymized native HA ConnectLife diagnostics.

Usage: py -3 tools/compare_diagnostics.py old.json new.json
The per-export device aliases are intentionally randomized: comparisons are
aggregated by type/feature, never falsely joined by the changing alias.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys


def _dataset(report: dict) -> dict:
    data = report.get("data", report)
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        raise ValueError("Not a ConnectLife diagnostic data object")
    return data


def summarize(report: dict) -> dict:
    data = _dataset(report)
    groups: dict[str, dict] = {}
    for device in data["devices"]:
        if not isinstance(device, dict):
            continue
        label = f'{device.get("type_code", "unknown")}/{device.get("feature_code", "unknown")}'
        group = groups.setdefault(label, {"count": 0, "online": 0, "powered_on": 0,
                                          "modes_when_on": Counter(), "present_fields": Counter(),
                                          "temperatures_when_off": []})
        status = device.get("status") or {}
        if not isinstance(status, dict):
            status = {}
        group["count"] += 1
        group["online"] += int(device.get("online") is True)
        group["present_fields"].update(status.keys())
        powered = str(status.get("t_power")) == "1"
        if powered:
            group["powered_on"] += 1
            group["modes_when_on"][str(status.get("t_work_mode", "unknown"))] += 1
        elif status.get("f_temp_in") is not None:
            group["temperatures_when_off"].append(status["f_temp_in"])
    for group in groups.values():
        group["modes_when_on"] = dict(group["modes_when_on"])
        group["present_fields"] = dict(group["present_fields"])
    return {"captured_at": data.get("captured_at"),
            "rest_at": (data.get("freshness") or {}).get("last_rest_success_at"),
            "ws_at": (data.get("freshness") or {}).get("last_ws_event_at"),
            "ws_events": len(data.get("websocket_events") or []),
            "groups": groups}


def compare(old: dict, new: dict) -> dict:
    before, after = summarize(old), summarize(new)
    changed = {}
    for label in sorted(set(before["groups"]) | set(after["groups"])):
        first, second = before["groups"].get(label), after["groups"].get(label)
        if first != second:
            changed[label] = {"before": first, "after": second}
    return {
        "warning": "Device aliases are randomly regenerated per download; this is a grouped comparison, not per-unit matching or proof of per-key freshness.",
        "capture_before": {k: before[k] for k in ("captured_at", "rest_at", "ws_at", "ws_events")},
        "capture_after": {k: after[k] for k in ("captured_at", "rest_at", "ws_at", "ws_events")},
        "changed_groups": changed,
    }


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: compare_diagnostics.py old.json new.json")
    with Path(sys.argv[1]).open(encoding="utf-8") as stream:
        old = json.load(stream)
    with Path(sys.argv[2]).open(encoding="utf-8") as stream:
        new = json.load(stream)
    print(json.dumps(compare(old, new), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
