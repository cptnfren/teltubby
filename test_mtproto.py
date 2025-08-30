#!/usr/bin/env python3
"""
Test script for MTProto client functionality.
This script tests the basic MTProto client operations without requiring
full worker setup.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Add the project root to Python path
sys.path.insert(0, str(Path(__file__).parent))

from teltubby.runtime.config import AppConfig
from teltubby.mtproto.client import MTProtoClient, MTAuthHooks


async def test_mtproto_client():
    """Test the MTProto client basic functionality."""
    print("🔧 Testing MTProto Client...")
    
    # Load configuration
    try:
        config = AppConfig.from_env()
        print("✅ Configuration loaded successfully")
        print(f"   - MTProto API ID: {config.mtproto_api_id}")
        print(f"   - MTProto API Hash: {'*' * 8 if config.mtproto_api_hash else 'None'}")
        print(f"   - MTProto Phone: {'*' * 8 if config.mtproto_phone_number else 'None'}")
        print(f"   - Session Path: {config.mtproto_session_path}")
    except Exception as e:
        print(f"❌ Failed to load configuration: {e}")
        return False
    
    # Check if MTProto credentials are configured
    if not all([config.mtproto_api_id, config.mtproto_api_hash, config.mtproto_phone_number]):
        print("❌ MTProto credentials not fully configured")
        print("   Please set MTPROTO_API_ID, MTPROTO_API_HASH, and MTPROTO_PHONE_NUMBER")
        return False
    
    # Create hooks for authentication
    hooks = MTAuthHooks()
    
    # Set up request_code hook
    async def request_code():
        print("📱 Please enter the verification code sent to your phone:")
        return input("Code: ").strip()
    
    # Set up request_password hook (for 2FA)
    async def request_password():
        print("🔐 Please enter your 2FA password:")
        return input("Password: ").strip()
    
    hooks.request_code = request_code
    hooks.request_password = request_password
    
    # Create and start client
    try:
        client = MTProtoClient(config, hooks=hooks)
        print("🔄 Starting MTProto client...")
        await client.start()
        print("✅ MTProto client started successfully")
        
        # Test basic functionality
        me = await client._client.get_me()
        print(f"👤 Authenticated as: {getattr(me, 'username', 'Unknown')} (ID: {me.id})")
        
        # Test file download (you'll need to provide actual chat_id and message_id)
        print("\n📥 To test file download, you'll need to:")
        print("   1. Send a file to yourself via Telegram")
        print("   2. Note the chat_id (your user ID) and message_id")
        print("   3. Update the test_download function below")
        
        await client.stop()
        print("✅ MTProto client stopped successfully")
        return True
        
    except Exception as e:
        print(f"❌ MTProto client test failed: {e}")
        return False


async def test_download_file(chat_id: int, message_id: int):
    """Test downloading a specific file."""
    print(f"\n📥 Testing file download: chat_id={chat_id}, message_id={message_id}")
    
    try:
        config = AppConfig.from_env()
        client = MTProtoClient(config)
        await client.start()
        
        # Create temporary file
        with tempfile.NamedTemporaryFile(delete=False, prefix="mtproto_test_") as tmp_file:
            tmp_path = tmp_file.name
        
        try:
            # Download the file
            print(f"🔄 Downloading file to {tmp_path}...")
            size = await client.download_file_by_message(
                chat_id=chat_id,
                message_id=message_id,
                dest_path=tmp_path
            )
            
            print(f"✅ File downloaded successfully: {size} bytes")
            
            # Verify file
            if os.path.exists(tmp_path):
                actual_size = os.path.getsize(tmp_path)
                print(f"📁 File saved: {tmp_path}")
                print(f"📊 File size: {actual_size} bytes")
                
                if actual_size == size:
                    print("✅ File size verification passed")
                else:
                    print(f"⚠️  File size mismatch: expected {size}, got {actual_size}")
                
                # Clean up
                os.unlink(tmp_path)
                print("🧹 Temporary file cleaned up")
            else:
                print("❌ Downloaded file not found")
                
        except Exception as e:
            print(f"❌ Download failed: {e}")
            # Clean up temp file if it exists
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        
        await client.stop()
        
    except Exception as e:
        print(f"❌ Test setup failed: {e}")


async def main():
    """Main test function."""
    print("🚀 MTProto Client Test Suite")
    print("=" * 40)
    
    # Test basic client functionality
    success = await test_mtproto_client()
    
    if success:
        print("\n✅ Basic MTProto client test passed!")
        print("\n💡 To test file download, uncomment and update the line below:")
        print("   # await test_download_file(chat_id=123456789, message_id=123)")
        
        # Uncomment and update these values to test file download
        # await test_download_file(chat_id=123456789, message_id=123)
    else:
        print("\n❌ Basic MTProto client test failed!")
        print("   Please check your configuration and try again.")


if __name__ == "__main__":
    asyncio.run(main())
