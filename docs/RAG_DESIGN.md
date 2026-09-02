# RAG and Knowledge-Library Design

## Goal

The knowledge library lets each professor ground AI output in institutionally relevant source
material rather than relying only on general model knowledge.

## Supported Collections

- `research_papers`
- `previous_notes`
- `books`
- `case_studies`

The API can accept other collection labels, but production should use a governed allow-list.

## Current Ingestion

```text
PDF / DOCX / TXT / MD / CSV
        |
        v
Extension and size validation
        |
        v
Text extraction
        |
        +-- optional OCR fallback for scanned PDFs
        |
        v
source_documents database row
        |
        +-- extracted text for retrieval
        +-- checksum for traceability
        +-- local file or encrypted S3 object
```

Text is stored with document metadata in the database. The original file is persisted through the
configured storage provider.

## Current Retrieval

File: `app/agents/retrieval.py`

The retriever:

1. filters documents by authenticated tenant
2. optionally filters by collection
3. considers up to 250 recent documents
4. scores documents by occurrences of meaningful query terms
5. selects up to 12 documents
6. limits each excerpt and total context size

Limits are controlled by `MAX_CONTEXT_CHARS`.

## Tenant Isolation

Every source query requires `tenant_id`. Storage keys use:

```text
{S3_PREFIX}/tenants/{tenant_id}/rag/{document_id}/{filename}
```

Artifact and download queries are also tenant-scoped.

## Current Limitations

- Retrieval is document-level, not chunk-level.
- Ranking is lexical and does not understand synonyms.
- No vector embeddings are stored.
- No reranking or citation verification exists.
- Large PDFs can exceed useful model context even with bounding.
- Scanned PDFs require OCR configuration. Local development can use Tesseract/PyMuPDF, while AWS
  production should use Amazon Textract when OCR is enabled.

## Production Upgrade

```text
Document
   -> malware/content validation
   -> text and table extraction
   -> semantic chunking
   -> metadata enrichment
   -> embeddings
   -> vector store
   -> hybrid retrieval
   -> reranking
   -> cited model response
```

Recommended metadata:

- tenant and institution
- course, discipline, and subject
- document type
- title, authors, year, DOI, and URL
- edition or version
- uploaded by and uploaded at
- access classification
- page and section reference

Possible vector backends:

- OpenAI vector stores
- PostgreSQL with pgvector
- Qdrant

Keep S3 as the original-file source of record regardless of vector backend.

## Knowledge Governance

- Never ingest generated output as authoritative knowledge automatically.
- Require professor approval before adding synthesized material to a curated collection.
- Track document version and retirement status.
- Enforce copyright and institutional-license restrictions.
- Provide deletion and retention controls.
- Show source identity in final faculty output.
