"""Test helpers shared by the test modules."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .const import (
    CHANNELS_OK,
    CHANNELS_URL,
    NOTIFICATION_OK,
    NOTIFICATION_URL,
    NOTIFY_OK,
    NOTIFY_URL,
    PING_OK,
    PING_URL,
    RECIPIENTS_OK,
    RECIPIENTS_URL,
    WEBHOOK_URL,
)


def mock_addon(
    aioclient_mock: AiohttpClientMocker,
    *,
    ping: dict[str, Any] | None = None,
    webhook_status: HTTPStatus = HTTPStatus.NO_CONTENT,
    recipients: dict[str, Any] | None = None,
    recipients_status: HTTPStatus = HTTPStatus.OK,
    notify: dict[str, Any] | None = None,
    channels: dict[str, Any] | None = None,
    notification: dict[str, Any] | None = None,
    **ping_kwargs: Any,
) -> AiohttpClientMocker:
    """Replace every add-on endpoint mock. Defaults: a current, healthy add-on.

    `ping_kwargs` (e.g. `status=`, `exc=`) override the ping response. `notify`,
    `channels` and `notification` override the `POST /notify`, `GET /channels` and
    `GET /notifications/{NOTIFICATION_ID}` mocks (default: their `*_OK` bodies).
    """
    aioclient_mock.clear_requests()
    aioclient_mock.get(PING_URL, **(ping_kwargs or {"json": ping or PING_OK}))
    aioclient_mock.put(WEBHOOK_URL, status=webhook_status)
    aioclient_mock.delete(WEBHOOK_URL, status=webhook_status)
    body = recipients or RECIPIENTS_OK
    if recipients_status == HTTPStatus.OK:
        aioclient_mock.get(
            RECIPIENTS_URL, json=body, headers={"ETag": f'"{body["revision"]}"'}
        )
    else:
        aioclient_mock.get(RECIPIENTS_URL, status=recipients_status)
    aioclient_mock.post(
        NOTIFY_URL, **(notify or {"status": HTTPStatus.ACCEPTED, "json": NOTIFY_OK})
    )
    aioclient_mock.get(CHANNELS_URL, **(channels or {"json": CHANNELS_OK}))
    aioclient_mock.get(NOTIFICATION_URL, **(notification or {"json": NOTIFICATION_OK}))
    return aioclient_mock


def calls(
    aioclient_mock: AiohttpClientMocker, method: str, url: str
) -> list[tuple[Any, Any, Any, Any]]:
    """Return the recorded calls for one method and URL."""
    return [
        call
        for call in aioclient_mock.mock_calls
        if call[0].lower() == method.lower() and str(call[1]) == url
    ]
