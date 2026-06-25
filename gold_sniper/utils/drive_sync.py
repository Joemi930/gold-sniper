import asyncio
import json
import mimetypes
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

# ── Allows running this file directly: python utils/drive_sync.py ────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
# ─────────────────────────────────────────────────────────────────────────────

import schedule
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from utils.logger import get_logger
from utils.discord_notifier import send_discord_notification


ROOT_DIR = Path(__file__).resolve().parents[1]
CREDENTIALS_PATH = ROOT_DIR / "data" / "credentials.json"
TOKEN_PATH = ROOT_DIR / "data" / "drive_token.json"
ERROR_LOG = ROOT_DIR / "logs" / "drive_sync_errors.log"
REPORTS_DIR = ROOT_DIR / "logs" / "reports"
DRIVE_FOLDER_NAME = "GoldSniper_V3_Backups"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
SCHEDULE_TZ = "Africa/Kinshasa"
LOCAL_TZ = ZoneInfo(SCHEDULE_TZ)
FAILURE_ALERT = "⚠️ Google Drive sync échoué — données conservées localement"


class DriveSync:
    """Synchronise les donnees critiques Gold Sniper vers Google Drive."""

    def __init__(
        self,
        credentials_path: Path = CREDENTIALS_PATH,
        token_path: Path = TOKEN_PATH,
        service_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.service_factory = service_factory
        self.logger = get_logger()

    def first_launch_needs_browser(self) -> bool:
        return not self.token_path.exists()

    async def sync_once(self, blackboard=None) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(self._sync_once_sync)
        except Exception as exc:
            await self._handle_failure(blackboard, exc)
            return {"ok": False, "error": str(exc), "uploaded": []}

    async def _handle_failure(self, blackboard, exc: Exception) -> None:
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        line = f"{datetime.now(LOCAL_TZ).isoformat()} | {type(exc).__name__}: {exc}\n"
        await asyncio.to_thread(_append_error_log, line)
        self.logger.warning(f"Google Drive sync echoue: {exc}")
        if blackboard is not None:
            await send_discord_notification(blackboard, FAILURE_ALERT)

    def _sync_once_sync(self) -> dict[str, Any]:
        service = self.service_factory() if self.service_factory else self._build_service()
        folder_id = self._ensure_folder(service, DRIVE_FOLDER_NAME)
        uploaded = []
        for path in collect_sync_files():
            uploaded.append(self._upload_file(service, path, folder_id))
        return {
            "ok": True,
            "folder_id": folder_id,
            "uploaded": uploaded,
            "uploaded_count": len(uploaded),
            "synced_at": datetime.now(LOCAL_TZ).isoformat(),
        }

    def _build_service(self):
        credentials = self._load_credentials()
        return build("drive", "v3", credentials=credentials, cache_discovery=False)

    def _load_credentials(self) -> Credentials:
        if not self.credentials_path.exists():
            raise FileNotFoundError(f"credentials.json introuvable: {self.credentials_path}")

        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        credentials = None
        if self.token_path.exists():
            credentials = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())

        if not credentials or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), SCOPES)
            credentials = flow.run_local_server(port=0, open_browser=True)

        self.token_path.write_text(credentials.to_json(), encoding="utf-8")
        return credentials

    def _ensure_folder(self, service, folder_name: str) -> str:
        escaped = folder_name.replace("'", "\\'")
        query = (
            "mimeType='application/vnd.google-apps.folder' "
            f"and name='{escaped}' and trashed=false"
        )
        response = service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            pageSize=1,
        ).execute()
        files = response.get("files", [])
        if files:
            return files[0]["id"]

        metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        folder = service.files().create(body=metadata, fields="id").execute()
        return folder["id"]

    def _upload_file(self, service, path: Path, folder_id: str) -> dict[str, Any]:
        drive_name = build_drive_filename(path)
        existing_id = self._find_existing_file(service, drive_name, folder_id)
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        media = MediaFileUpload(str(path), mimetype=mime_type, resumable=False)
        metadata = {"name": drive_name, "parents": [folder_id]}

        if existing_id:
            result = service.files().update(
                fileId=existing_id,
                body={"name": drive_name},
                media_body=media,
                fields="id, name, webViewLink",
            ).execute()
        else:
            result = service.files().create(
                body=metadata,
                media_body=media,
                fields="id, name, webViewLink",
            ).execute()
        return {
            "local_path": str(path),
            "drive_id": result.get("id"),
            "drive_name": result.get("name", drive_name),
            "webViewLink": result.get("webViewLink"),
        }

    def _find_existing_file(self, service, name: str, folder_id: str) -> str | None:
        escaped_name = name.replace("'", "\\'")
        query = f"name='{escaped_name}' and '{folder_id}' in parents and trashed=false"
        response = service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            pageSize=1,
        ).execute()
        files = response.get("files", [])
        return files[0]["id"] if files else None


def collect_sync_files(now: datetime | None = None) -> list[Path]:
    files = [
        ROOT_DIR / "data" / "memory.db",
        ROOT_DIR / "logs" / "decision_log.jsonl",
        ROOT_DIR / "logs" / "backtests" / "backtest_results.jsonl",
    ]
    if REPORTS_DIR.exists():
        files.extend(sorted(REPORTS_DIR.glob("*.txt")))
        files.extend(sorted(REPORTS_DIR.glob("*.json")))
        files.extend(sorted(REPORTS_DIR.glob("*.jsonl")))
    return [path for path in files if path.exists() and path.is_file()]


def build_drive_filename(path: Path, now: datetime | None = None) -> str:
    now = (now or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ)
    stamp = now.strftime("%Y-%m-%d")
    relative = path.resolve().relative_to(ROOT_DIR)
    safe_relative = "__".join(relative.parts)
    return f"{stamp}__{safe_relative}"


def install_drive_sync_job(
    scheduler,
    blackboard,
    syncer: DriveSync | None = None,
    job_factory: Callable[[], Callable[[], None]] | None = None,
) -> None:
    if job_factory:
        scheduler.every().day.at("23:00", SCHEDULE_TZ).do(job_factory())
        return
    syncer = syncer or DriveSync()
    scheduler.every().day.at("23:00", SCHEDULE_TZ).do(_async_sync_job(syncer, blackboard))


async def drive_sync_loop(blackboard, syncer: DriveSync | None = None) -> None:
    logger = get_logger()
    scheduler = schedule.Scheduler()
    syncer = syncer or DriveSync()
    install_drive_sync_job(scheduler, blackboard, syncer)
    logger.info("Google Drive sync demarree: quotidien 23:00 UTC+1")
    while not blackboard.kill_event.is_set():
        try:
            scheduler.run_pending()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await syncer._handle_failure(blackboard, exc)
        await asyncio.sleep(1.0)


def _async_sync_job(syncer: DriveSync, blackboard) -> Callable[[], None]:
    def run() -> None:
        asyncio.create_task(syncer.sync_once(blackboard))
    return run


def _append_error_log(line: str) -> None:
    with ERROR_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line)


async def _main() -> None:
    result = await DriveSync().sync_once()
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(_main())
