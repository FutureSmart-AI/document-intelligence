import time
import uuid
from typing import Optional, Dict, Any
from urllib.parse import urlparse

import httpx
import newspaper
from fastapi import HTTPException
from firecrawl import FirecrawlApp, JsonConfig
from langchain_community.document_loaders import WebBaseLoader
from pydantic import BaseModel, Field

from services import celery_setup_env
from services.celery_logger import logger
from celery_utils.db.db_utils import (
    retrieve_knowledge_processing_status, update_processing_status,
    mark_task_completed, create_document_record
)
from celery_utils.enrichment.enrichment_utils import enrich_document
from celery_utils.processing.s3_utils import upload_extracted_text_to_s3
from celery_utils.indexing.indexer import index_in_vectordb
from celery_utils.shared.user_usage_tracking import send_weblink_processing_event, get_owner_id_from_service_id
from celery_utils.shared.credentials_utils import get_api_key
from celery_utils.processing.file_utils import get_file_text
from celery_utils.processing.processing_modes import (
    ProcessingMode, resolve_mode, resolve_html_scraper, firecrawl_only_main_content,
)
from celery_utils.shared.tracing import track_time, traced_step, traced_reader, mark_indexing_time, reset_embedding_cost, get_embedding_cost
from celery_utils.shared import document_telemetry
from langsmith import traceable

# --- Constants ---
CACHE_MAX_AGE_MS = 3600000  # 1 hour — FireCrawl cache TTL
CRAWL_LIMIT = 10  # Max pages to crawl per URL
CRAWL_POLL_INTERVAL_SEC = 30  # Polling interval for async crawl status
MIN_CONTENT_LENGTH = 5  # Minimum chars to consider content valid

# Content type constants for MIME-based routing
CONTENT_TYPE_PDF = 'application/pdf'
CONTENT_TYPE_OFFICE_DOCX = 'application/vnd.openxmlformats-officedocument'
CONTENT_TYPE_OFFICE_DOC = 'application/msword'
CONTENT_TYPE_HTML = 'text/html'
CONTENT_TYPE_PLAIN = 'text/plain'


# ---------------------------------------------------------------------------
# Metadata Helpers
# ---------------------------------------------------------------------------

def get_combined_title(seo_title: str, author: str, page_title: str = '') -> str:
    """Combine page title, SEO title, and author into a pipe-delimited metadata string.
    Used to build extra_info for enriched documents.
    """
    parts = []
    if page_title:
        parts.append(f"| page_title: {page_title}")
    if seo_title and seo_title != page_title:
        parts.append(f"| seo_title: {seo_title}")
    if author:
        parts.append(f"| author: {author}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Enrichment & Indexing — Core processing for a single URL's content
# ---------------------------------------------------------------------------

def _enrich_and_index_url_content(
    user_id: Optional[str], knowledge_id: str, object_id: str, url: str,
    extracted_text: str, metadata: Dict[str, Any], embedding_model: str,
    source_object_id: Optional[str] = None, chunk_size: Optional[int] = None,
    enable_chunking: bool = True, owner_id: Optional[str] = None,
    processing_mode: Optional[ProcessingMode] = None
) -> None:
    """Enrich extracted URL content with LLM metadata and index in vectordb.
    Builds an enriched text string with SEO metadata, runs enrichment pipeline,
    then stores the result in the vector database.
    """
    try:
        seo_title = metadata.get("seo_title")
        seo_description = metadata.get("seo_description")
        extra_info = metadata.get("extra_info")

        # Build enriched text with available SEO metadata
        enriched_parts = [f"URL: {url}\n"]
        if seo_title:
            enriched_parts.append(f"SEO Title: {seo_title}\n")
        if seo_description:
            enriched_parts.append(f"SEO Description: {seo_description}\n")
        if extra_info:
            enriched_parts.append(f"Extra Info: {extra_info}\n")
        enriched_parts.append(f"Extracted Text: {extracted_text}\n")
        enriched_text = "\n".join(enriched_parts)
        del enriched_parts  # Free list from memory

        # Run LLM enrichment pipeline (metadata extraction + chunking)
        logger.info(f"Enriching URL content: url={url}, knowledge_id={knowledge_id}")
        enriched_metadata, chunks_only = enrich_document(
            enriched_text, knowledge_id, user_id, object_id,
            chunk_size=chunk_size, enable_chunking=enable_chunking, owner_id=owner_id,
            # The mode that chose the scraper also picks the enrichment model: a page
            # read for its tables/structure comes back messier than plain prose (see
            # processing_modes.enrichment_override_for_mode).
            processing_mode=processing_mode,
        )
        del enriched_text  # Free enriched text after LLM processing

        # Attach URL-specific metadata to the enriched document
        enriched_metadata["seo_title"] = seo_title or ""
        enriched_metadata["seo_description"] = seo_description or ""
        enriched_metadata["url"] = url
        enriched_metadata["source_knowledge_id"] = metadata.get("source_knowledge_id", "")
        enriched_metadata["source_url"] = metadata.get("source_url", "")
        if source_object_id:
            enriched_metadata["source_object_id"] = source_object_id

        # Index in vector database
        update_processing_status(knowledge_id, object_id, "indexing")
        index_in_vectordb(
            object_id=object_id, knowledge_id=knowledge_id, user_id=user_id,
            enriched_metadata=enriched_metadata, embedding_model=embedding_model,
            chunks_only=chunks_only, owner_id=owner_id
        )
        del enriched_metadata  # Free enriched metadata after indexing
        logger.info(f"Indexed URL in vectordb: url={url}, object_id={object_id}")

    except Exception as e:
        logger.error(f"Enrichment/indexing failed for url={url}, object_id={object_id}: {e}")
        raise


# ---------------------------------------------------------------------------
# Single URL Processing
# ---------------------------------------------------------------------------

def _process_single_url(
    user_id: Optional[str], knowledge_id: str, object_id: str, url: str,
    url_content: Dict[str, Any], hybrid_search: bool, embedding_model: str,
    chunk_size: Optional[int], contextualise_chunks: Optional[bool],
    source_url: Optional[str] = None, source_object_id: Optional[str] = None,
    enable_chunking: bool = True, owner_id: Optional[str] = None,
    processing_mode: Optional[ProcessingMode] = None
) -> None:
    """Process and index a single URL: validate content -> enrich -> index -> upload to S3."""
    try:
        logger.info(f"Processing single URL: url={url}, object_id={object_id}")

        # Validate extracted content length
        extracted_text = url_content.get("content", "")
        if len(extracted_text.strip()) <= MIN_CONTENT_LENGTH:
            update_processing_status(knowledge_id, object_id, "failed", error_detail=f"Content too short for URL: {url} ({len(extracted_text.strip())} chars)")
            logger.error(f"Content too short for URL: url={url}, chars={len(extracted_text.strip())}")
            return

        # Build metadata dict for enrichment
        metadata = {
            "extra_info": url_content.get("extra_info", ""),
            "seo_title": url_content.get("seo_title", ""),
            "seo_description": url_content.get("seo_description", ""),
            "url": url,
            "source_url": source_url,
        }

        # Enrich and index
        update_processing_status(knowledge_id, object_id, "enriching")
        _enrich_and_index_url_content(
            user_id=user_id, knowledge_id=knowledge_id, object_id=object_id,
            url=url, extracted_text=extracted_text, metadata=metadata,
            embedding_model=embedding_model, source_object_id=source_object_id,
            chunk_size=chunk_size, enable_chunking=enable_chunking, owner_id=owner_id,
            processing_mode=processing_mode
        )

        # Upload extracted text to S3 for reference
        upload_extracted_text_to_s3(extracted_text=extracted_text, file_name=None, weblink=url, object_id=object_id)
        update_processing_status(knowledge_id, object_id, "completed")
        logger.info(f"Single URL processing complete: url={url}, object_id={object_id}")

    except Exception as e:
        update_processing_status(knowledge_id, object_id, "failed")
        logger.error(f"Single URL processing failed: url={url}, object_id={object_id}: {e}")
        raise


def _process_single_url_content(
    user_id: Optional[str], knowledge_id: str, object_id: str, weblink: str,
    scraped_content: Dict[str, Any], hybrid_search: bool, embedding_model: str,
    chunk_size: Optional[int], contextualise_chunks: Optional[bool],
    enable_chunking: bool = True, owner_id: Optional[str] = None,
    processing_mode: Optional[ProcessingMode] = None
) -> tuple[int, int, int]:
    """Process scraped data from a single URL (non-crawl path).
    Returns (processed_page_count, char_count, llama_parse_credits) for usage tracking.
    Credits are non-zero only when the URL resolved to a file that LlamaParse read.
    """
    try:
        extracted_text = scraped_content.get("content", "")
        llama_parse_credits = scraped_content.get("llama_parse_credits", 0) or 0
        char_count = len(extracted_text.strip())
        logger.info(f"Processing single URL content: url={weblink}, chars={char_count}")

        url_content = {
            "extra_info": scraped_content.get("extra_info", ""),
            "source_url": weblink,
            "content": extracted_text,
            "seo_title": scraped_content.get("seo_title", ""),
            "seo_description": scraped_content.get("seo_description", "")
        }

        _process_single_url(
            user_id=user_id, knowledge_id=knowledge_id, object_id=object_id, url=weblink,
            url_content=url_content, hybrid_search=hybrid_search, embedding_model=embedding_model,
            chunk_size=chunk_size, contextualise_chunks=contextualise_chunks, source_object_id=None,
            enable_chunking=enable_chunking, owner_id=owner_id, processing_mode=processing_mode
        )
        del url_content  # Free dict after processing
        return 1, char_count, llama_parse_credits

    except Exception as e:
        update_processing_status(knowledge_id, object_id, "failed")
        logger.error(f"Single URL content processing failed: url={weblink}: {e}")
        raise


# ---------------------------------------------------------------------------
# Crawl Result Processing — Handles multi-page crawl results
# ---------------------------------------------------------------------------

def _process_crawl_result(
    user_id: Optional[str], knowledge_id: str, object_id: str, weblink: str,
    scraped_content: Dict[str, Any], use_llama_parse: bool, hybrid_search: bool,
    embedding_model: str, chunk_size: Optional[int], contextualise_chunks: Optional[bool],
    enable_chunking: bool = True, owner_id: Optional[str] = None,
    processing_mode: Optional[ProcessingMode] = None
) -> tuple[int, int, int]:
    """Process crawl results containing a main URL and multiple crawled child URLs.
    Creates separate document records for each crawled URL.
    Returns (processed_page_count, total_char_count, llama_parse_credits) for usage
    tracking; credits accumulate across any children that turned out to be files.
    """
    try:
        main_url = scraped_content.get("source_url", weblink)
        main_content = scraped_content.get("source_content", "")
        char_count = len(main_content.strip())
        crawl_llama_parse_credits = 0
        logger.info(f"Processing crawl result: main_url={main_url}, chars={char_count}")

        # Process the main/source URL first
        main_url_content = {
            "extra_info": scraped_content.get("source_extra_info", ""),
            "source_url": main_url,
            "content": main_content,
            "seo_title": scraped_content.get("source_seo_title", ""),
            "seo_description": scraped_content.get("source_seo_description", "")
        }

        _process_single_url(
            user_id=user_id, knowledge_id=knowledge_id, object_id=object_id, url=main_url,
            url_content=main_url_content, hybrid_search=hybrid_search, embedding_model=embedding_model,
            chunk_size=chunk_size, contextualise_chunks=contextualise_chunks, source_object_id=None,
            enable_chunking=enable_chunking, owner_id=owner_id, processing_mode=processing_mode
        )
        del main_url_content  # Free main URL content dict

        # Process each crawled child URL
        crawled_urls = scraped_content.get("crawled_urls", [])
        logger.info(f"Processing {len(crawled_urls)} crawled child URLs for {weblink}")

        for crawled_url_content in crawled_urls:
            crawled_url = crawled_url_content.get("url", "")
            if not crawled_url or crawled_url == main_url:
                continue

            # A child that is really a file (PDF/DOCX/plain text) is re-read through the
            # same mode-aware reader an upload uses, discarding FireCrawl's markdown for it.
            # Returns None for an ordinary HTML child, which keeps the existing behaviour.
            child_as_file = _reread_child_as_file(
                crawled_url, use_llama_parse,
                document_processing_mode=processing_mode.value if processing_mode else None,
                owner_id=owner_id,
            )
            if child_as_file is not None:
                child_mime = child_as_file.pop("mime_type", "")
                child_credits = child_as_file.get("llama_parse_credits", 0) or 0
                crawl_llama_parse_credits += child_credits
                # Keep the crawler's own URL/SEO framing; take the file reader's text.
                crawled_url_content = {**crawled_url_content, **child_as_file, "url": crawled_url}
            else:
                child_mime = ""

            # Create a new document record for each crawled URL
            crawled_object_id = str(uuid.uuid4())
            create_document_record(
                user_id=user_id, knowledge_id=knowledge_id,
                object_id=crawled_object_id, file_name=None,
                use_llama_parse=use_llama_parse, url=crawled_url,
                contextualise_chunks=contextualise_chunks, chunk_size=chunk_size,
                content_type="file" if child_mime else "url", source_object_id=object_id,
                source_url=main_url
            )
            logger.info(f"Created record for crawled URL: url={crawled_url}, object_id={crawled_object_id}")

            # Accumulate char count across all crawled pages
            crawled_content = crawled_url_content.get("content", "")
            char_count += len(crawled_content.strip())

            # Process the crawled URL (enrich + index)
            _process_single_url(
                user_id=user_id, knowledge_id=knowledge_id, object_id=crawled_object_id, url=crawled_url,
                url_content=crawled_url_content, hybrid_search=hybrid_search, embedding_model=embedding_model,
                chunk_size=chunk_size, contextualise_chunks=contextualise_chunks, source_url=main_url,
                source_object_id=object_id, enable_chunking=enable_chunking, owner_id=owner_id,
                processing_mode=processing_mode
            )
            del crawled_url_content  # Free per-URL content after processing
        del crawled_urls  # Free crawled URLs list

        update_processing_status(knowledge_id, object_id, "completed")
        processed_page_count = scraped_content.get("pages_processed", 1 + len(scraped_content.get("crawled_urls", [])))
        logger.info(
            f"Crawl processing complete: main_url={main_url}, pages={processed_page_count}, "
            f"total_chars={char_count}, llama_parse_credits={crawl_llama_parse_credits}"
        )
        return processed_page_count, char_count, crawl_llama_parse_credits

    except Exception as e:
        update_processing_status(knowledge_id, object_id, "failed")
        logger.error(f"Crawl result processing failed for weblink={weblink}: {e}")
        raise


# ---------------------------------------------------------------------------
# Scraping / Crawling with FireCrawl
# ---------------------------------------------------------------------------

# Pydantic model used by FireCrawl JSON extraction to get the blog/article title
class _BlogPost(BaseModel):
    blog_title: str = Field(..., description="The title of the blog post/article")


@traced_reader(
    "API: FireCrawl - Scrape URL",
    inputs=("url", "only_main_content", "owner_id"),
    meta_keys=("seo_title", "seo_description", "source_url", "key_type"),
)
def scrape_url_with_firecrawl(url: str, only_main_content: Optional[bool] = None,
                              owner_id: Optional[str] = None) -> Dict[str, Any]:
    """Scrape a single URL using FireCrawl API.
    Extracts markdown content, SEO metadata, and blog title via JSON extraction.

    ``only_main_content`` is mode-driven (see processing_modes.firecrawl_only_main_content) —
    left unset (FireCrawl's own default, ``True``) unless the mode asked to keep surrounding
    structure that "main content only" would strip.
    """
    try:
        json_config = JsonConfig(schema=_BlogPost)
        api_key, key_type = get_api_key(owner_id, "firecrawl", celery_setup_env.FIRECRAWL_API_KEY)
        app = FirecrawlApp(api_key=api_key)

        scrape_kwargs = {"formats": ['json', 'markdown'], "json_options": json_config, "max_age": CACHE_MAX_AGE_MS}
        if only_main_content is not None:
            scrape_kwargs["only_main_content"] = only_main_content

        logger.info(f"Scraping URL with FireCrawl: url={url}, only_main_content={only_main_content}")
        data = app.scrape_url(url, **scrape_kwargs)

        # Extract metadata from FireCrawl response
        seo_title = data.metadata.get("title", "")
        source_url = data.metadata.get("sourceURL", "")
        author = data.metadata.get("author", "")
        page_title = data.json.get("blog_title", "")
        seo_description = data.metadata.get('description', '')
        combined_title = get_combined_title(seo_title, author, page_title)

        logger.info(f"FireCrawl scrape complete: url={url}, page_title={page_title}, seo_title={seo_title}")
        return {
            "extra_info": combined_title,
            "source_url": source_url,
            "content": data.markdown,
            "seo_title": seo_title,
            "seo_description": seo_description,
            "key_type": key_type
        }
    except Exception as e:
        logger.error(f"FireCrawl scrape failed for url={url}: {e}")
        raise HTTPException(status_code=500, detail=f"Error in scrape_url_with_firecrawl: {e}")


@traced_reader(
    "API: FireCrawl - Crawl URL",
    inputs=("url", "max_depth", "only_main_content", "owner_id"),
    meta_keys=("pages_processed", "source_url", "key_type"),
)
def crawl_url_with_firecrawl(url: str, max_depth: int, only_main_content: Optional[bool] = None,
                             owner_id: Optional[str] = None) -> Dict[str, Any]:
    """Crawl a URL and its linked pages using FireCrawl API.
    Returns structured data with the source URL content and all crawled child URLs.

    ``max_depth`` bounds how far from the starting URL the crawl follows links. It used to be
    accepted here and then dropped — the value travelled all the way from the API through SQS into
    this function and was never handed to FireCrawl, so a crawl was limited only by CRAWL_LIMIT and
    every depth behaved identically. It went unnoticed because a caller changing the value *did* see
    a difference: a separate check downstream reads ``max_depth > 0`` to decide whether crawled child
    pages get indexed at all, so the parameter looked wired.

    ``only_main_content`` is mode-driven (see processing_modes.firecrawl_only_main_content) and
    is FireCrawl's own crawl-wide scrape setting, so it applies uniformly to the entry page and
    every crawled child page. This is unrelated to the separate, already-logged limitation that a
    child page which happens to be a PDF is still read as FireCrawl's markdown rather than through
    our own MIME-based PDF reader (fs-agent-knowledge docs/plan/document-processing/decisions.md,
    2026-08-24) — that is about *which reader* touches a child page, not these scrape options.
    """
    try:
        from firecrawl import ScrapeOptions

        api_key, key_type = get_api_key(owner_id, "firecrawl", celery_setup_env.FIRECRAWL_API_KEY)
        app = FirecrawlApp(api_key=api_key)

        scrape_options_kwargs = {"formats": ['markdown', 'html'], "maxAge": CACHE_MAX_AGE_MS}
        if only_main_content is not None:
            scrape_options_kwargs["onlyMainContent"] = only_main_content
        scrape_options = ScrapeOptions(**scrape_options_kwargs)
        logger.info(f"Starting FireCrawl crawl: url={url}, limit={CRAWL_LIMIT}, max_depth={max_depth}")
        # Both bounds apply and whichever is hit first wins: max_depth limits how far links are
        # followed, CRAWL_LIMIT caps the total pages. Passing a falsy depth would be read as "no
        # limit" by the SDK, which is the behaviour being fixed — so only forward a real value.
        crawl_result = app.crawl_url(
            url=url, limit=CRAWL_LIMIT,
            max_depth=max_depth if max_depth and max_depth > 0 else None,
            scrape_options=scrape_options, poll_interval=CRAWL_POLL_INTERVAL_SEC
        )

        # Initialize result structure with empty defaults
        result = {
            "source_url": url,
            "source_content": "",
            "source_extra_info": "",
            "source_seo_title": "",
            "source_seo_description": "",
            "crawled_urls": [],
            "pages_processed": 0,
            "key_type": key_type
        }

        if not (crawl_result.success and crawl_result.data):
            logger.warning(f"Crawl returned no data for url={url}")
            return result

        pages_processed = len(crawl_result.data)

        # Extract main URL content (first document in crawl results)
        main_doc = crawl_result.data[0]
        result["source_content"] = main_doc.markdown if hasattr(main_doc, 'markdown') else main_doc.page_content
        result["source_extra_info"] = get_combined_title(
            main_doc.metadata.get("title", ""),
            main_doc.metadata.get("author", ""),
            main_doc.metadata.get("page_title", "")
        )
        result["source_seo_title"] = main_doc.metadata.get("title", "")
        result["source_seo_description"] = main_doc.metadata.get("description", "")
        del main_doc  # Free main document reference

        # Extract crawled child URL content (remaining documents)
        for doc in crawl_result.data[1:]:
            result["crawled_urls"].append({
                "url": doc.metadata.get("sourceURL", doc.metadata.get("source", "")),
                "content": doc.markdown if hasattr(doc, 'markdown') else doc.page_content,
                "extra_info": get_combined_title(
                    doc.metadata.get("title", ""),
                    doc.metadata.get("author", ""),
                    doc.metadata.get("page_title", "")
                ),
                "seo_title": doc.metadata.get("title", ""),
                "seo_description": doc.metadata.get("description", "")
            })
            del doc  # Free per-document reference

        result["pages_processed"] = pages_processed
        del crawl_result  # Free entire crawl result
        logger.info(f"FireCrawl crawl complete: url={url}, pages_processed={pages_processed}")
        return result

    except Exception as e:
        logger.error(f"FireCrawl crawl failed for url={url}: {e}")
        raise


def process_url_with_firecrawl(url: str, method: str, max_depth: int, only_main_content: Optional[bool] = None,
                               owner_id: Optional[str] = None) -> Dict[str, Any]:
    """Route to FireCrawl scrape or crawl based on the specified method."""
    try:
        logger.info(f"Processing URL with FireCrawl: url={url}, method={method}")
        if method == "scrape":
            return scrape_url_with_firecrawl(url, only_main_content=only_main_content, owner_id=owner_id)
        return crawl_url_with_firecrawl(url, max_depth, only_main_content=only_main_content, owner_id=owner_id)
    except Exception as e:
        logger.error(f"FireCrawl processing failed for url={url}, method={method}: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing URL with FireCrawl: {e}")


# ---------------------------------------------------------------------------
# Alternative Scrapers (Newspaper, LangChain)
# ---------------------------------------------------------------------------
@traced_reader(
    "API: Newspaper3k - Scrape URL",
    meta_keys=("seo_title", "seo_description", "source_url"),
)
def _download_and_parse_article(article: Any) -> Dict[str, Any]:
    """Download and parse a web article using newspaper3k library.
    Extracts title, author, content, and SEO metadata.
    """
    try:
        article.download()
        article.parse()
    except Exception as e:
        logger.error(f"Newspaper article download/parse failed: {e}")
        raise

    seo_title = getattr(article, 'title', '')
    url = getattr(article, 'url', '')
    article_text = getattr(article, 'text', '')
    authors = getattr(article, 'authors', [])
    authors_str = " ".join(authors) if authors else ""
    seo_description = getattr(article, 'meta_description', '')

    logger.info(f"Newspaper parsed: url={url}, seo_title={seo_title}, chars={len(article_text)}")
    return {
        "extra_info": get_combined_title(seo_title, authors_str),
        "source_url": url,
        "content": article_text,
        "seo_title": seo_title,
        "seo_description": seo_description
    }


@traced_reader(
    "API: LangChain - Load URL",
    inputs=("url",),
    meta_keys=("seo_title", "seo_description", "source_url"),
)
def process_url_with_langchain(url: str) -> Dict[str, Any]:
    """Scrape a URL using LangChain WebBaseLoader to extract content and metadata."""
    try:
        logger.info(f"Loading URL with LangChain WebBaseLoader: url={url}")
        loader = WebBaseLoader(url)
        docs = loader.load()

        page_content = docs[0].page_content if docs else ''
        metadata = docs[0].metadata if docs else {}

        seo_title = metadata.get('title', '')
        source_url = metadata.get('source', url)
        seo_description = metadata.get('description', '')

        logger.info(f"LangChain load complete: url={url}, seo_title={seo_title}, chars={len(page_content)}")
        return {
            "extra_info": get_combined_title(seo_title, ''),
            "source_url": source_url,
            "content": page_content,
            "seo_title": seo_title,
            "seo_description": seo_description
        }
    except Exception as e:
        logger.error(f"LangChain URL loading failed for url={url}: {e}")
        raise


# ---------------------------------------------------------------------------
# URL Text Extraction Dispatcher
# ---------------------------------------------------------------------------

def extract_text_from_url(url: str, scrape_using: str = 'langchain', scraping_method: str = 'scrape',
                          max_depth: int = 0, only_main_content: Optional[bool] = None,
                          owner_id: Optional[str] = None) -> Dict[str, Any]:
    """Extract text from a URL by dispatching to the configured scraping library.
    Supported: 'newspaper', 'firecrawl', 'langchain'. ``scrape_using`` is resolved by the
    caller from the processing mode (see processing_modes.resolve_html_scraper) — this
    function itself is just the dispatcher and has no opinion on which backend to pick.
    """
    # LangSmith step for the scraper choice — the counterpart of
    # "Node: Route File To Extractor" on the file side. The backend that actually ran
    # shows up as the child step (FireCrawl / Newspaper3k / LangChain).
    with traced_step(
        "Node: Route URL To Scraper",
        inputs={"url": url, "scraper": scrape_using, "method": scraping_method},
        url=str(url),
        scraper=str(scrape_using),
        scraping_method=str(scraping_method or ""),
        max_depth=int(max_depth or 0),
        only_main_content=bool(only_main_content) if only_main_content is not None else None,
    ) as route_step:
        try:
            logger.info(f"Extracting content: url={url}, scraper={scrape_using}, method={scraping_method}")
            scraper = scrape_using.lower()

            if scraper == 'newspaper':
                article = newspaper.Article(url, language='en')
                result = _download_and_parse_article(article)
            elif scraper == 'firecrawl':
                result = process_url_with_firecrawl(url=url, method=scraping_method, max_depth=max_depth,
                                                    only_main_content=only_main_content, owner_id=owner_id)
            elif scraper == 'langchain':
                result = process_url_with_langchain(url)
            else:
                raise ValueError(f"Unsupported scraping method: {scrape_using}")

            if isinstance(result, dict):
                route_step.add(
                    pages_processed=result.get("pages_processed"),
                    child_urls=len(result.get("crawled_urls") or []),
                    source_url=result.get("source_url"),
                )
                route_step.set_text(result.get("content"))
            return result

        except Exception as e:
            # Raise, never return an error-shaped "success". This used to return
            # {"content": f"Error fetching content: {e}"} — a string comfortably longer than
            # MIN_CONTENT_LENGTH, so it passed _process_single_url's length gate, went to the
            # enrichment LLM, and was indexed as though it were the page. The collection then
            # held documents whose body was an error message, with status "completed".
            # Raising lets process_and_index_weblink's own handler mark the document failed
            # with this reason, which is what every other extraction failure already does.
            logger.error(f"URL content extraction failed: url={url}, scraper={scrape_using}: {e}")
            raise


# ---------------------------------------------------------------------------
# Weblink Content Type Detection & Processing
# ---------------------------------------------------------------------------

# Extension → MIME, used only when the server won't tell us (see _get_content_type_from_url).
_EXTENSION_MIME_FALLBACK = {
    '.pdf': CONTENT_TYPE_PDF,
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.txt': CONTENT_TYPE_PLAIN,
    '.md': CONTENT_TYPE_PLAIN,
}


def _mime_from_extension(url: str) -> str:
    """Guess a MIME type from the URL's path extension. Empty string when nothing matches."""
    path = urlparse(url).path.lower()
    for ext, mime in _EXTENSION_MIME_FALLBACK.items():
        if path.endswith(ext):
            return mime
    return ''


def _get_content_type_from_url(client: httpx.Client, url: str) -> str:
    """Detect the MIME type of a URL, preferring a HEAD request but never depending on one.

    Plenty of servers answer HEAD with 405, or answer 200 with no Content-Type at all. That
    used to leave mime_type empty, which fell through extract_weblink_content's routing to
    ``raise ValueError("Unsupported content type: ")`` — so a perfectly ordinary page failed
    to ingest, with an error message naming no type because there wasn't one.

    Two fallbacks, in order: the URL's own file extension (which is what makes a PDF link
    behind a HEAD-hostile server still reach the PDF reader), then plain HTML, since a
    weblink with no other signal is overwhelmingly a web page and the scraper will surface a
    real error if it isn't.
    """
    try:
        response = client.head(url, follow_redirects=True)
        content_type = response.headers.get('Content-Type', '')
        mime_type = content_type.split(';')[0].strip()
    except Exception as e:
        logger.warning(f"HEAD request failed, falling back to extension/HTML: url={url}: {e}")
        mime_type = ''

    if not mime_type:
        mime_type = _mime_from_extension(url) or CONTENT_TYPE_HTML
        logger.info(f"URL content type not served, inferred: url={url}, mime={mime_type}")
        return mime_type

    logger.info(f"URL content type detected: url={url}, mime={mime_type}")
    return mime_type


def _process_file_content(client: httpx.Client, url: str, mime_type: str, use_llama_parse: bool,
                          document_processing_mode: Optional[str] = None,
                          owner_id: Optional[str] = None) -> Dict[str, Any]:
    """Download a file from URL and extract text (for PDF, DOCX, plain text links)."""
    logger.info(f"Downloading file from URL: url={url}, mime={mime_type}")
    response = client.get(url)
    file_bytes = response.content
    del response  # Free HTTP response
    filename = url.split("/")[-1]
    # A file found behind a weblink is read by the exact same function an uploaded file
    # is, mode and auto-shift included — resolve_mode falls back to the legacy boolean
    # (and then to auto) when no mode was supplied, so an older caller sees no change.
    text = get_file_text(
        file_bytes, mime_type, filename,
        resolve_mode(document_processing_mode, use_llama_parse), owner_id=owner_id,
    )
    del file_bytes  # Free downloaded bytes
    logger.info(f"File content extracted from URL: url={url}, filename={filename}")
    return text


def _is_file_mime(mime_type: str) -> bool:
    """True when a MIME type is one get_file_text can actually read: PDF, DOCX, plain text.

    Legacy ``application/msword`` (.doc) is deliberately excluded even though
    extract_weblink_content routes it to the file reader. get_file_text raises
    "MS Word (.doc) files are not supported" for it, so re-reading a crawled child as one
    can only ever download the whole file and then fall back — the child keeps FireCrawl's
    markdown either way, so skip the wasted download. A .doc supplied directly as the
    weblink still fails loudly, which is right: that URL is what the caller asked for.
    """
    return (
        CONTENT_TYPE_PDF in mime_type
        or CONTENT_TYPE_OFFICE_DOCX in mime_type
        or CONTENT_TYPE_PLAIN in mime_type
    )


def _reread_child_as_file(child_url: str, use_llama_parse: bool,
                          document_processing_mode: Optional[str] = None,
                          owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Re-read a crawled child URL through the mode-aware file reader when it is a file.

    FireCrawl renders every crawled child to markdown before this code ever sees it, so a
    PDF or DOCX behind a crawled link used to be indexed as FireCrawl's own rendering —
    LlamaParse, the scanned/layout_aware/visual settings and the PDF auto-shift were all
    unreachable for children, which is exactly where tabular and scanned content was being
    silently flattened. One HEAD per child (bounded by CRAWL_LIMIT) decides; a file is
    re-downloaded and read by the same get_file_text an upload uses.

    Returns None whenever the child should keep FireCrawl's markdown — an HTML page, a
    type that resolves to nothing file-shaped, or a re-read that failed. Never raises: a
    child that cannot be re-read is not a reason to fail the whole crawl.

    Note a HEAD failure alone no longer means "give up": _get_content_type_from_url falls
    back to the URL's extension, so a PDF link behind a HEAD-hostile server is still
    re-read. It resolves to HTML — and so returns None here — only when the extension says
    nothing either.
    """
    try:
        with httpx.Client() as client:
            mime_type = _get_content_type_from_url(client, child_url)
            if not _is_file_mime(mime_type):
                return None
            logger.info(f"Crawled child is a file, re-reading through the mode-aware path: url={child_url}, mime={mime_type}")
            content = _process_file_content(
                client, child_url, mime_type, use_llama_parse,
                document_processing_mode=document_processing_mode, owner_id=owner_id,
            )
            content["mime_type"] = mime_type
            return content
    except Exception as e:
        # Falling back to FireCrawl's markdown is strictly better than dropping the page.
        logger.warning(f"Child re-read failed, keeping FireCrawl markdown: url={child_url}: {e}")
        return None


@track_time("Node: Extract Weblink Content")
def extract_weblink_content(weblink: str, use_llama_parse: bool, scrape_using: str,
                            scraping_method: str, max_depth: int,
                            document_processing_mode: Optional[str] = None,
                            only_main_content: Optional[bool] = None,
                            owner_id: Optional[str] = None) -> Dict[str, Any]:
    """Detect content type of a weblink and route to the appropriate processor.
    PDF/DOCX/text -> download and parse as file; document_processing_mode applies here.
    HTML -> scrape with ``scrape_using`` (already resolved from the mode by the caller —
    see process_and_index_weblink and processing_modes.resolve_html_scraper) with
    ``only_main_content`` (also mode-resolved) tuning what FireCrawl keeps.
    """
    try:
        logger.info(f"Detecting content type for weblink: {weblink}")
        with httpx.Client() as client:
            mime_type = _get_content_type_from_url(client, weblink)

            if CONTENT_TYPE_PDF in mime_type:
                return _process_file_content(client, weblink, mime_type, use_llama_parse,
                                              document_processing_mode=document_processing_mode, owner_id=owner_id)
            elif CONTENT_TYPE_OFFICE_DOCX in mime_type or CONTENT_TYPE_OFFICE_DOC in mime_type:
                return _process_file_content(client, weblink, mime_type, use_llama_parse,
                                              document_processing_mode=document_processing_mode, owner_id=owner_id)
            elif CONTENT_TYPE_HTML in mime_type:
                return extract_text_from_url(weblink, scrape_using, scraping_method, max_depth,
                                              only_main_content=only_main_content, owner_id=owner_id)
            elif CONTENT_TYPE_PLAIN in mime_type:
                return _process_file_content(client, weblink, mime_type, use_llama_parse,
                                              document_processing_mode=document_processing_mode, owner_id=owner_id)
            else:
                logger.error(f"Unsupported content type: mime={mime_type}, url={weblink}")
                raise ValueError(f"Unsupported content type: {mime_type}")

    except httpx.RequestError as e:
        logger.error(f"HTTP request failed for weblink={weblink}: {e}")
        raise ValueError(f"Error processing weblink: {e}")


# ---------------------------------------------------------------------------
# Scrape Method Label — Maps scraper + method to a tracking label
# ---------------------------------------------------------------------------

_SCRAPE_METHOD_MAP = {
    'firecrawl': {'crawl': 'firecrawl_crawl', 'scrape': 'firecrawl_scrape'},
    'langchain': 'langchain_scrape',
    'newspaper': 'newspaper_scrape',
}


def _get_event_scrape_method(scrape_using: str, scraping_method: str) -> str:
    """Map scraper name + method to a usage tracking event label."""
    entry = _SCRAPE_METHOD_MAP.get(scrape_using.lower())
    if isinstance(entry, dict):
        return entry.get(scraping_method, "firecrawl_scrape")
    return entry or "unknown"


# ---------------------------------------------------------------------------
# Main Pipeline: Scrape -> Extract -> Enrich -> Index -> Track
# ---------------------------------------------------------------------------

@track_time("Node: Process And Index Weblink")
@traceable(name="Process Document")
def process_and_index_weblink(
    user_id: Optional[str], knowledge_id: str, object_id: str, weblink: str,
    use_llama_parse: bool, scrape_using: str, scraping_method: str, max_depth: int,
    chunk_size: Optional[int], hybrid_search: bool, embedding_model: str,
    contextualise_chunks: Optional[bool] = False, enable_chunking: bool = True,
    document_processing_mode: Optional[str] = None,
) -> None:
    """End-to-end weblink processing pipeline called by Celery workers.
    Steps: scrape/crawl URL -> extract text -> enrich with LLM -> index in vectordb -> track usage.
    For crawl mode, processes multiple child URLs as separate documents.

    ``scrape_using`` is accepted only for wire back-compatibility with a caller that still
    sends it — its *value* is no longer consulted. The scraper is now chosen from
    ``document_processing_mode`` (see processing_modes.resolve_html_scraper), the same way the
    mode already chooses LlamaParse settings for a file — see
    fs-agent-knowledge docs/plan/document-processing/decisions.md (2026-08-24) for why.

    document_processing_mode reaches both halves of a weblink now: it picks the LlamaParse
    settings when the URL resolves to a file (PDF/DOCX/plain text — see
    extract_weblink_content), and it picks the scraper backend plus FireCrawl's
    ``onlyMainContent`` when it resolves to an HTML page. It does not reach crawled child pages
    beyond FireCrawl's own crawl-wide scrape settings (a separate, logged limitation — see
    docs/plan/document-processing/decisions.md in fs-agent-knowledge).
    """
    logger.info(f"Starting weblink processing: url={weblink}, knowledge_id={knowledge_id}, object_id={object_id}, method={scraping_method}")
    processing_start_time = time.time()
    reset_embedding_cost()
    owner_id = get_owner_id_from_service_id(knowledge_id)
    mode = resolve_mode(document_processing_mode, use_llama_parse)
    resolved_scraper = resolve_html_scraper(mode, scraping_method)
    only_main_content = firecrawl_only_main_content(mode)

    try:
        # Skip if already processed (idempotency check)
        status = retrieve_knowledge_processing_status(knowledge_id, object_id)
        if status == "completed":
            logger.info(f"Task already completed for object_id={object_id}, skipping")
            return
        document_telemetry.start(object_id)

        # Step 1: Scrape/crawl the weblink
        update_processing_status(knowledge_id, object_id, "scraping")
        _parse_t0 = time.time()
        logger.info(f"Scraping weblink: url={weblink}, mode={mode.value}, scraper={resolved_scraper}, method={scraping_method}")
        scraped_content = extract_weblink_content(
            weblink, use_llama_parse, resolved_scraper, scraping_method, max_depth,
            document_processing_mode=document_processing_mode, only_main_content=only_main_content,
            owner_id=owner_id,
        )
        scrape_key_type = scraped_content.get("key_type") if isinstance(scraped_content, dict) else None

        # A URL that resolved to a file was read by get_file_text, not by a scraper, so it
        # reports the mode that actually ran (post auto-shift). An HTML page has no such
        # mode — the scraper is the mechanism — so effective_mode stays None there and
        # mark_task_completed leaves the stored value untouched.
        effective_mode = scraped_content.get("effective_mode") if isinstance(scraped_content, dict) else None
        resolved_as_file = effective_mode is not None
        # Captured here because scraped_content is freed before the usage event is sent.
        scraped_content_parse_mode = scraped_content.get("parse_mode") if resolved_as_file else None
        document_telemetry.record_parse(
            object_id,
            parser=(
                ("llamaparse" if scraped_content_parse_mode else "local_text")
                if resolved_as_file else (resolved_scraper or scraping_method or "scraper")
            ),
            mode=effective_mode or mode.value,
            ms=int((time.time() - _parse_t0) * 1000),
            scraping_method=scraping_method,
            fallback_reason=(scraped_content.get("parse_fallback_reason") if isinstance(scraped_content, dict) else None),
        )

        # Step 2: Extract and process content
        update_processing_status(knowledge_id, object_id, "extracting")
        # Labelling a file read as "firecrawl_scrape"/"newspaper_scrape" is simply wrong —
        # no scraper ran. Report the parse path instead, so weblink spend is attributable.
        event_scrape_method = (
            f"file_{scraped_content.get('parse_mode') or 'local'}"
            if resolved_as_file
            else _get_event_scrape_method(resolved_scraper, scraping_method)
        )

        # Route to crawl (multi-page) or single-URL processing
        if max_depth > 0 and scraping_method == 'crawl' and isinstance(scraped_content, dict) and 'crawled_urls' in scraped_content:
            logger.info(f"Processing as crawl result with {len(scraped_content.get('crawled_urls', []))} child URLs")
            processed_page_count, char_count, llama_parse_credits = _process_crawl_result(
                user_id=user_id, knowledge_id=knowledge_id, object_id=object_id, weblink=weblink,
                scraped_content=scraped_content, use_llama_parse=use_llama_parse, hybrid_search=hybrid_search,
                embedding_model=embedding_model, chunk_size=chunk_size, contextualise_chunks=contextualise_chunks,
                enable_chunking=enable_chunking, owner_id=owner_id, processing_mode=mode
            )
        else:
            logger.info("Processing as single URL (non-crawl)")
            processed_page_count, char_count, llama_parse_credits = _process_single_url_content(
                user_id=user_id, knowledge_id=knowledge_id, object_id=object_id, weblink=weblink,
                scraped_content=scraped_content, hybrid_search=hybrid_search, embedding_model=embedding_model,
                chunk_size=chunk_size, contextualise_chunks=contextualise_chunks,
                enable_chunking=enable_chunking, owner_id=owner_id, processing_mode=mode
            )
        del scraped_content  # Free scraped content after processing

        # Step 3: Mark task as completed and record duration
        processing_time_ms = mark_task_completed(
            knowledge_id, object_id, processing_start_time, effective_mode=effective_mode
        )
        mark_indexing_time(processing_time_ms, knowledge_id=knowledge_id, object_id=object_id, char_count=char_count)
        logger.info(f"Weblink processing complete: url={weblink}, pages={processed_page_count}, chars={char_count}, duration={processing_time_ms}ms")

        # Step 4: Send usage tracking event
        send_weblink_processing_event(
            scrape_method=event_scrape_method,
            crawl_depth=max_depth,
            processed_page_count=processed_page_count,
            char_count=char_count,
            duration_ms=processing_time_ms,
            knowledge_id=knowledge_id,
            object_id=object_id,
            key_type=scrape_key_type,
            requested_mode=mode.value,
            effective_mode=effective_mode,
            parse_mode=scraped_content_parse_mode,
            llama_parse_credits=llama_parse_credits,
        )

    except Exception as e:
        update_processing_status(knowledge_id, object_id, "failed", error_detail=str(e))
        logger.error(f"Weblink processing failed: url={weblink}, knowledge_id={knowledge_id}: {e}")
        raise
