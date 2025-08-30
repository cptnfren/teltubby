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
        "stats": "📈",
        "queue": "📥",
        "inspect": "🔎",
        "retry": "🔁",
        "cancel": "🛑",
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
            f"{cls.EMOJIS['info']} **All Available Commands:**\n"
            f"• `/start` - Show this welcome message\n"
            f"• `/help` - Show detailed command reference\n"
            f"• `/status` - Check bot status and system health\n"
            f"• `/quota` - View storage quota usage\n"
            f"• `/mode` - Show current operation mode\n"
            f"• `/db_maint` - Database maintenance\n"
            f"• `/mtcode <code>` - Submit MTProto verification code\n"
            f"• `/mtpass <password>` - Submit 2FA password\n"
            f"• `/mtstatus` - Check MTProto worker status\n"
            f"• `/queue` - List recent jobs in queue\n"
            f"• `/jobs <id>` - Show job details\n"
            f"• `/retry <id>` - Retry failed job\n"
            f"• `/cancel <id>` - Cancel pending job\n\n"
            f"{cls.EMOJIS['success']} Just forward or copy messages to me in DM!\n\n"
            f"{cls.EMOJIS['info']} **Tip:** Use `/help` for detailed usage instructions!"
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
    def format_jobs_queued(cls, job_ids: List[str]) -> str:
        """Format concise confirmation when jobs are queued for MTProto.

        Variables:
        - job_ids: list[str] - queued job identifiers
        """
        header = (
            f"{cls.EMOJIS['success']} **Job Queued** {cls.EMOJIS['queue']}"
            if len(job_ids) == 1
            else f"{cls.EMOJIS['success']} **Jobs Queued** {cls.EMOJIS['queue']}"
        )
        lines = [header, ""]
        
        for jid in job_ids:
            lines.append(f"• **Job ID:** `{jid}`")
            lines.append("")
            lines.append("**Available Commands:**")
            lines.append(f"🔎 **Inspect:** `/jobs {jid}`")
            lines.append(f"🔁 **Retry:** `/retry {jid}`")
            lines.append(f"🛑 **Cancel:** `/cancel {jid}`")
            lines.append("")
        
        # Add helpful tip for mobile users
        lines.append(f"{cls.EMOJIS['info']} **Mobile Tip:** Tap any command above to copy it!")
        
        return "\n".join(lines)
    
    @classmethod
    def format_mode(cls, mode: str) -> str:
        """Format mode command response."""
        mode_emoji = "🌐" if mode == "webhook" else "📡"
        webhook_desc = "Webhook mode with external endpoint"
        polling_desc = "Long polling mode for development"
        mode_desc = webhook_desc if mode == "webhook" else polling_desc
        
        return (
            f"{cls.EMOJIS['mode']} **Operation Mode** {mode_emoji}\n\n"
            f"{cls.EMOJIS['info']} Current mode: **{mode}**\n"
            f"{cls.EMOJIS['info']} {mode_desc}"
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
    def format_ingestion_failed(cls, reason: str | None = None, item_count: int = 1) -> str:
        """Format ingestion failure message with specific reason."""
        if reason:
            reason_text = f"\n{cls.EMOJIS['info']} **Reason:** {reason}"
        else:
            reason_text = ""
        
        if item_count > 1:
            album_text = (
                f"\n{cls.EMOJIS['info']} **Note:** "
                f"Albums require all files to be processed successfully."
            )
        else:
            album_text = ""
        
        return (
            f"{cls.EMOJIS['error']} **Archive Failed** {cls.EMOJIS['warning']}\n\n"
            f"{cls.EMOJIS['info']} Failed to archive your media.{reason_text}{album_text}\n"
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
