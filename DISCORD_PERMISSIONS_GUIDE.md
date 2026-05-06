# Discord Permission Troubleshooting Guide

## Error: 403 Forbidden (error code: 50013): Missing Permissions

This error occurs when the bot tries to send a message but lacks the necessary Discord permissions.

## Quick Troubleshooting Steps

### 1. Verify Channel ID
- Open Discord in Developer Mode (User Settings → Advanced → Developer Mode)
- Right-click your target channel → "Copy Channel ID"
- Compare with the DISCORD_CHANNEL_ID in your .env file
- Make sure they match exactly

### 2. Check Bot Permissions in Discord

#### Server-Level Permissions (Bot Role):
- Go to Server Settings → Roles
- Find your bot's role
- Ensure it has these permissions:
  - ✅ Send Messages
  - ✅ Embed Links
  - ✅ Read Message History
  - ✅ View Channel

#### Channel-Level Permissions:
- Go to your target channel settings (right-click channel → Edit Channel)
- Go to Permissions tab
- Find your bot or its role
- Ensure these permissions are enabled (green):
  - ✅ View Channel
  - ✅ Send Messages
  - ✅ Embed Links
  - ✅ Read Message History

### 3. Common Issues

**Bot not in server**: 
- Use this invite link format: `https://discord.com/oauth2/authorize?client_id=YOUR_BOT_CLIENT_ID&permissions=52224&scope=bot%20applications.commands`
- Replace YOUR_BOT_CLIENT_ID with your actual bot client ID
- This grants: Send Messages, Embed Links, Read Message History, Use Slash Commands

**Wrong channel**: 
- Bot can't send to DM channels
- Bot must be in the same server as the channel
- Channel ID must be from a text channel the bot can access

**Role hierarchy**: 
- Bot's role should be above any roles it needs to mention
- If using role pings, bot needs "Mention Everyone" permission

### 4. Test Bot Permissions

You can test if the bot can access the channel by using the new slash command:
```
/field_help
```

If this works, the bot has basic access. If it doesn't appear, there's a deeper permission issue.

### 5. Re-invite Bot with Correct Permissions

If you're still having issues, re-invite the bot:

1. Go to Discord Developer Portal
2. Select your application → OAuth2 → URL Generator
3. Select scopes: ☑️ bot ☑️ applications.commands
4. Select permissions:
   - Send Messages
   - Embed Links  
   - Read Message History
   - Use Slash Commands
   - Mention Everyone (if using role pings)
5. Copy the generated URL and use it to re-invite the bot

### 6. Verify .env Configuration

Your .env file should look like:
```
DISCORD_BOT_TOKEN=your_actual_bot_token_here
DISCORD_CHANNEL_ID=1234567890123456789
```

- Token should start with `MT` or `MTE` and be about 70 characters long
- Channel ID should be 17-19 digits, numbers only

### 7. Check Bot Status

Ensure the bot is:
- ✅ Online in the server (shows as online in member list)
- ✅ Has a role that allows it to see and send to the channel
- ✅ Not banned or restricted in any way

## Testing Commands

Once permissions are fixed, test with:
- `/field_help` - Should work if basic permissions are correct
- `/field_history` - Tests if bot can access its data and respond
- `/repost_status` - Tests if bot can send messages to channels

## Still Having Issues?

If you're still getting permission errors:
1. Check the bot logs for more specific error details
2. Try the bot in a different channel to isolate the issue
3. Make sure you're the server admin or have permission to manage bot roles
4. Verify the bot token is correct and hasn't been regenerated
