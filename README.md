# CHANNELBLAM

See that user in Slack? BLAM. Now you don't. How about locking your channel to IDV-verified members entirely? BLAM. It's done.

CHANNELBLAM is quite fast, with almost instantaneous kicking of members disallowed by any rule.

## How to Use

Invite @CHANNELBLAM to your channel. It should automatically invite @BLAMMER, but if it doesn't, do that manually. Then, grant @BLAMMER Channel Manager to allow it to kick members.

Lock your channel to IDV with `/blam idv`. Run `/blam idv test` to see how many people would be kicked beforehand (doesn't require channel manager).

BLAM a specific user with `/blam @user`, and remove them from the blamlist with `/blam remove @user`.

Would you like to exempt users from the blanket IDV bans? Run `/blam whitelist @user` or `/blam whitelist channel` (whitelist entire current member list).

## Environment

Set the following variables:

```sh
export SLACK_APP_TOKEN=xapp-...
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_SIGNING_SECRET=your-signing-secret
export SLACK_PERSONAL_TOKEN=xoxp-... # moving away from this one in the future
export SLACK_XOXC=xoxc-...
export SLACK_XOXD=xoxd-...
export ADMIN_ID=U...
```

## Run (with uv)

```sh
uv sync && uv run python main.py
```
