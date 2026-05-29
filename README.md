# Madison Field Status Discord Bot

A focused Discord bot that monitors the Madison Parks RSS feed, keeps a lightweight history, and maintains one live message in a channel.

## What It Does

- Reads one RSS feed only
- Edits one persistent Discord message instead of posting new messages
- Uses the embed title as a clickable link to the RSS post
- Puts the RSS content in the embed body
- Shows Palmer soccer field status for fields 1 to 5 and 7 to 10
- Shows Dublin soccer field status for fields 1 to 9
- Stores current status and history in the database

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and fill in your values:

```env
DISCORD_BOT_TOKEN=your_bot_token_here
DISCORD_CHANNEL_ID=your_channel_id_here
RSS_FEED_URL=https://www.madisonal.gov/RSSFeed.aspx?ModID=1&CID=Field-Status-6
POLL_INTERVAL_MINUTES=2
```

`RSS_FEED_URL` is optional. If you do not set it, the bot uses the default Madison field feed.

### 3. Run the Bot

```bash
python field_status_bot.py
```

## Message Format

Each update uses:

- The RSS post title as the embed title
- The RSS post link as the title hyperlink
- The RSS content as the embed description
- One field per park showing the status of each soccer field individually

## History And State

- `feed_status` keeps the current Discord message id and last processed RSS entry
- `feed_history` keeps the processed RSS history entries

## Field Mapping

- Palmer: soccer fields 1 through 5 and 7 through 10
- Dublin: soccer fields 1 through 9

## Troubleshooting

- If the bot does not update the message, verify the channel permissions
- If the bot does not see new RSS items, verify the feed URL and network access
- If the bot restarts, it will continue editing the same stored message when possible
