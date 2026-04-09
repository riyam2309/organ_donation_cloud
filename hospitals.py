from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Hospital:
    id: str
    name: str
    lat: float
    lon: float


# Hackathon demo registry (edit freely)
HOSPITALS: list[Hospital] = [
    Hospital(id="H001", name="City General Hospital", lat=19.0760, lon=72.8777),
    Hospital(id="H002", name="Metro Heart Institute", lat=28.6139, lon=77.2090),
    Hospital(id="H003", name="Riverbank Medical Center", lat=12.9716, lon=77.5946),
    Hospital(id="H004", name="Coastal Transplant Unit", lat=13.0827, lon=80.2707),
]


def get_hospital(hospital_id: str | None) -> Hospital | None:
    if not hospital_id:
        return None
    for h in HOSPITALS:
        if h.id == hospital_id:
            return h
    return None

