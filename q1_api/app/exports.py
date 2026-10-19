"""Export endpoints: retry delivery and download the CSV."""
from fastapi import APIRouter, Response

from app.db import DB, get_or_404, to_api
from app.export import deliver
from app.schemas import ExportOut

router = APIRouter(prefix="/api/exports", tags=["exports"])


@router.post("/{export_id}/deliver", response_model=ExportOut)
def redeliver(export_id: str, db: DB):
    """Retry delivery with the export's original Idempotency-Key; does nothing once delivered."""
    return to_api(deliver(db, get_or_404(db.exports, export_id, "export")))


@router.get("/{export_id}/file", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
def download(export_id: str, db: DB):
    export = get_or_404(db.exports, export_id, "export")
    disposition = f'attachment; filename="{export["filename"]}"'
    return Response(export["csv"], media_type="text/csv", headers={"Content-Disposition": disposition})
