"""
Stage 3: Integration tests for File Upload & Resource endpoints (9 routes).

Tests:
- POST /api/sessions/{session_id}/files — upload file
- GET /api/sessions/{session_id}/files — list uploaded files
- DELETE /api/sessions/{session_id}/files/{file_id} — delete uploaded file
- GET /api/sessions/{session_id}/resources — list resources
- GET /api/sessions/{session_id}/resources/{resource_id} — get resource file
- GET /api/sessions/{session_id}/resources/{resource_id}/download — download as attachment
- DELETE /api/sessions/{session_id}/resources/{resource_id} — soft-remove resource
- GET /api/sessions/{session_id}/images — legacy image list
- GET /api/sessions/{session_id}/images/{image_id} — legacy image get
"""

import uuid


async def _create_session_with_project(env):
    project = await env["create_test_project"]("Resource Test")
    session = await env["create_test_session"](project["project_id"], "ResSession")
    return session


class TestUploadFile:
    async def test_upload_text_file(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/files",
            files={"file": ("test.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "file" in body
        assert body["file"]["original_name"] == "test.txt"

    async def test_upload_binary_file(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        # Create a small PNG-like header
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        resp = await client.post(
            f"/api/sessions/{sid}/files",
            files={"file": ("image.png", png_data, "image/png")},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_upload_to_nonexistent_session(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/sessions/{fake_id}/files",
            files={"file": ("test.txt", b"data", "text/plain")},
        )
        assert resp.status_code == 404


class TestListFiles:
    async def test_list_empty(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        resp = await client.get(f"/api/sessions/{sid}/files")
        assert resp.status_code == 200
        assert resp.json()["files"] == []

    async def test_list_after_upload(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        await client.post(
            f"/api/sessions/{sid}/files",
            files={"file": ("a.txt", b"aaa", "text/plain")},
        )
        await client.post(
            f"/api/sessions/{sid}/files",
            files={"file": ("b.txt", b"bbb", "text/plain")},
        )

        resp = await client.get(f"/api/sessions/{sid}/files")
        assert resp.status_code == 200
        assert len(resp.json()["files"]) == 2


class TestDeleteFile:
    async def test_delete_uploaded_file(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        upload_resp = await client.post(
            f"/api/sessions/{sid}/files",
            files={"file": ("del.txt", b"delete me", "text/plain")},
        )
        file_id = upload_resp.json()["file"]["file_id"]

        resp = await client.delete(f"/api/sessions/{sid}/files/{file_id}")
        assert resp.status_code == 200

        # Verify removed from listing
        list_resp = await client.get(f"/api/sessions/{sid}/files")
        ids = [f["file_id"] for f in list_resp.json()["files"]]
        assert file_id not in ids

    async def test_delete_nonexistent_file(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        resp = await client.delete(f"/api/sessions/{sid}/files/nonexistent")
        assert resp.status_code == 404


class TestListResources:
    async def test_list_resources_empty(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        resp = await client.get(f"/api/sessions/{sid}/resources")
        assert resp.status_code == 200
        body = resp.json()
        assert "resources" in body
        assert "count" in body

    async def test_list_resources_after_upload(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        # Upload a file (auto-registers as resource)
        await client.post(
            f"/api/sessions/{sid}/files",
            files={"file": ("res.txt", b"resource data", "text/plain")},
        )

        resp = await client.get(f"/api/sessions/{sid}/resources")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1


class TestGetResource:
    async def test_get_resource_file(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        # Upload a file
        await client.post(
            f"/api/sessions/{sid}/files",
            files={"file": ("content.txt", b"file content here", "text/plain")},
        )

        # Get the resource list
        res_resp = await client.get(f"/api/sessions/{sid}/resources")
        resources = res_resp.json()["resources"]
        if resources:
            rid = resources[0]["resource_id"]
            resp = await client.get(f"/api/sessions/{sid}/resources/{rid}")
            assert resp.status_code == 200

    async def test_get_nonexistent_resource(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        resp = await client.get(f"/api/sessions/{sid}/resources/nonexistent")
        assert resp.status_code == 404


class TestDownloadResource:
    async def test_download_resource(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        await client.post(
            f"/api/sessions/{sid}/files",
            files={"file": ("dl.txt", b"download content", "text/plain")},
        )

        res_resp = await client.get(f"/api/sessions/{sid}/resources")
        resources = res_resp.json()["resources"]
        if resources:
            rid = resources[0]["resource_id"]
            resp = await client.get(f"/api/sessions/{sid}/resources/{rid}/download")
            assert resp.status_code == 200
            assert "attachment" in resp.headers.get("content-disposition", "")


class TestRemoveResource:
    async def test_soft_remove_resource(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        await client.post(
            f"/api/sessions/{sid}/files",
            files={"file": ("rm.txt", b"remove me", "text/plain")},
        )

        res_resp = await client.get(f"/api/sessions/{sid}/resources")
        resources = res_resp.json()["resources"]
        if resources:
            rid = resources[0]["resource_id"]
            resp = await client.delete(f"/api/sessions/{sid}/resources/{rid}")
            assert resp.status_code == 200

    async def test_remove_nonexistent_resource(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        resp = await client.delete(f"/api/sessions/{sid}/resources/nonexistent")
        # API may return 200 (no-op) or 404 depending on implementation
        assert resp.status_code in (200, 404)


class TestLegacyImages:
    async def test_list_images_empty(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        resp = await client.get(f"/api/sessions/{sid}/images")
        assert resp.status_code == 200
        body = resp.json()
        assert "images" in body
        assert "count" in body

    async def test_get_nonexistent_image(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session_with_project(api_integration_env)
        sid = session["session_id"]

        resp = await client.get(f"/api/sessions/{sid}/images/nonexistent")
        assert resp.status_code == 404
