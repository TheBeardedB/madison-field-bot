# Madison Field Status Discord Bot

A lightweight, stable Discord bot that monitors Madison, Alabama recreation center field status via RSS feed and posts intelligent updates to Discord channels. **Features modern slash commands for easy management!**

## Features

### 🎯 Smart RSS Monitoring
- **Normal interval**: Every 20 minutes
- **Peak times**: Every 2 minutes during:
  - 2:30-3:30 PM CST on weekdays (typical update time)
  - 7:30-8:30 AM CST on weekends
- **Dynamic scheduling**: Increases frequency around expected update times parsed from content

### 🧠 Intelligent Status Detection
- Parses field status: Open, Partially Open, Closed
- Identifies specific closed fields (Palmer Park, Dublin Park, Soccer Fields, etc.)
- Detects soccer field closures specifically

### 📢 Automated Discord Posting
Posts updates when:
1. **Fields reopen** after being closed
2. **Soccer fields close** (any soccer field closure)
3. **All fields close**

### 🎨 Rich Discord Embeds
- **Green**: All fields open
- **Orange**: Some fields closed  
- **Red**: All fields closed
- Includes specific closed field details
- Shows original RSS content
- Timestamps in CST

### 💾 Persistent History
- Maintains status history in `field_status_history.json`
- Tracks changes over time
- Survives bot restarts
- Keeps last 100 status changes

### ⚡ Advanced Content Parsing
- Extracts expected update times from phrases like:
  - "Further updates at 4pm"
  - "Next update by 3:30pm"
  - "Check back around 2pm"
- Automatically adjusts monitoring frequency around expected updates

### 🎮 Modern Slash Commands
- Uses Discord's native slash command system
- Interactive dropdowns and parameter validation
- Ephemeral responses (private command results)
- Auto-complete and built-in help
- Proper permission checking

## Quick Setup

### 1. Create Discord Bot
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application and bot
3. Copy the bot token
4. Invite bot to your server with permissions:
   - Send Messages
   - Use Slash Commands (for future features)
   - Embed Links
   - Read Message History

### 2. Install Dependencies
```bash
# Navigate to project directory
cd madison-field-bot

# Create virtual environment (recommended)
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration
Create a `.env` file by copying the example:
```bash
# Copy the example file
cp .env.example .env

# Edit .env with your actual values
```

The `.env` file should contain:
```env
DISCORD_BOT_TOKEN=your_bot_token_here
DISCORD_CHANNEL_ID=your_channel_id_here
```

Alternatively, you can set environment variables directly:
```bash
# Windows
set DISCORD_BOT_TOKEN=your_bot_token_here
set DISCORD_CHANNEL_ID=your_channel_id_here

# Linux/Mac
export DISCORD_BOT_TOKEN="your_bot_token_here"
export DISCORD_CHANNEL_ID="your_channel_id_here"
```

### 4. Run the Bot
```bash
python field_status_bot.py
```

## Getting Channel ID
1. Enable Developer Mode in Discord (User Settings → Advanced → Developer Mode)
2. Right-click on your target channel
3. Select "Copy Channel ID"

## Bot Commands

The bot uses Discord's modern slash command system. All commands are invoked using `/command_name`.

### 🔔 Role Ping Management
- `/set_role_ping` - Set a role to be pinged when updates are posted to a channel (requires Administrator)
- `/list_role_pings` - Show all configured role pings (requires Manage Messages)

### 📋 Status Management  
- `/repost_status` - Repost historical status for testing embed formats (requires Manage Messages)
  - Uses dropdown selection for status type (Open, Partially Open, Closed)
- `/field_history` - Show recent status changes with optional limit parameter (requires Manage Messages)
- `/check_permissions` - Check bot permissions in channels (requires Manage Messages)

### ℹ️ Information
- `/field_help` - Show help message with all commands (available to everyone)

**Permission Requirements:**
- **Administrator**: `/set_role_ping`
- **Manage Messages**: `/repost_status`, `/field_history`, `/list_role_pings`, `/check_permissions`  
- **Everyone**: `/field_help`

## File Structure
```
madison-field-bot/
├── field_status_bot.py          # Main bot code
├── requirements.txt             # Python dependencies
├── field_status_history.json    # Status history (auto-created)
├── bot_config.json              # Bot configuration (auto-created)
├── field_status_bot.log        # Log file (auto-created)
├── .env                        # Environment configuration (create this)
├── .env.example                # Environment template
├── verify_setup.py             # Setup verification script
├── sync_dev_commands.py         # Development command sync (for faster testing)
├── clear_commands.py            # Clear slash commands (development)
├── DISCORD_PERMISSIONS_GUIDE.md # Permission troubleshooting
├── USAGE_GUIDE.md              # Command usage guide
├── ENV_SETUP.md                # Environment setup help
├── setup.bat / setup.sh         # Setup scripts
├── run_bot.bat / run_bot.sh     # Run scripts
└── README.md                   # This file
```

## Production Deployment

### Running as Windows Service
Use tools like NSSM (Non-Sucking Service Manager):
1. Download NSSM
2. Create service: `nssm install MadisonFieldBot`
3. Set Application Path to your Python executable
4. Set Arguments to full path of `field_status_bot.py`
5. Set Startup directory to project folder
6. Add environment variables in Environment tab

### Running as Linux Service
Create systemd service file `/etc/systemd/system/madison-field-bot.service`:
```ini
[Unit]
Description=Madison Field Status Discord Bot
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/path/to/madison-field-bot
Environment=DISCORD_BOT_TOKEN=your_token_here
Environment=DISCORD_CHANNEL_ID=your_channel_id_here
ExecStart=/path/to/madison-field-bot/venv/bin/python field_status_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable madison-field-bot
sudo systemctl start madison-field-bot
```

## Monitoring & Troubleshooting

### Logs
- **File**: `field_status_bot.log` (created automatically)
- **Console**: Real-time output when running directly
- **Service**: Use `sudo systemctl status madison-field-bot` on Linux

### Common Issues

**Slash Commands Not Appearing**:
- Global slash command sync can take up to 1 hour to propagate
- For immediate testing: Use `python sync_dev_commands.py` to sync to your specific server
- Check bot logs for "Successfully synced X slash commands" message
- Ensure bot has `applications.commands` scope when invited

**Duplicate Posts on Restart**:
- Fixed in latest version - bot now uses RSS `pubDate` to detect actual new updates
- Will not repost the same update after restart unless the RSS feed has a new `pubDate`

**Permission Errors (403 Forbidden)**:
- Use `/check_permissions` to diagnose permission issues
- See `DISCORD_PERMISSIONS_GUIDE.md` for detailed troubleshooting
- Ensure bot has: Send Messages, Embed Links, View Channel, Read Message History

**Setup Issues**:
- Run `python verify_setup.py` to check configuration
- Verify `.env` file contains correct bot token and channel ID

**RSS Feed Access**: The bot includes headers to bypass robots.txt restrictions. If access is still blocked, check logs for HTTP errors.

**Missing Updates**: Verify channel ID and bot permissions. Check logs for parsing errors.

**Time Zone**: Bot uses US/Central timezone automatically.

**Memory Usage**: Bot keeps only last 100 status changes in memory.

### Log Examples
```
2024-01-15 15:00:01 - INFO - Checking RSS feed (interval: 2 minutes)
2024-01-15 15:00:02 - INFO - RSS content changed, processing update
2024-01-15 15:00:02 - INFO - Next expected update: 2024-01-15 16:00:00
2024-01-15 15:00:03 - INFO - Posted update to Discord: closed
```

## Technical Details

### RSS Feed
Monitors: `https://www.madisonal.gov/RSSFeed.aspx?ModID=1&CID=Field-Status-6`

### Status Detection Algorithm
1. Parse RSS content for closure keywords
2. Extract specific field names using regex patterns
3. Determine overall status (open/partial/closed)
4. Identify soccer-specific closures
5. Compare with previous status to detect changes

### Smart Scheduling
- Base interval: 20 minutes
- Peak times: 2 minutes during expected update windows
- Dynamic adjustment based on parsed expected update times
- Timezone-aware scheduling (US/Central)

## Customization

### Adding New Fields
Edit `field_patterns` in `parse_field_status()`:
```python
field_patterns = [
    r'(palmer park?)',
    r'(dublin park?)',
    r'(your new field pattern)',
    # ...
]
```

### Modifying Post Conditions
Edit `should_post_update()` method:
```python
def should_post_update(self, status, closed_fields, contains_soccer, previous_status):
    # Add your custom conditions here
    if your_condition:
        return True
    # ...
```

### Changing Colors
Edit `STATUS_COLORS` in `__init__()`:
```python
self.STATUS_COLORS = {
    'open': 0x00FF00,      # Green
    'partial': 0xFF8C00,   # Orange  
    'closed': 0xFF0000     # Red
}
```

## Future Enhancements
The bot architecture supports easy addition of:
- Slash commands for manual status checks
- Multiple channel support
- Custom notification preferences
- Weather integration
- Database storage for larger history
- Webhook endpoints

## Support
For issues or questions:
1. Check the logs first
2. Verify configuration
3. Ensure bot permissions are correct
4. Monitor RSS feed accessibility

## License
This bot is provided as-is for monitoring public field status information. Ensure compliance with Madison Parks & Recreation's terms of service.
