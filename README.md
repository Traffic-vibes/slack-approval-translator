# slack-approval-translator

Python Slack bot MVP for preparing Russian-to-English translations in Slack without posting them to a channel.

## What it does

- `/tr Russian text` translates Russian into natural business English.
- The English text is shown only to you as an ephemeral preview.
- Click `Done` to close the preview after copying the text you need.
- Click `Softer` to make the current English preview more polite, friendly, and natural.
- Click `Shorter` to make the current English preview shorter while preserving the meaning.
- Click `Cancel` to discard it.
- If `TARGET_USER_ID` is configured, incoming English messages are translated into Russian and sent as private ephemeral messages to that user.

The bot never posts translations to a channel.

## Requirements

- Python 3.10 or newer
- A Slack workspace where you can create and install an app
- An OpenAI API key

## 1. Create the Slack app

1. Open https://api.slack.com/apps and choose `Create New App`.
2. Choose `From scratch`, name it `slack-approval-translator`, and select your workspace.
3. Go to `Settings` -> `Basic Information`.
4. Under `App-Level Tokens`, create a token with the `connections:write` scope.
5. Copy the generated `xapp-...` token. This is `SLACK_APP_TOKEN`.
6. Go to `Settings` -> `Socket Mode` and turn on `Enable Socket Mode`.
7. Go to `Features` -> `OAuth & Permissions`.
8. Add these bot token scopes:
   - `commands`
   - `chat:write`
   - `channels:history`
   - `groups:history`
   - `im:history`
   - `mpim:history`
9. Click `Install to Workspace`.
10. Copy the `Bot User OAuth Token` that starts with `xoxb-`. This is `SLACK_BOT_TOKEN`.

## 2. Add slash commands

In your Slack app settings, go to `Features` -> `Slash Commands` and create this command:

| Command | Short description |
| --- | --- |
| `/tr` | Translate Russian to approved English |

With Socket Mode enabled, Slack delivers slash commands through the bot's WebSocket connection, so you do not need to run a public web server.

## 3. Add event subscriptions

Go to `Features` -> `Event Subscriptions` and turn on events.

Under `Subscribe to bot events`, add:

- `message.channels`
- `message.groups`
- `message.im`
- `message.mpim`

Reinstall the app if Slack asks you to apply new scopes or event subscriptions.

## 4. Add interactivity

Go to `Features` -> `Interactivity & Shortcuts` and turn on interactivity.

With Socket Mode enabled, button clicks are also delivered through the WebSocket connection.

## 5. Set up the project

Open a terminal in this folder and run:

```bash
python -m venv .venv
```

On Windows:

```powershell
.\.venv\Scripts\Activate.ps1
```

On macOS or Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## 6. Create your local `.env` file

Make a copy of `.env.example` named `.env`.

Fill in the values:

```bash
OPENAI_API_KEY=sk-proj-your-real-openai-api-key
OPENAI_MODEL=gpt-4o-mini
SLACK_BOT_TOKEN=xoxb-your-real-slack-bot-token
SLACK_APP_TOKEN=xapp-your-real-slack-app-token
TARGET_USER_ID=U1234567890
```

Set `TARGET_USER_ID` to the Slack user ID that should receive automatic incoming English-to-Russian translations. Leave it empty to disable automatic incoming translation.

Do not commit `.env`. It contains real API keys and tokens.

## 7. Run the bot

```bash
python app.py
```

Keep this terminal open while you use the bot in Slack.

## 8. Try it in Slack

In a channel where the app is installed or invited:

```text
/tr <paste Russian text here>
```

Slack will show you a private English preview with `Done`, `Softer`, `Shorter`, and `Cancel` buttons.

When `TARGET_USER_ID` is set, regular English messages in subscribed channels, private channels, DMs, and group DMs are also translated to Russian for that configured user. The bot skips bot messages, slash-command-like messages, and messages that contain Cyrillic text.

## Glossary

The bot prompt includes these ad-tech terms:

ROAS, CPA, CPI, CTR, CPC, CPM, GEO, geo, creative, creatives, build, campaign, adset, offer, landing, pixel, event, purchase, lead, install, UA, traffic.
