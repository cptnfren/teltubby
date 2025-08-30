# Technical Architecture

**Document Version:** 1.0  
**Date:** 2025-01-27  
**Status:** ✅ **FULLY IMPLEMENTED AND PRODUCTION READY**  
**Purpose:** Comprehensive technical overview of teltubby's architecture, components, and implementation details

---

## 1. System Overview

### 1.1 Architecture Pattern
Teltubby follows a **hybrid microservices architecture** that combines:
- **Monolithic bot service** for core functionality and user interaction
- **Independent MTProto worker** for large file processing
- **Message queue system** for coordination between services
- **Shared storage layer** for persistent data and media files

### 1.2 Key Design Principles
- **Separation of concerns** - Each service has a single responsibility
- **Loose coupling** - Services communicate via message queues
- **High availability** - Services can operate independently
- **Scalability** - Worker services can be scaled horizontally
- **Fault tolerance** - Graceful degradation and recovery mechanisms

---

## 2. Component Architecture

### 2.1 High-Level Component Diagram
```
┌─────────────────────────────────────────────────────────────────┐
│                    User Interface Layer                        │
├─────────────────────────────────────────────────────────────────┤
│  Telegram Bot Service (Main Application)                      │
│  ├── Command Handlers                                         │
│  ├── Message Processing                                       │
│  ├── File Size Detection                                      │
│  └── Job Creation                                            │
├─────────────────────────────────────────────────────────────────┤
│                    Message Queue Layer                         │
├─────────────────────────────────────────────────────────────────┤
│  RabbitMQ                                                    │
│  ├── Main Job Queue                                          │
│  ├── Dead Letter Queue                                       │
│  └── Exchange Routing                                        │
├─────────────────────────────────────────────────────────────────┤
│                    Processing Layer                            │
├─────────────────────────────────────────────────────────────────┤
│  MTProto Worker Service                                       │
│  ├── Job Consumer                                            │
│  ├── MTProto Client                                          │
│  ├── File Download                                           │
│  └── Storage Upload                                          │
├─────────────────────────────────────────────────────────────────┤
│                    Storage Layer                               │
├─────────────────────────────────────────────────────────────────┤
│  MinIO/S3 Storage                                            │
│  ├── Media Files                                             │
│  ├── JSON Metadata                                           │
│  └── Session Data                                            │
├─────────────────────────────────────────────────────────────────┤
│                    Data Layer                                  │
├─────────────────────────────────────────────────────────────────┤
│  SQLite Database                                              │
│  ├── Deduplication Index                                     │
│  ├── Job History                                             │
│  └── Authentication Secrets                                   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Service Responsibilities

#### **Telegram Bot Service (`bot/service.py`)**
- **Primary Interface**: User interaction and command handling
- **File Processing**: Small files (≤50MB) via Bot API
- **Job Creation**: Large files (>50MB) routed to MTProto worker
- **User Experience**: Rich telemetry and status updates
- **Admin Controls**: Job management and system monitoring

#### **MTProto Worker Service (`mtproto/worker.py`)**
- **Job Processing**: Consume jobs from RabbitMQ queue
- **Large File Handling**: Download files >50MB via MTProto
- **Storage Integration**: Upload files to MinIO/S3
- **Session Management**: Authentication and session health monitoring
- **Error Handling**: Job failure management and retry logic

#### **Job Queue Manager (`queue/job_manager.py`)**
- **Queue Management**: RabbitMQ connection and topology
- **Job Publishing**: Create and publish job messages
- **Queue Monitoring**: Depth and status information
- **Error Handling**: Connection resilience and recovery

#### **Ingestion Pipeline (`ingest/pipeline.py`)**
- **File Processing**: Download, validate, and upload media
- **Album Handling**: Group aggregation and validation
- **Deduplication**: File uniqueness checking and handling
- **Metadata Generation**: JSON artifact creation

---

## 3. Data Flow Architecture

### 3.1 File Processing Flow

#### **Small Files (≤50MB)**
```
User → Bot → Bot API → Download → Dedup Check → S3 Upload → JSON Metadata → Success Response
```

#### **Large Files (>50MB)**
```
User → Bot → Size Detection → Job Creation → RabbitMQ → MTProto Worker → Download → S3 Upload → Job Completion → User Notification
```

### 3.2 Job Processing Flow
```
1. Bot creates job → RabbitMQ queue
2. MTProto worker consumes job → Mark as PROCESSING
3. Worker downloads file via MTProto → Validate integrity
4. Worker uploads to S3 → Generate metadata
5. Worker marks job as COMPLETED → Notify user via bot
6. Bot sends completion message → User sees final status
```

### 3.3 Error Handling Flow
```
Error occurs → Log error details → Update job state → User notification → Recovery attempt (if applicable) → Manual intervention (if needed)
```

---

## 4. Data Models and Schemas

### 4.1 Job Message Schema
```json
{
  "job_id": "uuid-v4",
  "user_id": 123456789,
  "chat_id": 123456789,
  "message_id": 123,
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
    "created_at": "2025-01-27T10:00:00Z",
    "priority": "normal",
    "retry_count": 0,
    "max_retries": 3
  }
}
```

### 4.2 Database Schema

#### **Deduplication Table**
```sql
CREATE TABLE dedup_index (
    sha256 TEXT PRIMARY KEY,
    s3_key TEXT NOT NULL,
    size_bytes INTEGER,
    mime_type TEXT,
    file_unique_id TEXT UNIQUE,
    created_at TEXT NOT NULL
);
```

#### **Job History Table**
```sql
CREATE TABLE job_history (
    job_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    state TEXT NOT NULL,
    priority INTEGER DEFAULT 4,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error TEXT,
    payload_json TEXT
);
```

#### **Secrets Table**
```sql
CREATE TABLE secrets (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### 4.3 Storage Path Structure
```
teltubby/
├── {YYYY}/
│   ├── {MM}/
│   │   ├── {chat_slug}/
│   │   │   ├── {message_id}/
│   │   │   │   ├── {filename}.{ext}
│   │   │   │   └── message.json
│   │   └── mtproto/
│   │       ├── {message_id}/
│   │       │   ├── {filename}.{ext}
│   │       │   └── metadata.json
```

---

## 5. Configuration Management

### 5.1 Environment Configuration (`runtime/config.py`)
```python
@dataclass
class AppConfig:
    # Telegram Configuration
    telegram_bot_token: str
    telegram_whitelist_ids: List[int]
    telegram_mode: str
    
    # Storage Configuration
    s3_endpoint: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_bucket: str
    
    # MTProto Configuration
    mtproto_api_id: int | None
    mtproto_api_hash: str | None
    mtproto_phone_number: str | None
    
    # Queue Configuration
    rabbitmq_host: str
    rabbitmq_port: int
    job_queue_name: str
```

### 5.2 Configuration Sources
- **Environment Variables**: Primary configuration source
- **Default Values**: Sensible defaults for development
- **Validation**: Type checking and value validation
- **Runtime Access**: Centralized configuration access

---

## 6. Security Architecture

### 6.1 Access Control
- **Whitelist Enforcement**: Only authorized users can interact
- **DM-Only Processing**: Ignores group messages completely
- **User Isolation**: Jobs are isolated by user ID
- **Admin Controls**: All whitelisted users have admin access

### 6.2 Data Security
- **Secure Storage**: Private ACL for all uploaded files
- **Encrypted Sessions**: MTProto session encryption
- **Credential Management**: Environment-based secrets
- **Audit Logging**: Complete operation history

### 6.3 Network Security
- **Container Isolation**: Docker network isolation
- **Queue Security**: RabbitMQ authentication
- **Storage Security**: S3/MinIO access controls
- **Health Monitoring**: Security event detection

---

## 7. Monitoring and Observability

### 7.1 Health Checks (`web/health.py`)
- **Service Health**: Bot, worker, and queue status
- **Dependency Health**: Database, storage, and external services
- **Performance Metrics**: Response times and throughput
- **Error Tracking**: Failure rates and error types

### 7.2 Metrics Collection (`metrics/registry.py`)
```python
# Prometheus metrics
INGESTED_MESSAGES = Counter("teltubby_messages_ingested_total", "Total messages ingested")
INGESTED_BYTES = Counter("teltubby_bytes_ingested_total", "Total bytes ingested")
PROCESSING_SECONDS = Histogram("teltubby_processing_seconds", "Processing time in seconds")
JOBS_COMPLETED = Counter("teltubby_jobs_completed_total", "Total jobs completed")
JOBS_FAILED = Counter("teltubby_jobs_failed_total", "Total jobs failed")
```

### 7.3 Logging Strategy (`runtime/logging_setup.py`)
- **Structured Logging**: JSON format for machine processing
- **Log Levels**: DEBUG, INFO, WARNING, ERROR, CRITICAL
- **Log Rotation**: Size-based rotation with retention
- **Context Enrichment**: Request IDs and correlation data

---

## 8. Error Handling and Recovery

### 8.1 Error Categories
- **Transient Errors**: Network issues, temporary failures
- **Permanent Errors**: Invalid files, authentication failures
- **System Errors**: Service unavailability, resource exhaustion
- **User Errors**: Invalid commands, permission issues

### 8.2 Recovery Strategies
- **Automatic Retry**: Transient errors with exponential backoff
- **Manual Intervention**: Permanent errors requiring admin action
- **Graceful Degradation**: Service continues with reduced functionality
- **Circuit Breaker**: Prevents cascading failures

### 8.3 Error Reporting
- **User Notifications**: Clear, actionable error messages
- **Admin Alerts**: System-level issue notifications
- **Logging**: Detailed error context for debugging
- **Metrics**: Error rate tracking and alerting

---

## 9. Performance Characteristics

### 9.1 Scalability Factors
- **Concurrent Processing**: Configurable worker concurrency
- **Queue Depth**: RabbitMQ message buffering
- **Storage Performance**: S3/MinIO upload optimization
- **Database Performance**: SQLite query optimization

### 9.2 Performance Metrics
- **Throughput**: Messages processed per second
- **Latency**: End-to-end processing time
- **Resource Usage**: CPU, memory, and disk utilization
- **Queue Performance**: Message processing rates

### 9.3 Optimization Strategies
- **Connection Pooling**: Reuse database and storage connections
- **Batch Processing**: Group operations for efficiency
- **Async I/O**: Non-blocking operations for concurrency
- **Caching**: Reduce redundant operations

---

## 10. Deployment Architecture

### 10.1 Container Strategy
```yaml
# docker-compose.yml
services:
  teltubby:      # Main bot service
  mtworker:      # MTProto worker service
  rabbitmq:      # Message queue
  minio:         # Storage service
```

### 10.2 Service Dependencies
- **Bot Service**: Depends on RabbitMQ and MinIO
- **Worker Service**: Depends on RabbitMQ and MinIO
- **Queue Service**: Independent, required by both services
- **Storage Service**: Independent, required by both services

### 10.3 Health and Monitoring
- **Health Endpoints**: `/healthz`, `/metrics`, `/status`
- **Container Health**: Docker health checks
- **Service Discovery**: Internal service communication
- **Load Balancing**: Future horizontal scaling support

---

## 11. Development and Testing

### 11.1 Development Environment
- **Local Development**: Docker Compose for services
- **PowerShell Scripts**: Windows development workflow
- **Environment Management**: `.env` file configuration
- **Hot Reloading**: Development server with auto-restart

### 11.2 Testing Strategy
- **Unit Testing**: Individual component testing
- **Integration Testing**: Service interaction testing
- **End-to-End Testing**: Complete workflow validation
- **Performance Testing**: Load and stress testing

### 11.3 Code Quality
- **Type Hints**: Python type annotations
- **Documentation**: Comprehensive docstrings and comments
- **Code Style**: Consistent formatting and naming
- **Error Handling**: Comprehensive exception management

---

## 12. Future Architecture Considerations

### 12.1 Horizontal Scaling
- **Multiple Workers**: Scale MTProto worker instances
- **Load Balancing**: Distribute jobs across workers
- **Service Mesh**: Advanced service communication
- **Auto-scaling**: Dynamic resource allocation

### 12.2 Advanced Features
- **Web Interface**: Browser-based management
- **API Endpoints**: RESTful service interface
- **Event Streaming**: Real-time status updates
- **Analytics**: Usage pattern analysis

### 12.3 Infrastructure Evolution
- **Kubernetes**: Container orchestration platform
- **Cloud Native**: Managed services integration
- **Microservices**: Further service decomposition
- **Event Sourcing**: Event-driven architecture

---

## 13. Conclusion

The technical architecture of teltubby demonstrates a **well-designed, production-ready system** that balances simplicity with functionality. The hybrid approach combining Bot API and MTProto provides comprehensive file size support while maintaining a unified user experience.

### **Architecture Strengths:**
✅ **Clear separation of concerns** with well-defined service boundaries  
✅ **Fault tolerance** through graceful degradation and recovery  
✅ **Scalability** through message queue-based processing  
✅ **Security** through comprehensive access controls and monitoring  
✅ **Observability** through health checks, metrics, and logging  
✅ **Maintainability** through clean code structure and documentation  

### **Production Readiness:**
The system is **fully production-ready** with:
- **Robust error handling** and recovery mechanisms
- **Comprehensive monitoring** and health checks
- **Secure data handling** and access controls
- **Scalable architecture** ready for future growth
- **Professional-grade** logging and metrics

This architecture positions teltubby as a **world-class archival solution** that can handle enterprise-scale requirements while maintaining simplicity and reliability.

---

**Implementation Status: ✅ COMPLETE AND PRODUCTION READY**  
*This document reflects the current production implementation with all architectural components fully functional and deployed.*
