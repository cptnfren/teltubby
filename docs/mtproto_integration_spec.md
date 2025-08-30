# MTProto Integration for Large File Processing

**Feature Specification**  
**Version:** 1.0  
**Date:** 2025-01-27  
**Purpose:** Extend teltubby to handle files exceeding Telegram Bot API's 50MB limit using MTProto client and RabbitMQ job queue

---

## 1. Executive Summary

### 1.1 Problem Statement
Telegram Bot API has a hard 50MB file size limit that cannot be overcome. This prevents teltubby from archiving large media files (videos, documents, etc.) that exceed this threshold, limiting the archival capabilities for curators.

### 1.2 Solution Overview
Implement a hybrid architecture where:
- **Bot API** handles normal files (≤50MB) with existing workflow
- **MTProto client** processes large files (>50MB) via user account authentication
- **RabbitMQ job queue** coordinates between bot and MTProto worker
- **Unified user experience** maintained through bot interface

### 1.3 Key Benefits
- **Extended file size support**: 0MB to 2GB (MTProto limit)
- **Seamless user experience**: No workflow changes for curators
- **Robust job processing**: Persistent queue with recovery
- **Admin control**: All whitelisted users can manage system
- **Scalable architecture**: Independent worker services

---

## 2. System Architecture

### 2.1 High-Level Design
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Telegram     │    │   RabbitMQ     │    │   MTProto      │
│     Bot        │◄──►│   Job Queue     │◄──►│    Worker      │
│  (Bot API)     │    │   (Persistent)  │    │ (User Account)  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   MinIO/S3     │    │   Job History   │    │   Session      │
│   Storage      │    │   Database      │    │   Management   │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

### 2.2 Component Responsibilities

#### **Telegram Bot (Existing)**
- User interface and command handling
- File size detection and routing
- Job creation for large files
- User notifications and status updates
- Admin commands for system management

#### **RabbitMQ Job Queue**
- Persistent job storage and management
- Job state tracking (pending, processing, completed, failed)
- Retry logic with exponential backoff
- Dead letter queue for failed jobs
- Queue persistence across restarts

#### **MTProto Worker**
- Independent service for large file processing
- Job queue monitoring and consumption
- Large file download via user account
- MinIO/S3 upload with consistent naming
- Job status updates and error handling

#### **Job History Database**
- Persistent logging of all job attempts
- Audit trail for debugging and monitoring
- Performance metrics and analytics
- User notification history

---

## 3. Detailed Feature Specifications

### 3.1 File Size Detection and Routing

#### **Bot API Processing (≤50MB)**
- **Trigger**: File size ≤ 50MB
- **Action**: Process immediately using existing pipeline
- **User Experience**: Normal acknowledgment and telemetry
- **Storage**: Direct upload to MinIO/S3

#### **MTProto Processing (>50MB)**
- **Trigger**: File size > 50MB
- **Action**: Create RabbitMQ job and acknowledge user
- **User Experience**: "Queued for processing" message
- **Storage**: Queued for background processing

### 3.2 Job Queue Structure

#### **Job Message Schema**
```json
{
  "job_id": "uuid-v4",
  "user_id": "telegram_user_id",
  "chat_id": "telegram_chat_id",
  "message_id": "telegram_message_id",
  "file_info": {
    "file_id": "telegram_file_id",
    "file_unique_id": "telegram_unique_id",
    "file_size": 104857600,
    "file_type": "video",
    "file_name": "large_video.mp4",
    "mime_type": "video/mp4"
  },
  "telegram_context": {
    "forward_origin": "forward_info_object",
    "caption": "user_caption",
    "entities": "message_entities",
    "media_group_id": "group_id_if_album"
  },
  "job_metadata": {
    "created_at": "iso_timestamp",
    "priority": "normal|high|urgent",
    "retry_count": 0,
    "max_retries": 3,
    "estimated_duration": "time_estimate"
  }
}
```

#### **Queue Configuration**
- **Main Queue**: `teltubby.large_files`
- **Dead Letter Queue**: `teltubby.failed_jobs`
- **Priority Queue**: `teltubby.priority_jobs`
- **Delay Queue**: `teltubby.retry_jobs`

### 3.3 Job States and Lifecycle

#### **Job States**
1. **PENDING**: Job created, waiting for worker
2. **PROCESSING**: Worker actively processing
3. **COMPLETED**: Successfully processed and stored
4. **FAILED**: Processing failed, moved to dead letter
5. **RETRYING**: Failed job being retried
6. **CANCELLED**: Job manually cancelled by admin

#### **State Transitions**
```
PENDING → PROCESSING → COMPLETED
    ↓           ↓
RETRYING ←─── FAILED
    ↓
PENDING (retry)
```

### 3.4 User Experience Flow

#### **Large File Submission**
1. User sends file > 50MB to bot
2. Bot detects size and creates job
3. Bot immediately responds: "File queued for processing (large file)"
4. Bot provides job ID and estimated processing time
5. User can check status with `/status` command

#### **Processing Updates**
1. **Job Started**: "Large file processing started"
2. **Download Progress**: "Downloading file (25% complete)"
3. **Upload Progress**: "Uploading to storage (75% complete)"
4. **Completion**: "Large file archived successfully"
5. **Failure**: "Processing failed - check /status for details"

#### **Status Commands**
- `/status` - Show current job status and system health
- `/queue` - Show pending and processing jobs
- `/jobs <job_id>` - Show specific job details
- `/retry <job_id>` - Retry failed job
- `/cancel <job_id>` - Cancel pending job

---

## 4. MTProto Worker Service

### 4.1 Service Architecture
- **Independent Python service** separate from bot
- **RabbitMQ consumer** for job processing
- **MTProto client** for large file downloads
- **MinIO/S3 client** for storage uploads
- **Session management** for authentication

### 4.2 Authentication and Session Management
- **User account credentials** stored securely
- **2FA PIN handling** via bot interface
- **Session persistence** across restarts
- **Automatic reconnection** on failures
- **Fallback authentication** methods

### 4.3 File Processing Pipeline
1. **Job Consumption**: Pick up job from RabbitMQ
2. **File Download**: Download via MTProto client
3. **Validation**: Verify file integrity and size
4. **Storage Upload**: Upload to MinIO/S3 with consistent naming
5. **Metadata Generation**: Create JSON metadata matching bot format
6. **Job Completion**: Update job status and notify bot

### 4.4 Error Handling and Recovery
- **Network failures**: Automatic retry with backoff
- **Authentication failures**: Bot notification for admin intervention
- **Storage failures**: Job requeuing and retry
- **File corruption**: Validation and error reporting
- **Account suspension**: Immediate bot notification and job pausing

---

## 5. Integration Points

### 5.1 Bot Integration
- **Job creation**: Detect large files and create jobs
- **Status updates**: Receive job updates from worker
- **User notifications**: Inform users of job progress
- **Admin commands**: Queue management and monitoring
- **Error handling**: User-friendly error messages

### 5.2 Storage Integration
- **Consistent naming**: Use same slugging logic as bot
- **Path structure**: Follow same `teltubby/{YYYY}/{MM}/...` format
- **Deduplication**: Integrate with existing dedup system
- **Metadata format**: Generate compatible JSON artifacts
- **Access control**: Maintain same privacy settings

### 5.3 Database Integration
- **Job history**: Log all job attempts and outcomes
- **User tracking**: Associate jobs with users
- **Performance metrics**: Track processing times and success rates
- **Audit trail**: Complete history for debugging and compliance

---

## 6. Configuration and Environment

### 6.1 Environment Variables
```bash
# MTProto Configuration
MTPROTO_API_ID="your_api_id"
MTPROTO_API_HASH="your_api_hash"
MTPROTO_PHONE_NUMBER="your_phone_number"
MTPROTO_SESSION_PATH="/data/mtproto.session"

# RabbitMQ Configuration
RABBITMQ_HOST="localhost"
RABBITMQ_PORT="5672"
RABBITMQ_USERNAME="guest"
RABBITMQ_PASSWORD="guest"
RABBITMQ_VHOST="/"

# Worker Configuration
WORKER_CONCURRENCY="1"
WORKER_MAX_RETRIES="3"
WORKER_RETRY_DELAY_SECONDS="60"
WORKER_HEALTH_CHECK_INTERVAL="30"

# Job Queue Configuration
JOB_QUEUE_NAME="teltubby.large_files"
JOB_DEAD_LETTER_QUEUE="teltubby.failed_jobs"
JOB_PRIORITY_QUEUE="teltubby.priority_jobs"
JOB_RETRY_QUEUE="teltubby.retry_jobs"
```

### 6.2 Service Configuration Files
- **MTProto session**: Persistent session storage
- **Worker config**: Processing parameters and limits
- **Queue config**: RabbitMQ connection and queue settings
- **Logging config**: Structured logging configuration

---

## 7. Security and Privacy

### 7.1 Authentication Security
- **Secure credential storage** (environment variables or secrets)
- **Session encryption** and secure storage
- **2FA handling** via secure bot interface
- **Access logging** for all operations

### 7.2 Data Privacy
- **User data isolation** in job processing
- **Secure file handling** during download/upload
- **Audit logging** for compliance
- **Data retention** policies for job history

### 7.3 System Security
- **Network isolation** for MTProto worker
- **Queue security** with authentication
- **Storage access** control and encryption
- **Monitoring and alerting** for security events

---

## 8. Monitoring and Observability

### 8.1 Metrics Collection
- **Job processing rates** and success/failure ratios
- **File size distributions** and processing times
- **Queue depths** and processing delays
- **MTProto client health** and session status
- **Storage performance** and upload speeds

### 8.2 Health Checks
- **Worker health**: MTProto client connectivity
- **Queue health**: RabbitMQ connection and queue status
- **Storage health**: MinIO/S3 connectivity and performance
- **Session health**: Authentication and session validity

### 8.3 Alerting
- **Job failures**: Failed job notifications
- **Queue issues**: Queue depth and processing delays
- **Authentication problems**: MTProto client issues
- **Storage problems**: Upload failures and performance issues

---

## 9. Deployment and Operations

### 9.1 Service Deployment
- **Independent container** for MTProto worker
- **Docker Compose** integration with existing services
- **Health checks** and restart policies
- **Resource limits** and monitoring

### 9.2 Scaling Considerations
- **Single worker** for edge case processing
- **Queue-based scaling** if needed in future
- **Resource monitoring** and optimization
- **Performance tuning** for large file processing

### 9.3 Backup and Recovery
- **Job queue persistence** across restarts
- **Session backup** and recovery
- **Configuration backup** and versioning
- **Disaster recovery** procedures

---

## 10. Testing and Validation

### 10.1 Unit Testing
- **Job creation and management** logic
- **MTProto client** functionality
- **Queue operations** and error handling
- **File processing** pipeline components

### 10.2 Integration Testing
- **Bot-to-queue** integration
- **Queue-to-worker** integration
- **Worker-to-storage** integration
- **End-to-end** large file processing

### 10.3 Performance Testing
- **Large file processing** performance
- **Queue throughput** and latency
- **Storage upload** performance
- **Concurrent job** processing

### 10.4 Error Scenario Testing
- **Network failures** and recovery
- **Authentication failures** and handling
- **Storage failures** and retry logic
- **Account suspension** and fallback

---

## 11. Implementation Phases

### 11.1 Phase 1: Foundation
- **RabbitMQ setup** and queue configuration
- **Job schema** definition and validation
- **Basic job management** in bot
- **Job history** database schema

### 11.2 Phase 2: Bot Integration
- **Large file detection** and job creation
- **Job status commands** and user notifications
- **Queue monitoring** and management
- **Error handling** and user feedback

### 11.3 Phase 3: MTProto Worker
- **Worker service** development and deployment
- **MTProto client** integration and authentication
- **File processing** pipeline implementation
- **Job completion** and status updates

### 11.4 Phase 4: Coordination and Polish
- **End-to-end testing** and validation
- **Performance optimization** and tuning
- **Monitoring and alerting** implementation
- **Documentation and user guides**

---

## 12. Success Criteria

### 12.1 Functional Requirements
- **Large files processed** successfully (>50MB)
- **User experience maintained** through bot interface
- **Job queue resilience** across service restarts
- **Error handling** and user notifications working
- **Admin controls** accessible to all whitelisted users

### 12.2 Performance Requirements
- **Job processing latency** < 5 minutes for setup
- **File upload performance** comparable to bot API
- **Queue throughput** sufficient for expected load
- **Resource utilization** within acceptable limits

### 12.3 Reliability Requirements
- **99%+ job success rate** for valid files
- **Automatic recovery** from common failures
- **Graceful degradation** on system issues
- **Comprehensive logging** for debugging

---

## 13. Risks and Mitigation

### 13.1 Technical Risks
- **MTProto client stability** - Use proven libraries and error handling
- **Queue persistence** - Configure RabbitMQ for durability
- **Session management** - Robust authentication and recovery
- **File corruption** - Validation and integrity checks

### 13.2 Operational Risks
- **Account suspension** - Monitor and alert on authentication issues
- **Service dependencies** - Health checks and fallback procedures
- **Resource constraints** - Monitoring and scaling policies
- **Data consistency** - Transactional job processing

### 13.3 Security Risks
- **Credential exposure** - Secure storage and access controls
- **Session hijacking** - Secure session management
- **Data privacy** - User isolation and audit logging
- **Access control** - Whitelist management and monitoring

---

## 14. Future Enhancements

### 14.1 Advanced Features
- **Multiple MTProto workers** for high-volume processing
- **Priority job handling** for urgent files
- **Batch processing** for multiple large files
- **Advanced retry logic** with different strategies

### 14.2 Integration Opportunities
- **Web interface** for job monitoring and management
- **API endpoints** for external job submission
- **Notification systems** for job completion
- **Analytics dashboard** for processing metrics

### 14.3 Scalability Improvements
- **Worker clustering** for load distribution
- **Queue partitioning** by file type or size
- **Storage optimization** for large file handling
- **Caching layers** for improved performance

---

## 15. Conclusion

This MTProto integration feature extends teltubby's capabilities to handle files of any size while maintaining the existing user experience and architecture. The hybrid approach leverages the strengths of both Bot API and MTProto, coordinated through a robust job queue system.

The implementation provides:
- **Extended file size support** (0MB to 2GB)
- **Seamless user experience** through bot interface
- **Robust job processing** with persistence and recovery
- **Admin control** for all whitelisted users
- **Scalable architecture** for future enhancements

This feature positions teltubby as a comprehensive Telegram archival solution capable of handling the full range of media file sizes that users might want to archive.

---

**End of Specification**  
*This document provides complete context for AI agents to implement the MTProto integration feature.*
