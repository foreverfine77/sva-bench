"""Persist an acquired trace with its requested and observed settings."""

import csv
from datetime import datetime, timezone
import json


def save_sweep(directory, trace, identity, setup, configured):
    metadata = {"identity": identity, "requested": setup.as_json(),
                "configured": configured, "captured_settings": trace.settings,
                "captured_at_utc": datetime.now(timezone.utc).isoformat()}
    (directory / "settings.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    with (directory / "spectrum.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow((trace.axis_name, "level_dbm"))
        writer.writerows(zip(trace.axis, trace.level_dbm))
