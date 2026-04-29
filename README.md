# slack-approval-translator

Python Slack bot MVP for approving Russian-to-English translations before posting them to a Slack channel.

## What it does

- `/tr Russian text` translates Russian into natural business English.
- The English text is shown only to you as an ephemeral preview.
- Click `Send` to post the English message to the channel.
- Click `Cancel` to discard it.
- `/en2ru English text` returns a Russian translation as an ephemeral message.

The bot never posts `/tr` output automatically.

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
9. Click `Install to Workspace`.
10. Copy the `Bot User OAuth Token` that starts with `xoxb-`. This is `SLACK_BOT_TOKEN`.

## 2. Add slash commands

In your Slack app settings, go to `Features` -> `Slash Commands` and create two commands:

| Command | Short description |
| --- | --- |
| `/tr` | Translate Russian to approved English |
| `/en2ru` | Translate English to Russian |

With Socket Mode enabled, Slack delivers slash commands through the bot's WebSocket connection, so you do not need to run a public web server.

## 3. Add interactivity

Go to `Features` -> `Interactivity & Shortcuts` and turn on interactivity.

With Socket Mode enabled, button clicks are also delivered through the WebSocket connection.

## 4. Set up the project

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

## 5. Create your local `.env` file

Make a copy of `.env.example` named `.env`.

Fill in the values:

```bash
OPENAI_API_KEY=sk-proj-your-real-openai-api-key
OPENAI_MODEL=gpt-4o-mini
SLACK_BOT_TOKEN=xoxb-your-real-slack-bot-token
SLACK_APP_TOKEN=xapp-your-real-slack-app-token
```

Do not commit `.env`. It contains real API keys and tokens.

## 6. Run the bot

```bash
python app.py
```

Keep this terminal open while you use the bot in Slack.

## 7. Try it in Slack

In a channel where the app is installed or invited:

```text
/tr <paste Russian text here>
```

Slack will show you a private English preview with `Send` and `Cancel` buttons.

Then try:

```text
/en2ru We need to improve ROAS for this campaign.
```

Slack will show you a private Russian translation.

## Glossary

The bot prompt includes these ad-tech terms:

ROAS, CPA, CPI, CTR, CPC, CPM, GEO, geo, creative, creatives, build, campaign, adset, offer, landing, pixel, event, purchase, lead, install, UA, traffic.
