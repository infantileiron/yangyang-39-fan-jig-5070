from fastapi import APIRouter, Depends, Query, Response

from src.constants.enums import Role
from src.controllers.views import correction_view
from src.models.base import Session
from src.schemas.requests import CorrectionInput, CorrectionVerify
from src.services.auth import current_user, roles
from src.services.corrections import create_correction, list_corrections, verify_correction

router = APIRouter(prefix='/api/corrections')
editor = roles(Role.ANALYST, Role.SUPERVISOR)


@router.get('')
def index(trial: str | None = Query(None, max_length=60), user=Depends(current_user)):
    with Session() as db:
        return [correction_view(item) for item in list_corrections(db, trial.strip() if trial else None)]


@router.post('')
def create(request: CorrectionInput, response: Response, user=Depends(editor)):
    with Session.begin() as db:
        row, created = create_correction(db, request.model_dump(), user['sub'])
        view = correction_view(row)
    if not created:
        # 幂等重试：不新增记录，返回 200 和同一条更正
        response.status_code = 200
        view['deduplicated'] = True
    else:
        response.status_code = 201
    return view


@router.post('/{correction_id}/verify', status_code=201)
def verify(correction_id: int, request: CorrectionVerify, user=Depends(roles(Role.SUPERVISOR))):
    with Session.begin() as db:
        return correction_view(verify_correction(db, correction_id, user['sub'], request.note))
