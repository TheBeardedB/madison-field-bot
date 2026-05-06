@echo off
echo Starting Madison Field Status Bot...

if not exist .env (
    echo ERROR: .env file not found!
    echo Please create a .env file with your configuration:
    echo.
    echo Steps:
    echo 1. Copy the example: copy .env.example .env
    echo 2. Edit .env with your Discord bot token and channel ID
    echo.
    echo Example .env content:
    echo DISCORD_BOT_TOKEN=your_bot_token_here
    echo DISCORD_CHANNEL_ID=your_channel_id_here
    echo.
    pause
    exit /b 1
)

echo Activating virtual environment...
call venv\Scripts\activate.bat

if errorlevel 1 (
    echo ERROR: Failed to activate virtual environment!
    echo Please run setup.bat first to create the virtual environment.
    pause
    exit /b 1
)

echo Starting bot...
python field_status_bot.py

pause
