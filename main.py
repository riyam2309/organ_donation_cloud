from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import HTMLResponse

from .allocator import allocate_atomic
from .events import EventBus
from .hospitals import HOSPITALS
from .models import Allocation, Donor, DonorCreate, EventType, Recipient, RecipientCreate
from .store import InMemoryStore, utcnow


app = FastAPI(title="Real-Time Organ Allocation (Rule-Based)", version="1.0.0")

store = InMemoryStore()
bus = EventBus()


def _coerce_timestamp(ts: datetime | None) -> datetime:
    if ts is None:
        return utcnow()
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    with open(__file__.replace("main.py", "dashboard.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await bus.connect(ws)
    try:
        while True:
            # Keep connection alive; client doesn't need to send messages.
            await ws.receive_text()
    except Exception:
        await bus.disconnect(ws)


@app.get("/api/hospitals", response_model=list[dict])
async def list_hospitals() -> list[dict]:
    return [{"id": h.id, "name": h.name} for h in HOSPITALS]


@app.get("/api/recipients", response_model=List[Recipient])
async def list_recipients() -> List[Recipient]:
    return store.list_recipients()


@app.post("/api/recipients", response_model=Recipient)
async def create_recipient(body: RecipientCreate) -> Recipient:
    recipient = Recipient(
        required_organ=body.required_organ,
        blood_group=body.blood_group,
        rh=body.rh,
        urgency=body.urgency,
        added_at=_coerce_timestamp(body.added_at),
        age_years=body.age_years,
        weight_kg=body.weight_kg,
        height_cm=body.height_cm,
        hospital_id=body.hospital_id,
        heart_status=body.heart_status,
        hemodynamically_stable=body.hemodynamically_stable,
        meld_score=body.meld_score,
        peld_score=body.peld_score,
        hla_match_score=body.hla_match_score,
        pra_percent=body.pra_percent,
        on_dialysis=body.on_dialysis,
        egfr=body.egfr,
        las_score=body.las_score,
        cmv_positive=body.cmv_positive,
        type1_diabetes=body.type1_diabetes,
        pancreas_indication=body.pancreas_indication,
        hba1c=body.hba1c,
        c_peptide=body.c_peptide,
        visual_acuity_score=body.visual_acuity_score,
    )
    store.recipients[recipient.id] = recipient
    await bus.publish(
        EventType.recipient_created,
        utcnow(),
        {
            "recipient_id": str(recipient.id),
            "required_organ": recipient.required_organ,
            "blood_group": recipient.blood_group,
            "rh": recipient.rh,
            "urgency": recipient.urgency,
        },
    )
    return recipient


@app.get("/api/donors", response_model=List[Donor])
async def list_donors() -> List[Donor]:
    return store.list_donors()


@app.post("/api/donors", response_model=Donor)
async def create_donor(body: DonorCreate) -> Donor:
    donor = Donor(
        organ_type=body.organ_type,
        blood_group=body.blood_group,
        rh=body.rh,
        timestamp=_coerce_timestamp(body.timestamp),
        age_years=body.age_years,
        weight_kg=body.weight_kg,
        height_cm=body.height_cm,
        hospital_id=body.hospital_id,
        smoker_pack_years=body.smoker_pack_years,
        donor_bmi=body.donor_bmi,
        donor_amylase_normal=body.donor_amylase_normal,
        donor_lipase_normal=body.donor_lipase_normal,
        cmv_positive=body.cmv_positive,
    )
    store.donors[donor.id] = donor
    await bus.publish(
        EventType.donor_created,
        utcnow(),
        {
            "donor_id": str(donor.id),
            "organ_type": donor.organ_type,
            "blood_group": donor.blood_group,
            "rh": donor.rh,
        },
    )

    # Critical event-driven behavior: donor triggers immediate allocation attempt.
    result = await allocate_atomic(store, donor.id)
    if result.matched and result.recipient_id is not None:
        await bus.publish(
            EventType.allocation_made,
            utcnow(),
            {"donor_id": str(donor.id), "recipient_id": str(result.recipient_id)},
        )
    else:
        await bus.publish(
            EventType.allocation_none,
            utcnow(),
            {"donor_id": str(donor.id), "reason": result.reason},
        )

    return donor


@app.get("/api/allocations", response_model=List[Allocation])
async def list_allocations() -> List[Allocation]:
    return store.list_allocations()


@app.post("/api/allocate/{donor_id}", response_model=dict)
async def allocate_for_donor(donor_id: UUID) -> dict:
    if store.get_donor(donor_id) is None:
        raise HTTPException(status_code=404, detail="Donor not found")

    result = await allocate_atomic(store, donor_id)
    if result.matched and result.recipient_id is not None:
        await bus.publish(
            EventType.allocation_made,
            utcnow(),
            {"donor_id": str(donor_id), "recipient_id": str(result.recipient_id)},
        )
        return {"matched": True, "donor_id": str(donor_id), "recipient_id": str(result.recipient_id)}

    await bus.publish(
        EventType.allocation_none,
        utcnow(),
        {"donor_id": str(donor_id), "reason": result.reason},
    )
    return {"matched": False, "donor_id": str(donor_id), "reason": result.reason}

