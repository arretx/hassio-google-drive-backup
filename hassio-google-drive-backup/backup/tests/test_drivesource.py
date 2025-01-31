import os

import pytest
from ..exceptions import GoogleCredentialsExpired, GoogleDnsFailure, GoogleCantConnect, GoogleSessionError, GoogleTimeoutError, GoogleInternalError
from ..drivesource import FOLDER_MIME_TYPE, DriveSource
from ..snapshots import DriveSnapshot, DummySnapshot
from time import sleep
from .helpers import createSnapshotTar
from .conftest import ServerInstance
from ..config import Config
from .faketime import FakeTime
from ..settings import Setting

RETRY_EXHAUSTION_SLEEPS = [2, 4, 8, 16, 32]


def test_sync_empty(drive) -> None:
    assert len(drive.get()) == 0


def test_CRUD(drive, time) -> None:
    from_snapshot: DummySnapshot = DummySnapshot("Test Name", time.toUtc(time.local(1985, 12, 6)), "fake source", "testslug")

    data = createSnapshotTar("testslug", "Test Name", time.now(), 1024 * 1024 * 10)
    snapshot: DriveSnapshot = drive.save(from_snapshot, data)
    assert snapshot.name() == "Test Name"
    assert snapshot.date() == time.local(1985, 12, 6)
    assert not snapshot.retained()
    assert snapshot.size() == data.size()
    assert snapshot.slug() == "testslug"
    assert len(snapshot.id()) > 0
    assert snapshot.snapshotType() == from_snapshot.snapshotType()
    assert snapshot.protected() == from_snapshot.protected()
    from_snapshot.addSource(snapshot)

    # downlaod the item, its bytes should match up
    download = drive.read(from_snapshot)
    data.seek(0)
    while True:
        from_file = data.read(1024 * 1024)
        from_download = download.read(1024 * 1024)
        if len(from_file) == 0:
            assert len(from_download) == 0
            break
        if from_file != from_download:
            print("break!")
        assert from_file == from_download

    # read the item, make sure its data matches up
    snapshots = drive.get()
    assert len(snapshots) == 1
    snapshot = snapshots[from_snapshot.slug()]
    assert snapshot.name() == "Test Name"
    assert snapshot.date() == time.local(1985, 12, 6)
    assert not snapshot.retained()
    assert snapshot.size() == data.size()
    assert snapshot.slug() == "testslug"
    assert len(snapshot.id()) > 0
    assert snapshot.snapshotType() == from_snapshot.snapshotType()
    assert snapshot.protected() == from_snapshot.protected()

    # update retention
    assert not snapshot.retained()
    drive.retain(from_snapshot, True)
    assert drive.get()[from_snapshot.slug()].retained()
    drive.retain(from_snapshot, False)
    assert not drive.get()[from_snapshot.slug()].retained()

    # Delete the item, make sure its gone
    drive.delete(from_snapshot)
    snapshots = drive.get()
    assert len(snapshots) == 0


def test_folder_creation(drive, time, config):
    assert len(drive.get()) == 0

    folderId = drive.getFolderId()
    assert len(folderId) > 0

    item = drive.drivebackend.get(folderId)
    assert not item["trashed"]
    assert item["name"] == "Hass.io Snapshots"
    assert item["mimeType"] == FOLDER_MIME_TYPE
    assert item["appProperties"]['backup_folder'] == 'true'

    # sync again, assert the folder is reused
    time.advanceDay()
    os.remove(config.get(Setting.FOLDER_FILE_PATH))
    assert len(drive.get()) == 0
    assert drive.getFolderId() == folderId

    # trash the folder, assert we create a new one on sync
    drive.drivebackend.update(folderId, {"trashed": True})
    assert drive.drivebackend.get(folderId)["trashed"] is True
    assert len(drive.get()) == 0
    assert drive.getFolderId() != folderId

    # delete the folder, assert we create a new one
    folderId = drive.getFolderId()
    drive.drivebackend.delete(folderId)
    assert len(drive.get()) == 0
    assert drive.getFolderId() != folderId


def test_folder_selection(drive, time):
    folder_metadata = {
        'name': "Junk Data",
        'mimeType': FOLDER_MIME_TYPE,
        'appProperties': {
            "backup_folder": "true",
        },
    }

    # create two fodlers at different times
    id_old = drive.drivebackend.createFolder(folder_metadata)['id']
    sleep(2)
    id_new = drive.drivebackend.createFolder(folder_metadata)['id']

    # Verify we use the newest
    drive.get()
    assert drive.getFolderId() == id_new
    assert drive.getFolderId() != id_old


def test_bad_auth_creds(drive: DriveSource, time):
    drive.drivebackend.cred_refresh = "not_allowed"
    with pytest.raises(GoogleCredentialsExpired):
        drive.get()
    assert time.sleeps == []


def test_out_of_space():
    # SOMEDAY: Implement this test, server needs to return drive error json (see DriveRequests)
    pass


def test_drive_dns_resolution_error(drive: DriveSource, server: ServerInstance, config: Config, time):
    config.override(Setting.DRIVE_URL, "http://fsdfsdasdasdf.saasdsdfsdfsd.com:2567")
    with pytest.raises(GoogleDnsFailure):
        drive.get()
    assert time.sleeps == []


def test_drive_connect_error(drive: DriveSource, server: ServerInstance, config: Config, time):
    config.override(Setting.DRIVE_URL, "http://localhost:1034")
    with pytest.raises(GoogleCantConnect):
        drive.get()
    assert time.sleeps == []


def test_upload_session_expired(drive, time, server: ServerInstance):
    from_snapshot: DummySnapshot = DummySnapshot("Test Name", time.toUtc(time.local(1985, 12, 6)), "fake source", "testslug")
    data = createSnapshotTar("testslug", "Test Name", time.now(), 1024 * 1024 * 10)
    server.update({"drive_upload_error": 404})
    with pytest.raises(GoogleSessionError):
        drive.save(from_snapshot, data)
    assert time.sleeps == []


def test_drive_timeout(drive, config, time: FakeTime):
    config.override(Setting.GOOGLE_DRIVE_TIMEOUT_SECONDS, 0.000001)
    with pytest.raises(GoogleTimeoutError):
        drive.get()
    assert time.sleeps == []


def test_google_internal_error(drive, server: ServerInstance, time: FakeTime):
    server.update({"drive_all_error": 500})
    with pytest.raises(GoogleInternalError):
        drive.get()
    assert time.sleeps == RETRY_EXHAUSTION_SLEEPS
    time.sleeps = []

    server.update({"drive_all_error": 503})
    with pytest.raises(GoogleInternalError):
        drive.get()
    assert time.sleeps == RETRY_EXHAUSTION_SLEEPS


def test_check_time(drive: DriveSource, drive_creds):
    assert not drive.check()
    drive.saveCreds(drive_creds)
    assert drive.check()


def test_disable_upload(drive: DriveSource, config: Config):
    assert drive.upload()
    config.override(Setting.ENABLE_DRIVE_UPLOAD, False)
    assert not drive.upload()
