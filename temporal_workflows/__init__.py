"""
Temporal workflows for async file processing in the Matrix-Tuwunel deployment.

Documents uploaded to Matrix rooms are processed asynchronously via Temporal:
  1. Download from Matrix authenticated media API
  2. Parse with MarkItDown (text extraction, OCR fallback)
  3. Ingest into Haystack/Weaviate document store
  4. Notify Letta agent with indexing result
  5. Update Matrix room status message

Images and audio uploads remain synchronous in the main client.
"""
