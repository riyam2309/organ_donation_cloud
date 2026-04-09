from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple
from uuid import UUID

from .models import Allocation, Donor, Recipient
from .rules import is_blood_compatible, score_pair
from .store import InMemoryStore, utcnow


@dataclass
class AllocationResult:
    matched: bool
    donor_id: UUID
    recipient_id: Optional[UUID] = None
    reason: Optional[str] = None


def _fallback_priority_key(recipient: Recipient, now: datetime) -> Tuple[float, float, str]:
    """
    Fallback deterministic priority if organ-specific score cannot be computed.
    Higher urgency, then longer waiting time.
    """
    waiting_seconds = (now - recipient.added_at).total_seconds()
    return (-float(recipient.urgency), -waiting_seconds, str(recipient.id))


def shortlist_recipients(donor: Donor, recipients: List[Recipient]) -> List[Recipient]:
    """
    Apply basic structural hard gates before detailed scoring.
    """
    valid: list[Recipient] = []
    for r in recipients:
        if r.allocated:
            continue
        if r.required_organ != donor.organ_type:
            continue
        if not is_blood_compatible(donor, r):
            continue
        valid.append(r)
    return valid


async def allocate_atomic(store: InMemoryStore, donor_id: UUID) -> AllocationResult:
    """
    Atomic allocation:
    - Ensures donor can't be allocated twice
    - Ensures recipient can't be allocated twice
    - Uses a single lock for the critical section (sufficient for in-memory hackathon version)
    """
    async with store.allocation_lock:
        donor = store.get_donor(donor_id)
        if donor is None:
            return AllocationResult(matched=False, donor_id=donor_id, reason="donor_not_found")

        if donor.allocated_to_recipient_id is not None:
            return AllocationResult(
                matched=False,
                donor_id=donor_id,
                recipient_id=donor.allocated_to_recipient_id,
                reason="donor_already_allocated",
            )

        now = utcnow()
        recipients = list(store.recipients.values())
        valid = shortlist_recipients(donor, recipients)
        if not valid:
            return AllocationResult(matched=False, donor_id=donor_id, reason="no_compatible_recipients")

        # Score each candidate according to organ-specific rules
        scored: list[tuple[float, Recipient]] = []
        for r in valid:
            s = score_pair(donor, r, now)
            if s is not None:
                scored.append((s, r))

        if not scored:
            return AllocationResult(matched=False, donor_id=donor_id, reason="no_candidate_passed_hard_rules")

        # Max score; deterministic tie-breaker via fallback priority key
        scored.sort(key=lambda item: (-(item[0]),) + _fallback_priority_key(item[1], now))
        best = scored[0][1]

        # Double-check recipient still free (defensive)
        current = store.get_recipient(best.id)
        if current is None or current.allocated:
            return AllocationResult(matched=False, donor_id=donor_id, reason="recipient_unavailable")

        allocation = Allocation(
            donor_id=donor.id,
            recipient_id=best.id,
            organ_type=donor.organ_type,
            donor_blood_group=donor.blood_group,
            recipient_blood_group=best.blood_group,
            donor_rh=donor.rh,
            recipient_rh=best.rh,
            allocated_at=now,
        )

        # Commit updates atomically inside lock
        donor.allocated_to_recipient_id = best.id
        current.allocated = True
        current.allocated_donor_id = donor.id
        store.allocations[allocation.id] = allocation

        return AllocationResult(matched=True, donor_id=donor.id, recipient_id=best.id)

