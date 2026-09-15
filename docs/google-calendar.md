# Google Calendar sync

Google Calendar uses OAuth 2.0. The username/password CalDAV form does not
support Google, including Google app passwords.

## Connect

1. In **Settings → Integrations**, disable any old Google CalDAV account.
2. Click **Set up Google Calendar (OAuth)**. This opens the Google Workspace
   connector, also available under **Connections**.
3. Enable **Sync Google Calendar into Calendar**.
4. For automatic token refresh, expand **Advanced OAuth and token configuration**
   and select **Custom OAuth app**:
   - Enable **Google Calendar API** in your Google Cloud project.
   - Configure the OAuth consent screen. If the app is in Testing, add your Google
     account as a test user.
   - Create an OAuth client with application type **Web application**.
   - Register the exact redirect URI shown in the connector. Use the same hostname
     and port you use to open HushClaw. If the server uses `public_base_url`, use
     that base URL followed by `/oauth/app-connectors/google_workspace/callback`.
   - Enter the Client ID and Client secret. Scopes must include
     `https://www.googleapis.com/auth/calendar.readonly`. This scope alone is
     sufficient for calendar sync.
5. Click **Connect Google Workspace**. HushClaw saves the settings before opening
   Google authorization. Choose your Google account and grant calendar read access.
6. Return to **Calendar** and click **Sync**. Background sync runs every 30 minutes.

Managed OAuth can use the broker's access token. If no matching OAuth client
credentials are available locally, expired managed tokens require reconnecting;
use a Custom OAuth app for automatic refresh. Google may also require renewed
consent when a refresh token expires or is revoked.

## What sync does

- Imports readable calendars over the past year and next two years.
- Expands recurring events, preserves all-day dates, and normalizes timed events
  to UTC for display in the selected timezone.
- Updates moved events and removes deleted/cancelled Google events after a
  complete successful fetch.
- Keeps the previous snapshot on authentication, network, parsing, or database
  errors. Manual sync displays the error.
- Keeps Google events separate from local and CalDAV events.
- Reads from Google only. Local edits are not written back and may be replaced
  by the next sync.

An API key protecting HushClaw is required when starting authorization. Google's
callback uses an expiring, single-use OAuth state; the API key is not sent to Google.

References: [Google Calendar events API](https://developers.google.com/workspace/calendar/api/v3/reference/events/list),
[Google OAuth web application setup](https://developers.google.com/identity/protocols/oauth2/web-server),
[Google CalDAV authentication requirements](https://developers.google.com/workspace/calendar/caldav/v2/guide).
