"""Case review endpoint."""
from fastapi import APIRouter, HTTPException

from app.db import DB, get_or_404, to_api, transition_case
from app.schemas import CaseOut, ReviewRequest
from app.state_machine import InvalidTransition, review_event

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.post(
    "/{case_id}/review",
    response_model=CaseOut,
    responses={409: {"description": "The review is not allowed from the case's current status"}},
)
def review_case(case_id: str, review: ReviewRequest, db: DB):
    """Record a stage 1 or stage 2 decision; the state machine decides if it is allowed."""
    case = get_or_404(db.cases, case_id, "case")
    try:
        updated = transition_case(db, case, review_event(review.stage, review.decision))
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from None
    return to_api(updated)
