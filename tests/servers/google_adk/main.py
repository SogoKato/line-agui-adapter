"""Basic Chat feature."""

from __future__ import annotations

import logging
import os

from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from dotenv import load_dotenv
from fastapi import FastAPI
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode

load_dotenv()

LOG_LEVEL = os.environ.get("GOOGLE_ADK_TEST_LOG_LEVEL", "").upper()

if LOG_LEVEL:
    resolved_log_level = getattr(logging, LOG_LEVEL, None)
    if isinstance(resolved_log_level, int):
        logging.basicConfig(level=resolved_log_level)
        logging.getLogger().setLevel(resolved_log_level)

# Create a sample ADK agent (this would be your actual agent)
sample_agent = LlmAgent(
    name="assistant",
    model="gemini-2.5-flash",
    instruction="""
    You are a helpful multimodal assistant.
    Help users by answering their questions and assisting with their needs.

    General behavior:
    - If the user greets you, greet them back specifically with "Hello".
    - If the user greets you and does not make any request,
      greet them and ask "how can I assist you?"
    - If the user makes a statement without making a request,
      respond conversationally about that topic.
      Do not say you cannot help unless it is truly necessary.
    - If the user asks a question, answer directly when possible using context.
      Only say that you do not have enough information when that is actually the case.

    Image and multimodal behavior:
    - If the user sends an image, analyze it as real input.
    - If the user sends only an image, briefly describe what is visible.
    - Mention key subjects, text, and notable details when relevant.
    - If the user asks about the image, answer from the visual content.
    - Avoid generic replies like "It looks like you've uploaded an image".
    - If the image is unclear, say what is uncertain and
      give your best-effort description.

    Response style:
    - Be specific, concise, and useful.
    - When the user sends only an image,
      do not ask for clarification before giving an initial description.
    """,
)

# Create ADK middleware agent instance
chat_agent = ADKAgent(
    adk_agent=sample_agent,
    app_name="demo_app",
    user_id="demo_user",
    session_timeout_seconds=3600,
    use_in_memory_services=True,
    run_config_factory=lambda _input: RunConfig(
        streaming_mode=StreamingMode.SSE,
        save_input_blobs_as_artifacts=False,
    ),
)

# Create FastAPI app
app = FastAPI(title="ADK Middleware Basic Chat")

# Add the ADK endpoint
add_adk_fastapi_endpoint(app, chat_agent, path="/")
