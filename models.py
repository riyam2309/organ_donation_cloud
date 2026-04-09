from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class OrganType(str, Enum):
    kidney = "kidney"
    liver = "liver"
    heart = "heart"
    lung = "lung"
    pancreas = "pancreas"
    cornea = "cornea"


class BloodGroup(str, Enum):
    O = "O"
    A = "A"
    B = "B"
    AB = "AB"


class RhFactor(str, Enum):
    positive = "positive"
    negative = "negative"


class DonorCreate(BaseModel):
    organ_type: OrganType
    blood_group: BloodGroup
    rh: RhFactor
    hospital_id: str = Field(min_length=1, max_length=64)
    age_years: Optional[int] = Field(default=None, ge=0, le=120)
    weight_kg: Optional[float] = Field(default=None, ge=1)
    height_cm: Optional[float] = Field(default=None, ge=30)
    smoker_pack_years: Optional[float] = Field(default=None, ge=0)
    donor_bmi: Optional[float] = Field(default=None, ge=10, le=80)
    donor_amylase_normal: Optional[bool] = None
    donor_lipase_normal: Optional[bool] = None
    cmv_positive: Optional[bool] = None
    timestamp: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_required_fields(self) -> "DonorCreate":
        # Age is important for pancreas/cornea rules
        if self.organ_type in {OrganType.pancreas, OrganType.cornea} and self.age_years is None:
            raise ValueError("age_years is required for pancreas/cornea donors")

        # Heart/liver/kidney size rules need weight
        if self.organ_type in {OrganType.heart, OrganType.liver, OrganType.kidney} and self.weight_kg is None:
            raise ValueError("weight_kg is required for heart/liver/kidney donors")

        # Lung rules use height (proxy) and pack-years often
        if self.organ_type == OrganType.lung and self.height_cm is None:
            raise ValueError("height_cm is required for lung donors")

        # Pancreas donor quality gates
        if self.organ_type == OrganType.pancreas:
            if self.donor_bmi is None:
                raise ValueError("donor_bmi is required for pancreas donors")
            if self.donor_amylase_normal is None or self.donor_lipase_normal is None:
                raise ValueError("donor_amylase_normal and donor_lipase_normal are required for pancreas donors")

        return self


class Donor(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    organ_type: OrganType
    blood_group: BloodGroup
    rh: RhFactor
    timestamp: datetime
    age_years: Optional[int] = None
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    hospital_id: Optional[str] = None
    smoker_pack_years: Optional[float] = None
    donor_bmi: Optional[float] = None
    donor_amylase_normal: Optional[bool] = None
    donor_lipase_normal: Optional[bool] = None
    cmv_positive: Optional[bool] = None
    allocated_to_recipient_id: Optional[UUID] = None


class RecipientCreate(BaseModel):
    required_organ: OrganType
    blood_group: BloodGroup
    rh: RhFactor
    hospital_id: str = Field(min_length=1, max_length=64)
    urgency: int = Field(ge=1, le=10, description="10 = most urgent (generic fallback)")
    age_years: Optional[int] = Field(default=None, ge=0, le=120)
    weight_kg: Optional[float] = Field(default=None, ge=1)
    height_cm: Optional[float] = Field(default=None, ge=30)

    # Heart-specific
    heart_status: Optional[str] = Field(
        default=None,
        description="1A, 1B, 2 etc. Used for heart allocation scoring.",
    )
    hemodynamically_stable: Optional[bool] = Field(
        default=None, description="Must be stable enough for surgery."
    )

    # Liver-specific
    meld_score: Optional[int] = Field(default=None, ge=6, le=40)
    peld_score: Optional[int] = Field(default=None, ge=0, le=40)

    # Kidney-specific
    hla_match_score: Optional[int] = Field(default=None, ge=0, le=6)
    pra_percent: Optional[int] = Field(default=None, ge=0, le=100)
    on_dialysis: Optional[bool] = None
    egfr: Optional[float] = Field(default=None, ge=0, le=120)

    # Lung-specific
    las_score: Optional[float] = Field(default=None, ge=0, le=100)
    cmv_positive: Optional[bool] = None

    # Pancreas-specific
    type1_diabetes: Optional[bool] = None
    pancreas_indication: Optional[str] = Field(
        default=None, description="SPK, PAK, or PA"
    )
    hba1c: Optional[float] = Field(default=None, ge=0, le=20)
    c_peptide: Optional[float] = Field(default=None, ge=0)

    # Cornea-specific
    visual_acuity_score: Optional[float] = Field(
        default=None,
        description="Higher = worse vision / more urgent (simplified).",
    )
    added_at: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_required_fields(self) -> "RecipientCreate":
        # Heart needs status + stability + weight
        if self.required_organ == OrganType.heart:
            if self.heart_status not in {"1A", "1B", "2"}:
                raise ValueError("heart_status is required for heart recipients (1A, 1B, or 2)")
            if self.hemodynamically_stable is None:
                raise ValueError("hemodynamically_stable is required for heart recipients")
            if self.weight_kg is None:
                raise ValueError("weight_kg is required for heart recipients")

        # Liver needs MELD or PELD
        if self.required_organ == OrganType.liver:
            if self.meld_score is None and self.peld_score is None:
                raise ValueError("meld_score or peld_score is required for liver recipients")
            if self.weight_kg is None:
                raise ValueError("weight_kg is required for liver recipients")

        # Kidney needs HLA score + PRA + ESRD info
        if self.required_organ == OrganType.kidney:
            if self.hla_match_score is None:
                raise ValueError("hla_match_score (0-6) is required for kidney recipients")
            if self.pra_percent is None:
                raise ValueError("pra_percent is required for kidney recipients")
            if self.on_dialysis is None and self.egfr is None:
                raise ValueError("on_dialysis or egfr is required for kidney recipients")

        # Lung needs LAS + height + CMV status
        if self.required_organ == OrganType.lung:
            if self.las_score is None:
                raise ValueError("las_score is required for lung recipients")
            if self.height_cm is None:
                raise ValueError("height_cm is required for lung recipients")
            if self.cmv_positive is None:
                raise ValueError("cmv_positive is required for lung recipients")

        # Pancreas needs T1DM + indication + c-peptide
        if self.required_organ == OrganType.pancreas:
            if self.type1_diabetes is not True:
                raise ValueError("type1_diabetes must be true for pancreas recipients")
            if self.pancreas_indication not in {"SPK", "PAK", "PA"}:
                raise ValueError("pancreas_indication is required (SPK/PAK/PA) for pancreas recipients")
            if self.c_peptide is None:
                raise ValueError("c_peptide is required for pancreas recipients")

        # Cornea needs vision severity
        if self.required_organ == OrganType.cornea:
            if self.visual_acuity_score is None:
                raise ValueError("visual_acuity_score is required for cornea recipients")

        return self


class Recipient(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    required_organ: OrganType
    blood_group: BloodGroup
    rh: RhFactor
    urgency: int = Field(ge=1, le=10)
    added_at: datetime
    age_years: Optional[int] = None
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    hospital_id: Optional[str] = None

    # Heart
    heart_status: Optional[str] = None
    hemodynamically_stable: Optional[bool] = None

    # Liver
    meld_score: Optional[int] = None
    peld_score: Optional[int] = None

    # Kidney
    hla_match_score: Optional[int] = None
    pra_percent: Optional[int] = None
    on_dialysis: Optional[bool] = None
    egfr: Optional[float] = None

    # Lung
    las_score: Optional[float] = None
    cmv_positive: Optional[bool] = None

    # Pancreas
    type1_diabetes: Optional[bool] = None
    pancreas_indication: Optional[str] = None
    hba1c: Optional[float] = None
    c_peptide: Optional[float] = None

    # Cornea
    visual_acuity_score: Optional[float] = None

    allocated: bool = False
    allocated_donor_id: Optional[UUID] = None


class Allocation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    donor_id: UUID
    recipient_id: UUID
    organ_type: OrganType
    donor_blood_group: BloodGroup
    recipient_blood_group: BloodGroup
    donor_rh: RhFactor
    recipient_rh: RhFactor
    allocated_at: datetime


class EventType(str, Enum):
    donor_created = "donor.created"
    recipient_created = "recipient.created"
    allocation_made = "allocation.made"
    allocation_none = "allocation.none"


class Event(BaseModel):
    type: EventType
    timestamp: datetime
    payload: dict

