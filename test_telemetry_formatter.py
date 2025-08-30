#!/usr/bin/env python3
"""Test script for telemetry formatter."""

import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from teltubby.utils.telemetry_formatter import (
    TelemetryFormatter, 
    TelemetryData
)


def test_telemetry_formatter():
    """Test the telemetry formatter functionality."""
    print("🧪 Testing Telemetry Formatter...\n")
    
    # Test ingestion acknowledgment
    print("📋 Testing Ingestion Acknowledgment:")
    telemetry_data = TelemetryData(
        files_count=3,
        media_types=["photo", "video", "document"],
        base_path="teltubby/2024/01/chat_slug/123/",
        dedup_count=1,
        total_bytes=5242880,
        skipped_count=0
    )
    
    ack = TelemetryFormatter.format_ingestion_ack(telemetry_data)
    print(ack)
    print()
    
    # Test status
    print("📊 Testing Status:")
    status = TelemetryFormatter.format_status("webhook", 0.75)
    print(status)
    print()
    
    # Test quota
    print("💾 Testing Quota:")
    quota = TelemetryFormatter.format_quota(0.85)
    print(quota)
    print()
    
    # Test start command
    print("🚀 Testing Start Command:")
    start = TelemetryFormatter.format_start()
    print(start)
    print()
    
    # Test mode command
    print("🔧 Testing Mode Command:")
    mode = TelemetryFormatter.format_mode("webhook")
    print(mode)
    print()
    
    # Test database maintenance
    print("🗄️ Testing Database Maintenance:")
    db_maint = TelemetryFormatter.format_db_maint()
    print(db_maint)
    print()
    
    # Test quota pause
    print("⚠️ Testing Quota Pause:")
    quota_pause = TelemetryFormatter.format_quota_pause()
    print(quota_pause)
    print()
    
    # Test ingestion failed
    print("❌ Testing Ingestion Failed:")
    failed = TelemetryFormatter.format_ingestion_failed()
    print(failed)
    print()
    
    print("✅ All tests completed!")


if __name__ == "__main__":
    test_telemetry_formatter()
