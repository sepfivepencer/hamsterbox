from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api import dependencies
from app.application.share_preview import preview_share_package
from app.db.base import Base
from app.db.models import AppSettingModel
from app.db.session import build_engine
from app.main import app


def test_export_preview_and_download_share_a_stable_library_id(tmp_path, monkeypatch):
    engine = build_engine(f"sqlite:///{(tmp_path / 'library.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(dependencies, "get_session", factory)
    client = TestClient(app)
    try:
        entry = client.post("/api/entries", json={"title": "分享条目", "type": "book"})
        assert entry.status_code == 201
        payload = {"entry_ids": [entry.json()["id"]]}

        preview = client.post("/api/share/export/preview", json=payload)
        assert preview.status_code == 200
        assert preview.json()["entries"][0]["title"] == "分享条目"
        with factory() as session:
            assert session.get(AppSettingModel, "share.source_library_id") is None

        manifests = []
        for _ in range(2):
            download = client.post("/api/share/export", json=payload)
            assert download.status_code == 200
            assert download.headers["content-type"] == "application/octet-stream"
            assert "hamsterbox.hbox" in download.headers["content-disposition"]
            with ZipFile(BytesIO(download.content)) as archive:
                assert archive.namelist() == ["manifest.json"]
                manifests.append(json.loads(archive.read("manifest.json")))
        assert manifests[0]["source_library_id"] == manifests[1]["source_library_id"]
        assert manifests[0]["package_id"] != manifests[1]["package_id"]

        invalid = client.post("/api/share/export", json={"entry_ids": [9999]})
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "SHARE_EXPORT_INVALID"
        bad_type = client.post("/api/share/export", json={"entry_ids": [True]})
        assert bad_type.status_code == 422
        assert bad_type.json()["code"] == "VALIDATION_ERROR"

        sample = tmp_path / "sample.my-format"
        sample.write_bytes(b"custom format bytes")
        attached = client.post(
            f"/api/entries/{entry.json()['id']}/resources",
            json={"path": str(sample)},
        )
        assert attached.status_code == 201
        assert attached.json()["extension"] == ".my-format"
        assert attached.json()["file_name"] == "sample.my-format"

        hyphenated = {"entry_ids": [entry.json()["id"]]}
        preview = client.post("/api/share/export/preview", json=hyphenated)
        assert preview.status_code == 200, preview.text
        listed = preview.json()["entries"][0]["resources"][0]
        fingerprint = sha256(sample.read_bytes()).hexdigest()
        assert listed["file_name"] == "sample.my-format"
        assert listed["extension"] == ""
        assert listed["sha256"] == fingerprint
        assert str(sample) not in preview.text

        download = client.post("/api/share/export", json=hyphenated)
        assert download.status_code == 200, download.text
        read_back = preview_share_package(download.content).manifest["entries"][0]["resources"][0]
        assert read_back["file_name"] == "sample.my-format"
        assert read_back["extension"] == ""
        assert read_back["sha256"] == fingerprint
    finally:
        client.close()
        engine.dispose()
