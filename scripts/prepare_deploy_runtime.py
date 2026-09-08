"""Install the versioned Swiss Ephemeris assets under their runtime names."""
from pathlib import Path
from shutil import copyfile

import jhora

root = Path(__file__).resolve().parents[1]
source = root / "skill-packs/v1/vedic-astrology/scripts/ephe"
targets = [source, Path(jhora.__file__).parent / "data/ephe"]
for target in targets:
    target.mkdir(parents=True, exist_ok=True)
    for asset in source.glob("*.se1.txt"):
        copyfile(asset, target / asset.name.removesuffix(".txt"))
print("Versioned ephemeris assets ready.")
