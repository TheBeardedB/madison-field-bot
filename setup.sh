#!/bin/bash

echo "Setting up Madison Field Status Bot..."

echo
echo "Creating virtual environment..."
python3 -m venv venv

echo
echo "Activating virtual environment..."
source venv/bin/activate

echo
echo "Installing dependencies..."
pip install -r requirements.txt

echo
echo "Setup complete!"
echo
echo "Next steps:"
echo "1. Copy .env.example to .env"
echo "2. Edit .env with your Discord bot token and channel ID"
echo "3. Run: python verify_setup.py to check your configuration"
echo "4. Run the bot with: ./run_bot.sh"
echo

# Make run script executable
chmod +x run_bot.sh

echo "Scripts are now executable."
