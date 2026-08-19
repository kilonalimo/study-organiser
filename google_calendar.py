"""
Everything related to talking to Google Calendar lives here, so app.py
doesn't need to know the details of OAuth or the Calendar API.

How this fits together:
- We register the app with Google (Client ID + Secret, set up once in
  Google Cloud Console).
- The user visits /connect-google, which sends them to Google's consent
  screen. Google redirects back to /oauth2callback with a code.
- We exchange that code for an access token (short-lived) and a refresh
  token (long-lived — lets us get new access tokens later without
  asking the user to log in again).
- We create (or reuse) a calendar named "Wisteria" in their account and
  remember its id, so every synced task lands there rather than in
  their main calendar.
"""

import os
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import db

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_NAME = "Wisteria"


def _client_config():
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI")
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }, redirect_uri


def is_configured():
    """Whether the app itself has Google credentials set up (via .env)."""
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def is_connected():
    """Whether Ilona has connected her Google account."""
    return db.get_google_auth() is not None


def build_authorization_url():
    config, redirect_uri = _client_config()
    flow = Flow.from_client_config(config, scopes=SCOPES, redirect_uri=redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type="offline",       # ask for a refresh token
        include_granted_scopes="true",
        prompt="consent",            # make sure we actually get a refresh token
    )
    return auth_url, state


def handle_oauth_callback(authorization_response_url, state):
    config, redirect_uri = _client_config()
    flow = Flow.from_client_config(config, scopes=SCOPES, redirect_uri=redirect_uri, state=state)
    flow.fetch_token(authorization_response=authorization_response_url)
    creds = flow.credentials

    service = build("calendar", "v3", credentials=creds)
    calendar_id = _find_or_create_wisteria_calendar(service)

    db.save_google_auth(
        access_token=creds.token,
        refresh_token=creds.refresh_token,
        token_expiry=creds.expiry.isoformat() if creds.expiry else None,
        calendar_id=calendar_id,
    )


def _find_or_create_wisteria_calendar(service):
    calendar_list = service.calendarList().list().execute()
    for entry in calendar_list.get("items", []):
        if entry.get("summary") == CALENDAR_NAME:
            return entry["id"]

    created = service.calendars().insert(body={"summary": CALENDAR_NAME}).execute()
    return created["id"]


def _get_credentials():
    """Load stored tokens and refresh them if needed. Returns None if not connected."""
    auth = db.get_google_auth()
    if auth is None:
        return None

    config, _ = _client_config()
    creds = Credentials(
        token=auth["access_token"],
        refresh_token=auth["refresh_token"],
        token_uri=config["web"]["token_uri"],
        client_id=config["web"]["client_id"],
        client_secret=config["web"]["client_secret"],
        scopes=SCOPES,
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        db.save_google_auth(
            access_token=creds.token,
            refresh_token=creds.refresh_token,
            token_expiry=creds.expiry.isoformat() if creds.expiry else None,
            calendar_id=auth["calendar_id"],
        )

    return creds


def _get_service_and_calendar():
    creds = _get_credentials()
    if creds is None:
        return None, None
    service = build("calendar", "v3", credentials=creds)
    calendar_id = db.get_google_auth()["calendar_id"]
    return service, calendar_id


def sync_task(task):
    """
    Create or update the Google Calendar event for a task's planned_date.
    If the task has no planned_date, remove any existing event instead.
    Silently does nothing if Google isn't connected. Never raises —
    a Calendar hiccup should never break the main app.
    """
    try:
        service, calendar_id = _get_service_and_calendar()
        if service is None:
            return

        if not task["planned_date"]:
            _delete_event_if_any(service, calendar_id, task)
            return

        event_body = _event_body_for_task(task)

        if task["google_event_id"]:
            try:
                service.events().update(
                    calendarId=calendar_id, eventId=task["google_event_id"], body=event_body
                ).execute()
                return
            except HttpError as e:
                if e.resp.status != 404:
                    raise
                # event was deleted on the Google side — fall through and recreate it

        created = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        db.set_google_event_id(task["id"], created["id"])
    except Exception:
        # Don't let a Google API problem break task creation/editing.
        pass


def delete_task_event(task):
    try:
        service, calendar_id = _get_service_and_calendar()
        if service is None:
            return
        _delete_event_if_any(service, calendar_id, task)
    except Exception:
        pass


def _delete_event_if_any(service, calendar_id, task):
    if not task["google_event_id"]:
        return
    try:
        service.events().delete(calendarId=calendar_id, eventId=task["google_event_id"]).execute()
    except HttpError as e:
        if e.resp.status != 404 and e.resp.status != 410:
            raise
    db.set_google_event_id(task["id"], None)


def _event_body_for_task(task):
    start = task["planned_date"]
    end = (datetime.fromisoformat(start) + timedelta(days=1)).date().isoformat()

    details = task["role"]
    if task["course"]:
        details += f" · {task['course']}"
    if task["due_date"]:
        details += f"\nDue {task['due_date']}"

    return {
        "summary": task["title"],
        "description": details,
        "start": {"date": start},
        "end": {"date": end},
    }
