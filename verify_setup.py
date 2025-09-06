#!/usr/bin/env python3
"""
Madison Field Status Bot - Setup Verification Script
This script helps verify that your bot configuration is correct.
"""

import os
import sys
from dotenv import load_dotenv

def check_environment():
    """Check environment configuration"""
    print("🔧 Checking environment configuration...\n")
    
    # Load environment variables
    load_dotenv()
    
    # Check .env file exists
    if not os.path.exists('.env'):
        print("❌ .env file not found!")
        print("   Create one by copying .env.example:")
        print("   cp .env.example .env")
        return False
    
    print("✅ .env file found")
    
    # Check required environment variables
    token = os.getenv('DISCORD_BOT_TOKEN')
    channel_id = os.getenv('DISCORD_CHANNEL_ID')
    
    if not token:
        print("❌ DISCORD_BOT_TOKEN not set in .env file")
        return False
    
    if not channel_id:
        print("❌ DISCORD_CHANNEL_ID not set in .env file")
        return False
    
    print("✅ DISCORD_BOT_TOKEN is set")
    print("✅ DISCORD_CHANNEL_ID is set")
    
    # Validate token format
    if not (token.startswith('MT') or token.startswith('MTE')) or len(token) < 50:
        print("⚠️  Bot token format looks unusual")
        print("   Make sure you copied the token correctly from Discord Developer Portal")
    else:
        print("✅ Bot token format looks correct")
    
    # Validate channel ID format
    try:
        channel_id_int = int(channel_id)
        if len(channel_id) < 17 or len(channel_id) > 20:
            print("⚠️  Channel ID length looks unusual")
            print("   Make sure you copied the channel ID correctly")
        else:
            print("✅ Channel ID format looks correct")
    except ValueError:
        print("❌ Channel ID must be numeric")
        return False
    
    return True

def check_dependencies():
    """Check Python dependencies"""
    print("\n🐍 Checking Python dependencies...\n")
    
    required_packages = [
        'discord',
        'feedparser', 
        'aiohttp',
        'pytz',
        'dotenv'
    ]
    
    missing_packages = []
    
    for package in required_packages:
        try:
            __import__(package)
            print(f"✅ {package}")
        except ImportError:
            print(f"❌ {package} - Missing")
            missing_packages.append(package)
    
    if missing_packages:
        print(f"\n❌ Missing packages: {', '.join(missing_packages)}")
        print("   Run: pip install -r requirements.txt")
        return False
    
    return True

def check_files():
    """Check required files exist"""
    print("\n📁 Checking required files...\n")
    
    required_files = [
        'field_status_bot.py',
        'requirements.txt',
        '.env'
    ]
    
    optional_files = [
        'DISCORD_PERMISSIONS_GUIDE.md',
        'USAGE_GUIDE.md',
        'README.md',
        'sync_dev_commands.py',
        'clear_commands.py'
    ]
    
    all_good = True
    
    for file in required_files:
        if os.path.exists(file):
            print(f"✅ {file}")
        else:
            print(f"❌ {file} - Missing")
            all_good = False
    
    print("\nOptional files:")
    for file in optional_files:
        if os.path.exists(file):
            print(f"✅ {file}")
        else:
            print(f"⚠️  {file} - Not found (optional)")
    
    return all_good

def main():
    """Main verification function"""
    print("🤖 Madison Field Status Bot - Setup Verification")
    print("=" * 50)
    
    # Run all checks
    env_ok = check_environment()
    deps_ok = check_dependencies() 
    files_ok = check_files()
    
    print("\n" + "=" * 50)
    print("📋 VERIFICATION SUMMARY")
    print("=" * 50)
    
    if env_ok and deps_ok and files_ok:
        print("🎉 All checks passed! Your bot should be ready to run.")
        print("\nNext steps:")
        print("1. Make sure your Discord bot is invited to your server")
        print("2. Start the bot: ./run_bot.sh (or run_bot.bat on Windows)")
        print("3. Slash commands may take up to 1 hour to appear globally")
        print("4. For immediate testing: python sync_dev_commands.py")
        print("5. Test permissions with: /check_permissions")
        print("\n💡 Troubleshooting:")
        print("- If slash commands don't appear: Use sync_dev_commands.py")
        print("- If permission errors occur: See DISCORD_PERMISSIONS_GUIDE.md")
        print("- Bot won't repost the same update after restart (uses RSS pubDate)")
    else:
        print("❌ Some issues found. Please fix the above errors before running the bot.")
        
        if not env_ok:
            print("\n📝 Environment Issues:")
            print("   - Check your .env file configuration")
            print("   - Make sure bot token and channel ID are correct")
        
        if not deps_ok:
            print("\n📦 Dependency Issues:")
            print("   - Run: pip install -r requirements.txt")
            print("   - Make sure you're in the virtual environment")
        
        if not files_ok:
            print("\n📁 File Issues:")
            print("   - Make sure all bot files are present")
            print("   - Re-download if necessary")

if __name__ == "__main__":
    main()
