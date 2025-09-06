# Quick Usage Guide - Slash Commands

## Role Ping Setup

### Setting up role pings for automatic notifications:

1. **Create or identify the role** you want to ping (e.g., "Field Updates")

2. **Set role ping for a channel**:
   - Use `/set_role_ping`
   - Select the channel from the dropdown
   - Select the role from the dropdown
   - Click Submit

3. **Verify configuration**:
   ```
   /list_role_pings
   ```

4. **Remove role ping** (if needed):
   - Use `/set_role_ping`
   - Select the channel
   - Leave role field empty
   - Click Submit

Now when field status updates are posted to your selected channel, members with the selected role will be automatically pinged!

## Historical Reposting (Testing Embeds)

### Repost previous status updates to test embed formatting:

1. **Check recent history**:
   ```
   /field_history
   ```
   - Optionally specify limit (1-20, default: 5)

2. **Repost a specific status type**:
   - Use `/repost_status`
   - Select target channel from dropdown
   - Select status type from dropdown (Open, Partially Open, Closed)
   - Click Submit

This is perfect for:
- Testing embed appearance in different channels
- Showing examples to other administrators
- Verifying role ping configurations
- Tweaking bot settings without waiting for real updates

## Example Workflow

1. **Initial setup**:
   - Use `/set_role_ping` to configure #field-updates with @Field Updates role
   - Use `/set_role_ping` to configure #soccer-alerts with @Soccer Parents role

2. **Test the setup**:
   - Use `/repost_status` to post a "Closed" status to #field-updates
   - Use `/repost_status` to post a "Partially Open" status to #soccer-alerts

3. **Monitor history**:
   ```
   /field_history
   ```

4. **View all configurations**:
   ```
   /list_role_pings
   ```

## Slash Command Benefits

### ✅ **User-Friendly**
- Auto-complete and parameter hints
- Dropdown selections prevent typos
- Built-in validation
- Consistent UI across all Discord clients

### ✅ **Secure**
- Proper permission checking
- Ephemeral responses (only you see command results)
- No message parsing or prefix conflicts

### ✅ **Modern**
- Discord's recommended command system
- Better integration with Discord's UI
- Supports rich parameter types

## Permission Requirements

- **Administrator** permissions needed for:
  - `/set_role_ping`
  
- **Manage Messages** permissions needed for:
  - `/repost_status`
  - `/field_history`  
  - `/list_role_pings`

- **Any user** can use:
  - `/field_help`

## Tips

- All command responses are ephemeral (private) except for the actual field status reposts
- Role pings are sent as message content (outside the embed) so they trigger notifications properly
- Historical reposts include a note showing the original timestamp
- The bot remembers role ping configurations across restarts
- You can configure different roles for different channels
- Use `/field_help` to see all commands with descriptions
- Slash commands auto-sync when the bot starts up
