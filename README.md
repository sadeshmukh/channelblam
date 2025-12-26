# CHANNELBLAM

See that user in Slack? BLAM. Now you don't.

`/blam` them. Now they're gone. From your channel. Forever. Until you choose to `/blam remove` them, at least.

Use `/blam list` to find out who you've BLAMMED so far.

## Environment

Set the following variables:

```sh
export SLACK_APP_TOKEN=xapp-...
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_SIGNING_SECRET=your-signing-secret
```

## Run (with uv)

```sh
uv sync && uv run python main.py
```
