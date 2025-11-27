# Matrix-Letta File Upload Integration

This document describes the file upload integration between Matrix and Letta's Filesystem API.

## Overview

The file upload integration allows users to upload documents (PDF, TXT, MD, JSON) to Matrix rooms, which are then automatically:
1. Downloaded from Matrix media repository
2. Uploaded to Letta's filesystem API
3. Processed and embedded for semantic search
4. Made available to Letta agents for retrieval

## Architecture

### Components

1. **File Handler** (`src/matrix/file_handler.py`)
   - Detects file upload events
   - Downloads files from Matrix
   - Manages Letta folders
   - Uploads files to Letta
   - Polls for processing completion

2. **Matrix Client Integration** (`src/matrix/client.py`)
   - Registers file event callback
   - Routes files to appropriate agents
   - Sends status notifications

3. **Tests** (`tests/integration/test_file_upload.py`)
   - Unit and integration tests
   - Mocked API responses

## Supported File Types

From Letta Filesystem API documentation:

- **PDF** (`.pdf`) - Automatically extracted and chunked
- **Text** (`.txt`) - Plain text documents
- **Markdown** (`.md`) - Markdown documents
- **JSON** (`.json`) - JSON data files

**Note**: Docling is NOT required - Letta handles PDF extraction natively!

## File Size Limits

- Maximum file size: **50 MB**
- Files exceeding this limit are rejected with a notification

## Flow Diagram

```
User uploads file to Matrix room
         ↓
File event detected (m.room.message with msgtype=m.file)
         ↓
Validate file type and size
         ↓
Download file from Matrix media repository (mxc:// → HTTP)
         ↓
Get or create Letta folder for room (matrix-{room_id})
         ↓
Upload file to Letta folder via POST /v1/folders/{folder_id}/files
         ↓
Poll job status via GET /v1/jobs/{job_id}
         ↓
Notify user of completion in Matrix room
         ↓
Clean up temporary files
```

## API Integration

### Letta Filesystem API

#### Create Folder
```http
POST /v1/folders/
Content-Type: application/json
Authorization: Bearer {token}

{
  "name": "matrix-!roomid:server",
  "description": "Documents from Matrix room",
  "embedding": "openai/text-embedding-3-small"
}
```

#### Upload File
```http
POST /v1/folders/{folder_id}/files
Content-Type: multipart/form-data
Authorization: Bearer {token}

file: <file content>
metadata: {"source": "matrix", "room_id": "...", "sender": "...", ...}
```

Response:
```json
{
  "job_id": "job-uuid-here"
}
```

#### Poll Job Status
```http
GET /v1/jobs/{job_id}
Authorization: Bearer {token}
```

Response:
```json
{
  "status": "completed|processing|failed|cancelled"
}
```

### Matrix Media API

#### Download File
```http
GET /_matrix/media/v3/download/{serverName}/{mediaId}
Authorization: Bearer {token}
```

Converts `mxc://` URLs to HTTP download URLs.

## Folder Management

### Naming Convention
- Pattern: `matrix-{room_id}`
- Example: `matrix-!abc123:matrix.org`

### Folder Lifecycle
1. Created on first file upload to a room
2. Cached in memory for subsequent uploads
3. Attached to the agent responsible for that room
4. Reused for all files in the same room

## Notifications

Users receive real-time notifications about file processing:

- 📄 **Processing started**: "Processing file: {filename}"
- ✅ **Success**: "File {filename} uploaded successfully and indexed"
- ⚠️ **Timeout**: "File processing timed out for {filename}"
- ❌ **Error**: "Error processing file: {error_message}"

## Configuration

### Environment Variables

```bash
# Matrix Configuration
MATRIX_HOMESERVER_URL=http://matrix.oculair.ca:8008

# Letta Configuration
LETTA_API_URL=https://letta.oculair.ca
LETTA_TOKEN=your-letta-token-here
```

### Agent Mapping

The file handler uses the agent mapping database to determine which agent should receive files from each room:

```python
from src.models.agent_mapping import AgentMappingDB

db = AgentMappingDB()
mapping = db.get_by_room_id(room_id)
agent_id = mapping.agent_id if mapping else default_agent_id
```

## Error Handling

### File Validation Errors
- Unsupported file types → Ignored silently
- Files too large → User notified

### Download Errors
- Network failures → Retry with exponential backoff
- Invalid mxc:// URLs → Error notification

### Upload Errors
- Letta API errors → Error notification
- Timeout during processing → Warning notification

### Cleanup
- Temporary files always deleted after processing
- Even if upload fails

## Testing

Run tests with:

```bash
pytest tests/integration/test_file_upload.py -v
```

Test coverage includes:
- ✅ File metadata extraction
- ✅ File validation (type, size)
- ✅ Matrix file download
- ✅ Folder creation and caching
- ✅ Job polling (success, failure, timeout)
- ✅ End-to-end file upload flow

## Future Enhancements

### Phase 2
- [ ] Batch file uploads
- [ ] File type conversion
- [ ] Custom embedding models per room
- [ ] File version tracking

### Phase 3
- [ ] File deletion/updates
- [ ] File search UI
- [ ] Usage analytics
- [ ] Quota management

## Troubleshooting

### Files not being processed

1. Check if file type is supported
2. Verify file size < 50MB
3. Check Matrix client logs for errors
4. Verify Letta API is accessible

### Job polling timeout

1. Increase timeout in file_handler.py
2. Check Letta server load
3. Verify network connectivity

### Folder not created

1. Check Letta API credentials
2. Verify agent has permission to create folders
3. Check API logs for errors

## References

- [Letta Filesystem API Documentation](https://docs.letta.com/guides/agents/filesystem/)
- [Matrix Media Repository Spec](https://spec.matrix.org/v1.9/client-server-api/#get_matrixmediav3downloadservernamemediaid)
- [Matrix Message Events](https://spec.matrix.org/v1.9/client-server-api/#mroommessage)

## Implementation Status

- ✅ MXSYN-4: File upload event detection
- ✅ MXSYN-5: Matrix file download
- ✅ MXSYN-7: Letta folder management
- ✅ MXSYN-8: Letta file upload with job polling
- ✅ MXSYN-9: Matrix notification system
- ✅ Integration tests
- 🔄 Error handling and retry logic (in progress)
