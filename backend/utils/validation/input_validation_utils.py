"""
Input validation utilities for chatbot knowledge management.
Contains validation functions specific to chatbot input processing.
"""

from typing import Optional
from fastapi import HTTPException
from pydantic import ValidationError
from utils.pydantic_models_knowledge import ChatbotData, ScrapeUsing, ScrapingMethod, TranscribeUsing


def validate_chatbot_input(
    user_id: Optional[str] = None,
    chatbot_id: Optional[str] = None,
    text: Optional[str] = None,
    weblink: Optional[str] = None,
    use_llama_parse: Optional[bool] = False,
    scrape_using: ScrapeUsing = ScrapeUsing.option1,
    scraping_method: ScrapingMethod = ScrapingMethod.option1,
    transcript_provider: TranscribeUsing = TranscribeUsing.option1,
    contextualise_chunks: Optional[bool] = False,
    chunk_size: Optional[int] = None
) -> tuple:
    """
    Validate chatbot input data using Pydantic model validation.
    
    Args:
        user_id: Optional user identifier
        chatbot_id: Optional chatbot identifier
        text: Optional text content
        weblink: Optional weblink URL
        use_llama_parse: Whether to use LlamaParse
        scrape_using: Scraping service to use
        scraping_method: Scraping method
        transcript_provider: Transcription provider
        contextualise_chunks: Whether to contextualise chunks
        chunk_size: Size of text chunks
        
    Returns:
        Tuple of validated input parameters
        
    Raises:
        HTTPException: If validation fails
    """
    try:
        # ChatbotData.strip_user_id_from_payload always erases user_id (it exists to
        # stop a client-supplied user_id in a request body from spoofing identity).
        # Here user_id is the trusted, server-resolved caller identity (from
        # require_permission), not client input, so it must NOT be routed through that
        # validator — passed to ChatbotData at all, it would silently come back as None.
        chatbot_data = ChatbotData(
            chatbot_id=chatbot_id,
            text=text,
            weblink=weblink,
            use_llama_parse=use_llama_parse,
            scrape_using=scrape_using,
            scraping_method=scraping_method,
            transcript_provider=transcript_provider,
            contextualise_chunks=contextualise_chunks,
            chunk_size=chunk_size
        )
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors())

    # Unpack the validated data
    return (
        user_id,
        chatbot_data.chatbot_id,
        chatbot_data.text,
        chatbot_data.weblink,
        chatbot_data.use_llama_parse,
        chatbot_data.scrape_using,
        chatbot_data.scraping_method,
        chatbot_data.transcript_provider,
        chatbot_data.contextualise_chunks,
        chatbot_data.chunk_size
    )


def handle_empty_values(
    text,
    file,
    use_llama_parse,
    transcript_provider,
    weblink,
    scrape_using,
    contextualise_chunks,
    chunk_size
) -> tuple:
    """
    Handle empty string values by converting them to appropriate defaults.
    
    Args:
        text: Text content
        file: File object
        use_llama_parse: LlamaParse flag
        transcript_provider: Transcription provider
        weblink: Weblink URL
        scrape_using: Scraping service
        contextualise_chunks: Contextualise chunks flag
        chunk_size: Chunk size
        
    Returns:
        Tuple of processed values with empty strings converted to appropriate defaults
    """
    if text == "":
        text = None
    if isinstance(file, str) and file == "":
        file = None
    if use_llama_parse == "":
        use_llama_parse = False
    if transcript_provider == "":
        transcript_provider = TranscribeUsing.option1
    if weblink == "":
        weblink = None
    if scrape_using == "":
        scrape_using = ScrapeUsing.option1
    if contextualise_chunks == "":
        contextualise_chunks = False
    if chunk_size == "":
        chunk_size = None
        
    return (
        text, file, use_llama_parse, transcript_provider,
        weblink, scrape_using, contextualise_chunks, chunk_size
    )
