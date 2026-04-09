from __future__ import annotations

from datetime import datetime
from typing import Optional
import math

from .models import BloodGroup, OrganType, Recipient, Donor, RhFactor
from .hospitals import get_hospital


def _abo_compatible_standard(donor: BloodGroup, recipient: BloodGroup) -> bool:
    """
    Standard solid-organ-style ABO compatibility.
    """
    compatible = {
        BloodGroup.O: {BloodGroup.O, BloodGroup.A, BloodGroup.B, BloodGroup.AB},
        BloodGroup.A: {BloodGroup.A, BloodGroup.AB},
        BloodGroup.B: {BloodGroup.B, BloodGroup.AB},
        BloodGroup.AB: {BloodGroup.AB},
    }
    return recipient in compatible[donor]


def _abo_exact(donor: BloodGroup, recipient: BloodGroup) -> bool:
    return donor == recipient


def _rh_compatible(donor: RhFactor, recipient: RhFactor) -> bool:
    """
    Standard RhD compatibility:
    - Rh negative can donate to Rh negative or positive
    - Rh positive can donate to Rh positive only
    """
    if donor == RhFactor.negative:
        return True
    return recipient == RhFactor.positive


def is_blood_compatible(
    donor: Donor,
    recipient: Recipient,
) -> bool:
    """
    Combined ABO + Rh compatibility, varying by organ type.

    - Heart / Lung: ABO *exact* match, Rh compatibility as above.
    - Kidney / Liver / Pancreas: standard ABO compatibility, Rh compatibility.
    - Cornea: no ABO or Rh restriction.
    """
    if donor.organ_type == OrganType.cornea:
        return True

    if donor.blood_group is None or recipient.blood_group is None:
        return False

    if donor.rh is None or recipient.rh is None:
        return False

    if donor.organ_type in {OrganType.heart, OrganType.lung}:
        abo_ok = _abo_exact(donor.blood_group, recipient.blood_group)
    else:
        abo_ok = _abo_compatible_standard(donor.blood_group, recipient.blood_group)

    rh_ok = _rh_compatible(donor.rh, recipient.rh)
    return abo_ok and rh_ok


def _safe_ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def estimate_transport_hours(
    donor_hospital_id: str | None,
    recipient_hospital_id: str | None,
    avg_speed_kmh: float = 600.0,
) -> Optional[float]:
    """
    Estimate transport hours using hospital coordinates.
    Adds a fixed 1.5h overhead (procurement + packaging + ground legs).
    """
    dh = get_hospital(donor_hospital_id)
    rh = get_hospital(recipient_hospital_id)
    if dh is None or rh is None:
        return None
    distance_km = haversine_km(dh.lat, dh.lon, rh.lat, rh.lon)
    return (distance_km / avg_speed_kmh) + 1.5


def cmv_penalty_points(donor_cmv: Optional[bool], recipient_cmv: Optional[bool], max_penalty: float = 10.0) -> float:
    """
    Return a penalty (0..max_penalty) for CMV mismatch risk.
    Highest risk is D+/R-.
    """
    if donor_cmv is None or recipient_cmv is None:
        return 0.0
    if donor_cmv and not recipient_cmv:
        return max_penalty
    if donor_cmv and recipient_cmv:
        return max_penalty * 0.4
    if (not donor_cmv) and (not recipient_cmv):
        return max_penalty * 0.2
    return 0.0


def compute_size_compatibility(
    donor_weight_kg: float,
    recipient_weight_kg: float,
    organ_type: OrganType,
) -> tuple[bool, float]:
    """
    Returns (is_acceptable, score_0_to_1) modeled after your shared baseline.
    """
    if organ_type == OrganType.cornea:
        return True, 1.0

    if recipient_weight_kg == 0:
        return True, 0.5

    if organ_type == OrganType.heart:
        ratio = abs(donor_weight_kg - recipient_weight_kg) / recipient_weight_kg
        if ratio > 0.30:
            return False, 0.0
        if ratio > 0.20:
            return True, 0.3
        return True, 1.0 - (ratio / 0.20)

    if organ_type == OrganType.liver:
        # Simplified GRWR based on donor weight approximation
        estimated_graft_weight = donor_weight_kg * 0.025
        grwr = estimated_graft_weight / recipient_weight_kg * 100
        if grwr < 0.8:
            return False, 0.0
        score = min((grwr - 0.8) / 1.2, 1.0)
        return True, score

    if organ_type == OrganType.kidney:
        ratio = abs(donor_weight_kg - recipient_weight_kg) / recipient_weight_kg
        score = max(1.0 - ratio * 0.5, 0.2)
        return True, score

    if organ_type == OrganType.lung:
        return True, 0.5

    if organ_type == OrganType.pancreas:
        return True, 0.8

    return True, 0.5


def score_heart(donor: Donor, recipient: Recipient, now: datetime) -> Optional[float]:
    """
    Returns a 0–100 score for heart allocation or None if any hard gate fails.
    """
    # Hard organ and blood rules
    if donor.organ_type != OrganType.heart or recipient.required_organ != OrganType.heart:
        return None
    if not is_blood_compatible(donor, recipient):
        return None

    # Viability window 4–6 hours since cross-clamp (we use donor.timestamp as cross-clamp)
    age_hours = (now - donor.timestamp).total_seconds() / 3600.0
    if age_hours > 6:
        return None

    # Body size compatibility (hard gate + graded match score)
    if donor.weight_kg is None or recipient.weight_kg is None:
        return None
    ok, size_score01 = compute_size_compatibility(donor.weight_kg, recipient.weight_kg, OrganType.heart)
    if not ok:
        return None

    # Recipient hemodynamic stability & status
    if recipient.heart_status not in {"1A", "1B", "2"}:
        return None
    if recipient.hemodynamically_stable is False:
        return None

    score = 0.0

    # Urgency (40 pts)
    status_points = {"1A": 40, "1B": 30, "2": 10}
    score += status_points.get(recipient.heart_status, 0)

    # Wait time (20 pts) — scale days up to 365
    days_waiting = (now - recipient.added_at).total_seconds() / 86400.0
    wait_points = max(0.0, min(20.0, (days_waiting / 365.0) * 20.0))
    score += wait_points

    # Size match (20 pts)
    score += 20.0 * max(0.0, min(1.0, size_score01))

    # Geographic / logistics (20 pts) via transport estimate.
    # Hard gate: transport must fit inside viability window (6h here).
    transport_h = estimate_transport_hours(donor.hospital_id, recipient.hospital_id)
    if transport_h is not None:
        if transport_h > 6:
            return None
        # Closer is better
        score += 20.0 * max(0.0, min(1.0, (6.0 - transport_h) / 6.0))

    return min(score, 100.0)


def score_liver(donor: Donor, recipient: Recipient, now: datetime) -> Optional[float]:
    if donor.organ_type != OrganType.liver or recipient.required_organ != OrganType.liver:
        return None

    if not is_blood_compatible(donor, recipient):
        return None

    # Viability 12–24 hours
    age_hours = (now - donor.timestamp).total_seconds() / 3600.0
    if age_hours > 24:
        return None

    # Size compatibility via GRWR approximation (hard gate + score)
    if donor.weight_kg is None or recipient.weight_kg is None:
        return None
    ok, size_score01 = compute_size_compatibility(donor.weight_kg, recipient.weight_kg, OrganType.liver)
    if not ok:
        return None

    # MELD/PELD threshold
    meld = recipient.meld_score
    peld = recipient.peld_score
    if meld is not None:
        if meld < 15:
            return None
    elif peld is not None:
        if peld < 10:
            return None
    else:
        return None

    score = 0.0

    # MELD/PELD (50 pts, linear 6–40)
    if meld is not None:
        score += (max(6, min(40, meld)) - 6) / (40 - 6) * 50.0
    else:
        score += (max(0, min(40, peld)) / 40.0) * 50.0

    # Wait time (20 pts)
    days_waiting = (now - recipient.added_at).total_seconds() / 86400.0
    wait_points = max(0.0, min(20.0, (days_waiting / 365.0) * 20.0))
    score += wait_points

    # Size compatibility (20 pts)
    score += 20.0 * max(0.0, min(1.0, size_score01))

    # Geographic proximity (10 pts) via transport time (<12h strongly preferred)
    transport_h = estimate_transport_hours(donor.hospital_id, recipient.hospital_id)
    if transport_h is not None:
        if transport_h <= 12:
            score += 10.0 * max(0.0, min(1.0, (12.0 - transport_h) / 12.0))

    return min(score, 100.0)


def score_kidney(donor: Donor, recipient: Recipient, now: datetime) -> Optional[float]:
    if donor.organ_type != OrganType.kidney or recipient.required_organ != OrganType.kidney:
        return None

    if not is_blood_compatible(donor, recipient):
        return None

    # HLA crossmatch negative (we model as hla_match_score present; if missing, reject)
    if recipient.hla_match_score is None:
        return None
    if recipient.crossmatch_negative is not True:
        return None

    # ESRD requirement
    if not (recipient.on_dialysis or (recipient.egfr is not None and recipient.egfr < 20)):
        return None

    score = 0.0

    # HLA match score (0–6) -> up to 35 pts
    hla = max(0, min(6, recipient.hla_match_score))
    score += (hla / 6.0) * 35.0

    # PRA / urgency (25 pts)
    if recipient.pra_percent is not None:
        pra = max(0, min(100, recipient.pra_percent))
        score += (pra / 100.0) * 25.0

    # Wait time (25 pts)
    days_waiting = (now - recipient.added_at).total_seconds() / 86400.0
    wait_points = max(0.0, min(25.0, (days_waiting / 365.0) * 25.0))
    score += wait_points

    # Cold ischaemia (15 pts) via time since cross-clamp
    age_hours = (now - donor.timestamp).total_seconds() / 3600.0
    if age_hours <= 24:
        score += 15.0

    return min(score, 100.0)


def score_lung(donor: Donor, recipient: Recipient, now: datetime) -> Optional[float]:
    if donor.organ_type != OrganType.lung or recipient.required_organ != OrganType.lung:
        return None

    if not is_blood_compatible(donor, recipient):
        return None

    # Viability 4–6 hours
    hours = (now - donor.timestamp).total_seconds() / 3600.0
    if hours > 6:
        return None

    # Chest cavity size ±10% using height as proxy
    if donor.height_cm is None or recipient.height_cm is None:
        return None
    ratio = _safe_ratio(donor.height_cm, recipient.height_cm)
    if ratio is None or not (0.9 <= ratio <= 1.1):
        return None

    # Smoking history <20 pack-years
    if donor.smoker_pack_years is not None and donor.smoker_pack_years >= 20:
        return None

    score = 0.0

    # LAS score (45 pts)
    if recipient.las_score is not None:
        las = max(0.0, min(100.0, recipient.las_score))
        score += (las / 100.0) * 45.0

    # Size compatibility (25 pts)
    score += 25.0

    # Geographic / logistics (20 pts) with transport estimate (hard gate for 6h viability)
    transport_h = estimate_transport_hours(donor.hospital_id, recipient.hospital_id)
    if transport_h is not None:
        if transport_h > 6:
            return None
        score += 20.0 * max(0.0, min(1.0, (6.0 - transport_h) / 6.0))

    # CMV match (10 pts) with penalty for D+/R-
    score += max(0.0, 10.0 - cmv_penalty_points(donor.cmv_positive, recipient.cmv_positive, max_penalty=10.0))

    return min(score, 100.0)


def score_pancreas(donor: Donor, recipient: Recipient, now: datetime) -> Optional[float]:
    if donor.organ_type != OrganType.pancreas or recipient.required_organ != OrganType.pancreas:
        return None

    if not is_blood_compatible(donor, recipient):
        return None

    # Viability 12–20 h
    hours = (now - donor.timestamp).total_seconds() / 3600.0
    if hours > 20:
        return None

    # Donor age <50, BMI <30
    if donor.age_years is None or donor.age_years >= 50:
        return None
    if donor.donor_bmi is None or donor.donor_bmi >= 30:
        return None

    # Amylase/lipase normal
    if donor.donor_amylase_normal is False or donor.donor_lipase_normal is False:
        return None

    # Recipient T1DM
    if not recipient.type1_diabetes:
        return None

    score = 0.0

    # Medical urgency (35 pts) based on indication
    if recipient.pancreas_indication == "SPK":
        score += 35.0
    elif recipient.pancreas_indication == "PAK":
        score += 25.0
    elif recipient.pancreas_indication == "PA":
        score += 15.0

    # HbA1c & C-peptide (25 pts) — lower C-peptide = higher score
    if recipient.c_peptide is not None:
        # Assume 0–5 range
        cp = max(0.0, min(5.0, recipient.c_peptide))
        score += (1.0 - cp / 5.0) * 25.0

    # Wait time (25 pts)
    days_waiting = (now - recipient.added_at).total_seconds() / 86400.0
    wait_points = max(0.0, min(25.0, (days_waiting / 365.0) * 25.0))
    score += wait_points

    # Donor quality placeholder (15 pts)
    score += 15.0

    return min(score, 100.0)


def score_cornea(donor: Donor, recipient: Recipient, now: datetime) -> Optional[float]:
    if donor.organ_type != OrganType.cornea or recipient.required_organ != OrganType.cornea:
        return None

    # Viability up to 14 days
    days = (now - donor.timestamp).total_seconds() / 86400.0
    if days > 14:
        return None

    # Donor age <75
    if donor.age_years is None or donor.age_years >= 75:
        return None

    # Endothelial cell density ≥2000/mm2 — modeled via bool flag donor.donor_amylase_normal is not appropriate;
    # in this simplified model we skip explicit check and assume tissue bank pre-filters.

    score = 0.0

    # Visual acuity (30 pts)
    if recipient.visual_acuity_score is not None:
        va = max(0.0, min(1.0, recipient.visual_acuity_score))
        score += va * 30.0

    # Wait time (30 pts)
    days_waiting = (now - recipient.added_at).total_seconds() / 86400.0
    wait_points = max(0.0, min(30.0, (days_waiting / 365.0) * 30.0))
    score += wait_points

    # Remaining points as generic donor/recipient quality
    score += 40.0

    return min(score, 100.0)


def score_pair(donor: Donor, recipient: Recipient, now: datetime) -> Optional[float]:
    """
    Top-level scoring wrapper used by the allocator.
    """
    if donor.organ_type == OrganType.heart:
        return score_heart(donor, recipient, now)
    if donor.organ_type == OrganType.liver:
        return score_liver(donor, recipient, now)
    if donor.organ_type == OrganType.kidney:
        return score_kidney(donor, recipient, now)
    if donor.organ_type == OrganType.lung:
        return score_lung(donor, recipient, now)
    if donor.organ_type == OrganType.pancreas:
        return score_pancreas(donor, recipient, now)
    if donor.organ_type == OrganType.cornea:
        return score_cornea(donor, recipient, now)
    return None
