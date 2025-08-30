"""Telemetry message formatter for teltubby bot.

Provides formatted, emoji-rich messages for better readability in Telegram UI.
"""

from typing import List, Optional
from dataclasses import dataclass


@dataclass
class TelemetryData:
    """Data structure for telemetry information."""
    files_count: int
    media_types: List[str]
    base_path: str
    dedup_count: int
    total_bytes: int
    skipped_count: int
    processing_time: Optional[float] = None
    minio_used_percent: Optional[float] = None
    bot_mode: Optional[str] = None


class TelemetryFormatter:
    """Formats telemetry data into readable Telegram messages with emojis."""
    
    # Emoji constants for consistent usage
    EMOJIS = {
        "success": "✅",
        "info": "ℹ️",
        "warning": "⚠️",
        "error": "❌",
        "storage": "💾",
        "files": "📁",
        "photo": "🖼️",
        "video": "🎥",
        "document": "📄",
        "audio": "🎵",
        "duplicate": "🔄",
        "skipped": "⏭️",
        "time": "⏱️",
        "mode": "🔧",
        "quota": "📊",
        "database": "🗄️",
        "archive": "📦",
        "bot": "🤖",
        "minio": "☁️",
        "stats": "📈"
    }
    
    @classmethod
    def format_ingestion_ack(cls, data: TelemetryData) -> str:
        """Format ingestion acknowledgment with rich formatting."""
        # Convert bytes to human readable format
        size_str = cls._format_bytes(data.total_bytes)
        
        # Format media types with emojis
        type_emojis = {
            "photo": cls.EMOJIS["photo"],
            "video": cls.EMOJIS["video"], 
            "document": cls.EMOJIS["document"],
            "audio": cls.EMOJIS["audio"]
        }
        formatted_types = []
        for media_type in data.media_types:
            emoji = type_emojis.get(media_type, "📄")
            formatted_types.append(f"{emoji}{media_type}")
        
        # Build the formatted message
        lines = [
            f"{cls.EMOJIS['success']} **Archive Complete!** {cls.EMOJIS['archive']}",
            "",
            f"{cls.EMOJIS['files']} **Files Processed:** {data.files_count}",
            f"{cls.EMOJIS['storage']} **Media Types:** {' '.join(formatted_types)}",
            f"{cls.EMOJIS['storage']} **Total Size:** {size_str}",
            f"{cls.EMOJIS['minio']} **Storage Path:** `{data.base_path}`"
        ]
        
        # Add deduplication info if applicable
        if data.dedup_count > 0:
            lines.append(f"{cls.EMOJIS['duplicate']} **Duplicates Skipped:** {data.dedup_count}")
        
        # Add skipped items info if applicable
        if data.skipped_count > 0:
            lines.append(f"{cls.EMOJIS['skipped']} **Items Skipped:** {data.skipped_count}")
        
        # Add processing time if available
        if data.processing_time:
            lines.append(f"{cls.EMOJIS['time']} **Processing Time:** {data.processing_time:.2f}s")
        
        return "\n".join(lines)
    
    @classmethod
    def format_status(cls, mode: str, minio_used: Optional[float]) -> str:
        """Format status command response."""
        lines = [
            f"{cls.EMOJIS['bot']} **Teltubby Status** {cls.EMOJIS['info']}",
            "",
            f"{cls.EMOJIS['mode']} **Mode:** {mode}",
        ]
        
        if minio_used is not None:
            # Color code based on usage level
            if minio_used >= 0.9:
                emoji = cls.EMOJIS["warning"]
                status = "Critical"
            elif minio_used >= 0.8:
                emoji = cls.EMOJIS["warning"]
                status = "High"
            elif minio_used >= 0.6:
                emoji = cls.EMOJIS["info"]
                status = "Moderate"
            else:
                emoji = cls.EMOJIS["success"]
                status = "Good"
            
            usage_text = f"{minio_used*100:.1f}% ({status})"
            lines.append(f"{cls.EMOJIS['minio']} **Storage Usage:** {emoji} {usage_text}")
        else:
            lines.append(f"{cls.EMOJIS['minio']} **Storage Usage:** Unknown")
        
        return "\n".join(lines)
    
    @classmethod
    def format_quota(cls, used_ratio: float) -> str:
        """Format quota command response."""
        percentage = used_ratio * 100
        
        # Determine status and emoji based on usage
        if percentage >= 100:
            emoji = cls.EMOJIS["error"]
            status = "FULL - Ingestion Paused"
        elif percentage >= 90:
            emoji = cls.EMOJIS["warning"]
            status = "Critical - Consider cleanup"
        elif percentage >= 80:
            emoji = cls.EMOJIS["warning"]
            status = "High - Monitor closely"
        elif percentage >= 60:
            emoji = cls.EMOJIS["info"]
            status = "Moderate"
        else:
            emoji = cls.EMOJIS["success"]
            status = "Healthy"
        
        lines = [
            f"{cls.EMOJIS['quota']} **Storage Quota Status** {emoji}",
            "",
            f"{cls.EMOJIS['minio']} **Bucket Usage:** {percentage:.1f}%",
            f"{cls.EMOJIS['info']} **Status:** {status}"
        ]
        
        return "\n".join(lines)
    
    @classmethod
    def format_start(cls) -> str:
        """Format start command response."""
        return (
            f"{cls.EMOJIS['bot']} **Welcome to Teltubby!** {cls.EMOJIS['archive']}\n\n"
            f"{cls.EMOJIS['info']} I'm your Telegram media archiver. Here's what I do:\n\n"
            f"• {cls.EMOJIS['files']} Archive forwarded/copied messages to MinIO\n"
            f"• {cls.EMOJIS['storage']} Store media with deterministic filenames\n"
            f"• {cls.EMOJIS['duplicate']} Automatically deduplicate content\n"
            f"• {cls.EMOJIS['archive']} Generate structured JSON metadata\n\n"
            f"{cls.EMOJIS['info']} **Commands:**\n"
            f"• `/status` - Check bot status and storage\n"
            f"• `/quota` - View storage quota usage\n"
            f"• `/mode` - Show current operation mode\n"
            f"• `/db_maint` - Database maintenance\n\n"
            f"{cls.EMOJIS['success']} Just forward or copy messages to me in DM!"
        )
    
    @classmethod
    def format_db_maint(cls) -> str:
        """Format database maintenance command response."""
        return (
            f"{cls.EMOJIS['database']} **Database Maintenance** {cls.EMOJIS['success']}\n\n"
            f"{cls.EMOJIS['info']} VACUUM operation completed successfully!\n"
            f"{cls.EMOJIS['storage']} Database optimized and cleaned up."
        )
    
    @classmethod
    def format_mode(cls, mode: str) -> str:
        """Format mode command response."""
        mode_emoji = "🌐" if mode == "webhook" else "📡"
        return (
            f"{cls.EMOJIS['mode']} **Operation Mode** {mode_emoji}\n\n"
            f"{cls.EMOJIS['info']} Current mode: **{mode}**\n"
            f"{cls.EMOJIS['info']} {'Webhook mode with external endpoint' if mode == 'webhook' else 'Long polling mode for development'}"
        )
    
    @classmethod
    def format_quota_pause(cls) -> str:
        """Format quota pause message."""
        return (
            f"{cls.EMOJIS['warning']} **Ingestion Paused** {cls.EMOJIS['storage']}\n\n"
            f"{cls.EMOJIS['error']} Storage bucket is at 100% capacity.\n"
            f"{cls.EMOJIS['info']} Please free up space or increase quota to resume archiving."
        )
    
    @classmethod
    def format_ingestion_failed(cls) -> str:
        """Format ingestion failure message."""
        return (
            f"{cls.EMOJIS['error']} **Ingestion Failed** {cls.EMOJIS['warning']}\n\n"
            f"{cls.EMOJIS['info']} Something went wrong while processing your media.\n"
            f"{cls.EMOJIS['info']} Please try again or contact support if the issue persists."
        )
    
    @classmethod
    def _format_bytes(cls, bytes_value: int) -> str:
        """Convert bytes to human readable format."""
        if bytes_value < 1024:
            return f"{bytes_value} B"
        elif bytes_value < 1024 * 1024:
            return f"{bytes_value / 1024:.1f} KB"
        elif bytes_value < 1024 * 1024 * 1024:
            return f"{bytes_value / (1024 * 1024):.1f} MB"
        else:
            return f"{bytes_value / (1024 * 1024 * 1024):.1f} GB"
