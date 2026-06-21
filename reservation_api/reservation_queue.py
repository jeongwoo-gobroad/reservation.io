from __future__ import annotations

import json

from .config import Settings


class ReservationQueueError(RuntimeError):
    pass


def enqueue_reservation_request(request_uuid: str, settings: Settings, worker_url: str) -> str:
    if settings.reservation_queue_backend in {"inline", "none", ""}:
        return ""
    if settings.reservation_queue_backend != "cloud_tasks":
        raise ReservationQueueError(f"unsupported reservation queue backend: {settings.reservation_queue_backend}")
    if not worker_url:
        raise ReservationQueueError("RESERVATION_WORKER_URL is required when RESERVATION_QUEUE_BACKEND=cloud_tasks")

    try:
        from google.api_core import exceptions as google_exceptions
        from google.cloud import tasks_v2
    except Exception as exc:
        raise ReservationQueueError("google-cloud-tasks is not installed") from exc

    project_id = settings.cloud_tasks_project_id or settings.project_id
    if not project_id:
        raise ReservationQueueError("CLOUD_TASKS_PROJECT_ID or GCP_PROJECT is required")
    if not settings.cloud_tasks_location:
        raise ReservationQueueError("CLOUD_TASKS_LOCATION is required")
    if not settings.cloud_tasks_queue:
        raise ReservationQueueError("CLOUD_TASKS_QUEUE is required")

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(project_id, settings.cloud_tasks_location, settings.cloud_tasks_queue)
    task_name = client.task_path(project_id, settings.cloud_tasks_location, settings.cloud_tasks_queue, f"reservation-{request_uuid}")
    headers = {"Content-Type": "application/json"}
    if settings.reservation_worker_secret:
        headers["X-Reservation-Worker-Secret"] = settings.reservation_worker_secret
    http_request: dict = {
        "http_method": tasks_v2.HttpMethod.POST,
        "url": worker_url,
        "headers": headers,
        "body": json.dumps({"request_uuid": request_uuid}, separators=(",", ":")).encode("utf-8"),
    }
    if settings.cloud_tasks_service_account_email:
        http_request["oidc_token"] = {
            "service_account_email": settings.cloud_tasks_service_account_email,
            "audience": settings.cloud_tasks_audience or worker_url,
        }
    try:
        response = client.create_task(request={"parent": parent, "task": {"name": task_name, "http_request": http_request}})
    except google_exceptions.AlreadyExists:
        return task_name
    except Exception as exc:
        raise ReservationQueueError(str(exc)) from exc
    return response.name
