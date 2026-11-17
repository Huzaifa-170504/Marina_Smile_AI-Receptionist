from __future__ import annotations

import json

from app.operations.holiday_sync import sync_configured_clinic


if __name__ == "__main__":
    print(json.dumps(sync_configured_clinic(), indent=2, default=str))
