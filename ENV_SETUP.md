# Environment Setup Troubleshooting

## Quick Fix for Environment Variable Errors

If you're getting errors about missing environment variables, follow these steps:

### 1. Create the .env file
```bash
# In the project directory (madison-field-bot/)
cp .env.example .env
```

### 2. Edit the .env file
Open `.env` in any text editor and replace the placeholder values:

```
DISCORD_BOT_TOKEN=your_actual_bot_token_here
DISCORD_CHANNEL_ID=your_actual_channel_id_here
```

### 3. Get Your Bot Token
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your application
3. Go to "Bot" section
4. Copy the token under "Token"

### 4. Get Your Channel ID
1. Enable Developer Mode in Discord (User Settings → Advanced → Developer Mode)
2. Right-click on your target channel
3. Select "Copy Channel ID"

### 5. File Location
The `.env` file must be in the same directory as `field_status_bot.py`:

```
madison-field-bot/
├── field_status_bot.py
├── .env                    ← Your .env file goes HERE
├── .env.example           
└── requirements.txt
```

### 6. Example .env File
```
DISCORD_BOT_TOKEN=your_bot_token_here
DISCORD_CHANNEL_ID=1234567890123456789
```

### 7. Common Issues

**"No such file or directory: '.env'"**
- The .env file doesn't exist
- Create it by copying .env.example

**"DISCORD_BOT_TOKEN environment variable not set"**  
- The .env file exists but is empty or has wrong format
- Make sure there are no spaces around the = sign
- Make sure you saved the file after editing

**"DISCORD_CHANNEL_ID environment variable not set"**
- Same as above, but for the channel ID
- Channel ID must be numbers only (no # symbol)

### 8. Testing Your Setup
Run this to check if your environment variables are loaded:
```bash
# Linux/Mac
source venv/bin/activate
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print('Token set:', bool(os.getenv('DISCORD_BOT_TOKEN'))); print('Channel set:', bool(os.getenv('DISCORD_CHANNEL_ID')))"

# Windows  
venv\Scripts\activate.bat
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print('Token set:', bool(os.getenv('DISCORD_BOT_TOKEN'))); print('Channel set:', bool(os.getenv('DISCORD_CHANNEL_ID')))"
```

Should output:
```
Token set: True
Channel set: True
```

If it shows `False`, your .env file isn't set up correctly.
