from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import UUID

from .models import Allocation, Donor, Recipient


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class InMemoryStore:
    donors: Dict[UUID, Donor] = field(default_factory=dict)
    recipients: Dict[UUID, Recipient] = field(default_factory=dict)
    allocations: Dict[UUID, Allocation] = field(default_factory=dict)

    allocation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def list_donors(self) -> List[Donor]:
        return sorted(self.donors.values(), key=lambda d: d.timestamp, reverse=True)

    def list_recipients(self) -> List[Recipient]:
        return sorted(self.recipients.values(), key=lambda r: r.added_at)

    def list_allocations(self) -> List[Allocation]:
        return sorted(self.allocations.values(), key=lambda a: a.allocated_at, reverse=True)

    def get_donor(self, donor_id: UUID) -> Optional[Donor]:
        return self.donors.get(donor_id)

    def get_recipient(self, recipient_id: UUID) -> Optional[Recipient]:
        return self.recipients.get(recipient_id)

