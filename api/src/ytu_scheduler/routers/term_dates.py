"""Teaching dates are maintained only in the loopback owner service."""

from datetime import date
from typing import Annotated, Literal, Self

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ytu_scheduler.auth import require_owner
from ytu_scheduler.database import get_session
from ytu_scheduler.models import AcademicTerm, Season

router = APIRouter(prefix="/owner/terms", tags=["owner"], dependencies=[Depends(require_owner)])


class NewTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    academic_year: str = Field(pattern=r"^[0-9]{4}-[0-9]{4}$")
    season: Season
    owner_confirmed: Literal[True]

    @model_validator(mode="after")
    def consecutive_years(self) -> Self:
        first, last = map(int, self.academic_year.split("-"))
        if first < 1 or last != first + 1:
            raise ValueError("Akademik yıl ardışık iki yıldan oluşmalıdır (ör. 2027-2028).")
        return self


class DateEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starts_on: date
    ends_on: date
    expected_starts_on: date | None
    expected_ends_on: date | None
    owner_confirmed: Literal[True]

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if not 0 <= (self.ends_on - self.starts_on).days <= 370:
            raise ValueError("Öğretim tarihleri sıralı ve en fazla 370 gün aralıklı olmalıdır.")
        return self


def view(term: AcademicTerm) -> dict:
    return {
        "code": term.code,
        "starts_on": term.starts_on,
        "ends_on": term.ends_on,
        "timezone": term.timezone,
    }


@router.get("")
def list_terms(response: Response, session: Annotated[Session, Depends(get_session)]) -> list:
    response.headers["Cache-Control"] = "no-store"
    return [view(t) for t in session.scalars(select(AcademicTerm).order_by(AcademicTerm.code))]


@router.post("", status_code=201)
def create_term(
    edit: NewTerm, response: Response, session: Annotated[Session, Depends(get_session)]
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    term = AcademicTerm(
        code=f"{edit.academic_year}-{edit.season.value}",
        academic_year=edit.academic_year,
        season=edit.season,
        timezone="Europe/Istanbul",
    )
    session.add(term)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            409, "Bu dönem zaten var. Listeden seçip tarihlerini düzenleyin."
        ) from None
    session.refresh(term)
    return view(term)


@router.post("/{code}/dates")
def save_dates(
    code: str, edit: DateEdit, response: Response, session: Annotated[Session, Depends(get_session)]
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    term = session.scalar(select(AcademicTerm).where(AcademicTerm.code == code))
    if term is None:
        raise HTTPException(404, "Dönem bulunamadı.")
    if term.timezone != "Europe/Istanbul":
        raise HTTPException(409, "Dönem saat dilimi Europe/Istanbul olmalıdır.")
    # Compare and write in one statement, so another tab cannot overwrite newer dates.
    result = session.execute(
        update(AcademicTerm)
        .where(
            AcademicTerm.id == term.id,
            AcademicTerm.starts_on == edit.expected_starts_on,
            AcademicTerm.ends_on == edit.expected_ends_on,
            AcademicTerm.timezone == "Europe/Istanbul",
        )
        .values(starts_on=edit.starts_on, ends_on=edit.ends_on)
    )
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "Tarihler değişti. Listeyi yenileyip yeniden inceleyin.")
    session.commit()
    session.refresh(term)
    return view(term)
