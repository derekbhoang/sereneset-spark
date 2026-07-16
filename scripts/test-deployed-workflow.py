from __future__ import annotations

import argparse
import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener


SHOWCASE_NAME = "SereneSet Essentials Launch (Showcase)"


class WorkflowCheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class Response:
    body: bytes
    content_type: str
    status: int

    def json(self) -> Any:
        return json.loads(self.body)


class HttpClient:
    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.opener = build_opener(ProxyHandler({}))

    def request(
        self,
        path_or_url: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Response:
        url = (
            path_or_url
            if path_or_url.startswith(("http://", "https://"))
            else f"{self.base_url}{path_or_url}"
        )
        body = None
        headers = {"Accept": "application/json, application/zip, */*"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                return Response(
                    body=response.read(),
                    content_type=response.headers.get_content_type(),
                    status=response.status,
                )
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise WorkflowCheckError(
                f"{method} {url} returned {exc.code}: {detail}"
            ) from exc


def require(condition: Any, message: str) -> None:
    if not condition:
        raise WorkflowCheckError(message)


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def check_brand_assets(
    client: HttpClient,
    campaign_id: str,
) -> list[dict[str, Any]]:
    links = client.request(f"/api/v1/campaigns/{campaign_id}/brand-assets").json()
    require(len(links) == 3, "Showcase must have exactly three brand assets")

    for link in links:
        brand_asset = link["brand_asset"]
        download = client.request(
            f"/api/v1/brand-assets/{brand_asset['id']}/download-url"
        ).json()
        stored = client.request(download["download_url"])
        require(
            len(stored.body) == brand_asset["size_bytes"],
            f"Brand asset size mismatch: {brand_asset['name']}",
        )
        require(
            sha256_hex(stored.body) == brand_asset["sha256"],
            f"Brand asset hash mismatch: {brand_asset['name']}",
        )
    return links


def check_assets(
    client: HttpClient,
    campaign_id: str,
) -> tuple[list[dict[str, Any]], int]:
    query = urlencode({"status": "approved"})
    assets = client.request(f"/api/v1/campaigns/{campaign_id}/assets?{query}").json()
    require(len(assets) == 2, "Showcase must have two approved assets")
    require(
        {asset["format"] for asset in assets} == {"image", "video_concept"},
        "Showcase must contain approved image and video assets",
    )

    version_count = 0
    for asset in assets:
        require(
            asset["status"] == "approved", "Status filter returned non-approved data"
        )
        require(asset["versions"], f"Asset has no versions: {asset['title']}")
        for version in asset["versions"]:
            version_count += 1
            require(
                version["inputs"], f"Version has no input snapshots: {version['id']}"
            )
            require(
                version["generation_metadata"].get("source") == "idempotent_demo_seed",
                f"Version provenance source is missing: {version['id']}",
            )

            metadata = client.request(
                f"/api/v1/assets/{asset['id']}/versions/{version['id']}/download-url"
            ).json()
            sidecar = client.request(metadata["download_url"]).json()
            require(
                sidecar["version"]["id"] == version["id"],
                f"Stored sidecar identifies the wrong version: {version['id']}",
            )

            artifact = client.request(
                "/api/v1/assets/"
                f"{asset['id']}/versions/{version['id']}/artifact/download-url"
            ).json()
            stored_artifact = client.request(artifact["download_url"])
            require(
                len(stored_artifact.body) == artifact["artifact_size_bytes"],
                f"Artifact size mismatch: {version['id']}",
            )
            expected_hash = version["generation_metadata"]["artifact_flow"]["sha256"]
            require(
                sha256_hex(stored_artifact.body) == expected_hash,
                f"Artifact hash mismatch: {version['id']}",
            )
            if artifact["artifact_content_type"] == "image/png":
                require(
                    stored_artifact.body.startswith(b"\x89PNG\r\n\x1a\n"),
                    "Image artifact is not a PNG",
                )
            if artifact["artifact_content_type"] == "video/webm":
                require(
                    stored_artifact.body.startswith(b"\x1aE\xdf\xa3"),
                    "Video artifact is not a WebM container",
                )

    require(version_count == 3, "Showcase must contain three asset versions")
    return assets, version_count


def check_generation_job(client: HttpClient, campaign_id: str) -> None:
    jobs = client.request(
        f"/api/v1/campaigns/{campaign_id}/generation-jobs?status=succeeded"
    ).json()
    require(len(jobs) == 1, "Showcase must have one succeeded video job")
    require(jobs[0]["progress_percent"] == 100, "Video job is not complete")


def check_export(client: HttpClient, campaign_id: str) -> int:
    export = client.request(f"/api/v1/campaigns/{campaign_id}/export")
    require(export.content_type == "application/zip", "Campaign export is not a ZIP")
    with zipfile.ZipFile(io.BytesIO(export.body)) as archive:
        names = set(archive.namelist())
        require("manifest.json" in names, "Campaign export has no root manifest")
        require(
            "brand-assets/manifest.json" in names,
            "Campaign export has no brand-assets manifest",
        )
        manifest = json.loads(archive.read("manifest.json"))

    require(len(manifest["brand_assets"]) == 3, "Export omitted a brand asset")
    require(len(manifest["assets"]) == 2, "Export omitted an approved asset")
    versions = [
        version for asset in manifest["assets"] for version in asset["versions"]
    ]
    require(len(versions) == 3, "Export omitted an asset version")
    for version in versions:
        require(version["artifact_zip_path"] in names, "Export omitted an artifact")
        require(version["metadata_zip_path"] in names, "Export omitted a sidecar")
        require(
            not version["artifact_export_error"], "Export reported an artifact error"
        )
        require(not version["metadata_export_error"], "Export reported a sidecar error")
        require(version["input_assets"], "Export omitted input provenance")
        require(
            all(item.get("zip_path") in names for item in version["input_assets"]),
            "Export omitted an input file",
        )
    return len(export.body)


def check_campaign_crud(client: HttpClient) -> None:
    marker = uuid.uuid4().hex[:10]
    created_id: str | None = None
    try:
        created = client.request(
            "/api/v1/campaigns",
            method="POST",
            payload={
                "name": f"Device smoke {marker}",
                "product": "Temporary workflow check",
                "audience": "Deployment tester",
                "status": "drafting",
                "due_date": None,
                "owner": "Device test",
                "goal": "Verify campaign creation through the deployed proxy.",
                "tone": "Direct",
                "brief": "This campaign is deleted by the same smoke test.",
                "channels": ["QA"],
                "brand_inputs": [],
            },
        ).json()
        created_id = created["id"]
        campaigns = client.request("/api/v1/campaigns").json()
        require(
            any(campaign["id"] == created_id for campaign in campaigns),
            "Created campaign was not returned by the deployed API",
        )
    finally:
        if created_id is not None:
            response = client.request(
                f"/api/v1/campaigns/{created_id}",
                method="DELETE",
            )
            require(response.status == 204, "Temporary campaign deletion failed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test a deployed SereneSet workflow.")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8080",
        help="Frontend origin that proxies /api/v1 requests.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=45)
    args = parser.parse_args()
    client = HttpClient(args.base_url, args.timeout_seconds)

    frontend = client.request("/")
    require(frontend.status == 200, "Frontend did not return HTTP 200")
    require(b"SereneSet Spark" in frontend.body, "Frontend shell is missing")

    readiness = client.request("/api/v1/health/ready").json()
    require(readiness["status"] == "ready", "Deployment is not ready")
    require(
        all(check["status"] == "ok" for check in readiness["checks"].values()),
        "One or more readiness dependencies are unhealthy",
    )

    campaigns = client.request("/api/v1/campaigns").json()
    showcase = next(
        (campaign for campaign in campaigns if campaign["name"] == SHOWCASE_NAME),
        None,
    )
    require(showcase is not None, "Showcase campaign was not found")
    campaign_id = showcase["id"]

    links = check_brand_assets(client, campaign_id)
    assets, version_count = check_assets(client, campaign_id)
    check_generation_job(client, campaign_id)
    export_size = check_export(client, campaign_id)
    check_campaign_crud(client)

    print(
        json.dumps(
            {
                "status": "passed",
                "base_url": args.base_url,
                "campaign_id": campaign_id,
                "brand_assets": len(links),
                "assets": len(assets),
                "versions": version_count,
                "export_size_bytes": export_size,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
