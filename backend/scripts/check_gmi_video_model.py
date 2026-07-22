from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import get_settings


DEFAULT_GMI_BASE_URL = "https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey"
EXIT_VERIFIED = 0
EXIT_REQUEST_FAILED = 1
EXIT_NOT_CONFIGURED = 2
EXIT_NOT_VIDEO_TO_VIDEO = 3

VIDEO_PARAMETER_TYPES = {
    "input_video",
    "reference_video",
    "source_video",
    "video",
    "video_file",
    "video_uri",
    "video_url",
}
VIDEO_PARAMETER_NAMES = VIDEO_PARAMETER_TYPES | {
    "input_video_file",
    "input_video_uri",
    "input_video_url",
    "reference_video_file",
    "reference_video_uri",
    "reference_video_url",
    "source_video_file",
    "source_video_uri",
    "source_video_url",
}


class ModelDetailsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelCapabilityReport:
    requested_model: str
    model: str
    status: str | None
    model_type: str | None
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...]
    parameter_names: tuple[str, ...]
    video_input_parameters: tuple[str, ...]
    required_video_input_parameters: tuple[str, ...]
    brief_description: str | None
    pricing_details: str | None
    video_to_video_verified: bool
    reason: str

    def as_json_dict(self) -> dict[str, object]:
        report = asdict(self)
        for key in (
            "input_modalities",
            "output_modalities",
            "parameter_names",
            "video_input_parameters",
            "required_video_input_parameters",
        ):
            report[key] = list(report[key])

        return report


def normalized_identifier(value: object) -> str:
    if not isinstance(value, str):
        return ""

    snake_case = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value.strip())
    return snake_case.replace("-", "_").replace(" ", "_").casefold()


def optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()

    return None


def modality_types(details: dict[str, Any], direction: str) -> tuple[str, ...]:
    modalities = details.get("modalities")
    if not isinstance(modalities, dict):
        return ()

    entries = modalities.get(direction)
    if not isinstance(entries, list):
        return ()

    types: list[str] = []
    for entry in entries:
        raw_type = entry.get("type") if isinstance(entry, dict) else entry
        media_type = normalized_identifier(raw_type)
        if media_type and media_type not in types:
            types.append(media_type)

    return tuple(types)


def model_parameters(details: dict[str, Any]) -> list[dict[str, Any]]:
    parameters = details.get("parameters")
    if not isinstance(parameters, list):
        return []

    return [parameter for parameter in parameters if isinstance(parameter, dict)]


def is_explicit_video_input_parameter(parameter: dict[str, Any]) -> bool:
    parameter_type = normalized_identifier(parameter.get("type"))
    parameter_name = normalized_identifier(parameter.get("name"))
    return (
        parameter_type in VIDEO_PARAMETER_TYPES
        or parameter_name in VIDEO_PARAMETER_NAMES
    )


def analyze_model_details(
    details: dict[str, Any],
    *,
    requested_model: str,
) -> ModelCapabilityReport:
    model = optional_string(details.get("model")) or requested_model
    status = optional_string(details.get("status"))
    model_type = optional_string(details.get("model_type"))
    input_modalities = modality_types(details, "input")
    output_modalities = modality_types(details, "output")
    parameters = model_parameters(details)
    parameter_names = tuple(
        name
        for parameter in parameters
        if (name := optional_string(parameter.get("name"))) is not None
    )
    video_parameters = [
        parameter
        for parameter in parameters
        if is_explicit_video_input_parameter(parameter)
    ]
    video_input_parameters = tuple(
        name
        for parameter in video_parameters
        if (name := optional_string(parameter.get("name"))) is not None
    )
    required_video_input_parameters = tuple(
        name
        for parameter in video_parameters
        if parameter.get("required") is True
        and (name := optional_string(parameter.get("name"))) is not None
    )

    checks = (
        (normalized_identifier(status) == "active", "model is not active"),
        (
            normalized_identifier(model_type) == "video",
            "model type is not video",
        ),
        (
            "video" in input_modalities,
            "input modalities do not declare video",
        ),
        (
            bool(video_input_parameters),
            "model parameters do not expose an explicit video input",
        ),
        (
            "video" in output_modalities,
            "output modalities do not declare video",
        ),
    )
    failed_reason = next((reason for passed, reason in checks if not passed), None)
    verified = failed_reason is None
    reason = (
        "active video model with explicit video input and video output"
        if verified
        else failed_reason or "video-to-video support could not be verified"
    )

    return ModelCapabilityReport(
        requested_model=requested_model,
        model=model,
        status=status,
        model_type=model_type,
        input_modalities=input_modalities,
        output_modalities=output_modalities,
        parameter_names=parameter_names,
        video_input_parameters=video_input_parameters,
        required_video_input_parameters=required_video_input_parameters,
        brief_description=optional_string(details.get("brief_description")),
        pricing_details=optional_string(details.get("pricing_details")),
        video_to_video_verified=verified,
        reason=reason,
    )


def model_details_url(*, base_url: str, model: str) -> str:
    normalized_base_url = base_url.strip().rstrip("/")
    if not normalized_base_url.startswith("https://"):
        raise ValueError("GMI base URL must start with https://")

    return f"{normalized_base_url}/models/{quote(model, safe='')}"


def response_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None

    if isinstance(body, dict):
        detail = body.get("detail") or body.get("error") or body.get("message")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()[:500]

    return response.text.strip()[:500] or response.reason_phrase


def fetch_model_details(
    *,
    client: httpx.Client,
    base_url: str,
    model: str,
) -> dict[str, Any]:
    url = model_details_url(base_url=base_url, model=model)
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = response_error_message(exc.response)
        raise ModelDetailsError(
            f"GMI model-details request failed ({exc.response.status_code}): {detail}"
        ) from exc
    except httpx.RequestError as exc:
        raise ModelDetailsError(f"GMI model-details request failed: {exc}") from exc

    try:
        details = response.json()
    except ValueError as exc:
        raise ModelDetailsError("GMI returned invalid JSON for model details") from exc

    if not isinstance(details, dict):
        raise ModelDetailsError("GMI returned an invalid model-details document")

    return details


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read a live GMICloud model schema and verify explicit "
            "video-to-video support without submitting a generation request."
        )
    )
    parser.add_argument(
        "--model",
        help="Model slug to inspect. Defaults to GENBLAZE_VIDEO_MODEL.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("GMI_BASE_URL", DEFAULT_GMI_BASE_URL),
        help="GMI request-queue API base URL.",
    )
    parser.add_argument(
        "--org-id",
        default=os.getenv("GMI_ORG_ID", ""),
        help="Optional GMI organization ID header.",
    )
    parser.add_argument(
        "--timeout",
        default=30.0,
        type=float,
        help="HTTP timeout in seconds. Defaults to 30.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable JSON report.",
    )
    return parser.parse_args(argv)


def print_human_report(report: ModelCapabilityReport) -> None:
    video_parameters = ",".join(report.video_input_parameters) or "none"
    required_parameters = ",".join(report.required_video_input_parameters) or "none"
    print("GMICloud model capability check")
    print(f"model={report.model}")
    print(f"status={report.status or 'unknown'}")
    print(f"model_type={report.model_type or 'unknown'}")
    print(f"input_modalities={','.join(report.input_modalities) or 'none'}")
    print(f"output_modalities={','.join(report.output_modalities) or 'none'}")
    print(f"video_input_parameters={video_parameters}")
    print(f"required_video_input_parameters={required_parameters}")
    print(
        "video_to_video_verified="
        f"{'true' if report.video_to_video_verified else 'false'}"
    )
    print(f"reason={report.reason}")
    if report.brief_description:
        print(f"description={report.brief_description}")
    if report.pricing_details:
        print(f"pricing={report.pricing_details}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    api_key = settings.genblaze_gmi_api_key.strip()
    if not api_key:
        print("GMI_API_KEY is not configured")
        return EXIT_NOT_CONFIGURED

    model = (args.model or settings.genblaze_video_model).strip()
    headers = {"Authorization": f"Bearer {api_key}"}
    if args.org_id.strip():
        headers["X-Organization-ID"] = args.org_id.strip()

    try:
        with httpx.Client(
            headers=headers,
            timeout=args.timeout,
            follow_redirects=True,
        ) as client:
            details = fetch_model_details(
                client=client,
                base_url=args.base_url,
                model=model,
            )
        report = analyze_model_details(details, requested_model=model)
    except (ModelDetailsError, ValueError) as exc:
        print(f"Video model capability check failed: {exc}")
        return EXIT_REQUEST_FAILED

    if args.json:
        print(json.dumps(report.as_json_dict(), indent=2, sort_keys=True))
    else:
        print_human_report(report)

    return EXIT_VERIFIED if report.video_to_video_verified else EXIT_NOT_VIDEO_TO_VIDEO


if __name__ == "__main__":
    raise SystemExit(main())
