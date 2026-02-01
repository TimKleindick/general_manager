import os
import sys
from pathlib import Path

import django


def _find_project_root(start: Path) -> Path:
    for parent in (start, *start.parents):
        if (parent / "website").is_dir():
            return parent
    raise RuntimeError(
        f"Unable to locate 'website' directory searching from {start!s}"
    )


project_root = _find_project_root(Path(__file__).resolve())
website_path = project_root / "website"
sys.path.insert(0, str(website_path))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings")

django.setup()

from testApp.prototype import *
import time

if __name__ == "__main__":
    # a = Project(31964)
    # b = ProjectCommercial(a, date=date(2024, 1, 1))
    total_time_start = time.perf_counter()
    for i in range(20):
        t1 = time.perf_counter()
        c = ProjectCommercial.all()
        for y in c:
            dict(y)
        t3 = time.perf_counter()
        print("time", i, t3 - t1)
    total_time_end = time.perf_counter()
    print("total time", total_time_end - total_time_start)
    print()
