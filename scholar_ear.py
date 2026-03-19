#!/usr/bin/env python3
"""ScholarEar — Turn research papers into podcast-style audio summaries."""

import argparse
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

import config

load_dotenv()


# ── Helpers ──────────────────────────────────────────────────────────────────

def print_header():
    print("\nScholarEar 🎧")
    print("─────────────────────────────")


def fmt_chars(n: int) -> str:
    return f"{n:,}"


# ── Step 1: Extract text from PDF ────────────────────────────────────────────

def extract_text(pdf_path: str) -> tuple[str, str, int]:
    """Try PyPDF2, then pdfplumber, then pymupdf. Returns (text, extractor_name, page_count)."""

    # PyPDF2
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        pages = len(reader.pages)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if text.strip() and len(text.strip()) > 200:
            return text, "PyPDF2", pages
    except Exception:
        pass

    # pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            pages = len(pdf.pages)
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            if text.strip() and len(text.strip()) > 200:
                return text, "pdfplumber", pages
    except Exception:
        pass

    # pymupdf (fitz)
    try:
        import fitz
        doc = fitz.open(pdf_path)
        pages = len(doc)
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        if text.strip() and len(text.strip()) > 200:
            return text, "pymupdf", pages
    except Exception:
        pass

    return "", "none", 0


# ── Step 2: Detect sections ──────────────────────────────────────────────────

def detect_sections(client, text: str) -> list[tuple[str, str]]:
    """Use Claude to identify section headings, then split text by those headings."""
    import json

    print("🔍 Detecting sections...")

    # Send enough text for Claude to see all headings (first ~15000 chars)
    sample = text[:15000]
    message = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=256,
        system=(
            "You are a research paper parser. Given extracted text from a PDF, identify the "
            "top-level section headings exactly as they appear in the text. Return ONLY a JSON "
            "array of strings. No explanation, no commentary, no markdown — just the JSON array. "
            "Include any numbering as it appears. "
            "Skip sub-sections — only return top-level sections. "
            "Skip References/Acknowledgments/Appendix sections. "
            "Example: [\"Abstract\", \"1. Introduction\", \"2. Methods\", \"3. Results\", \"4. Conclusion\"]"
        ),
        messages=[
            {"role": "user", "content": f"Identify the top-level section headings:\n\n{sample}"}
        ],
    )
    raw = message.content[0].text.strip()

    # Parse JSON array from response (handle markdown code blocks and extra text)
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    # Try to extract a JSON array if Claude added extra text around it
    json_match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if json_match:
        cleaned = json_match.group(0)
    try:
        headings = json.loads(cleaned)
    except json.JSONDecodeError:
        print(f"⚠️  Could not parse section headings from Claude. Raw response:")
        print(f"    {raw[:300]}")
        print("   Falling back to chunking.")
        return chunk_text(text)

    if not headings or len(headings) < 2:
        return chunk_text(text)

    # Split text by the detected headings
    sections = []
    for i, heading in enumerate(headings):
        # Find the heading in the text (case-insensitive, flexible whitespace)
        escaped = re.escape(heading)
        # Allow flexible whitespace between words
        pattern = r"\s*".join(escaped.split(r"\ "))
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            start = match.end()
            # Find where the next heading starts
            end = len(text)
            for next_heading in headings[i + 1:]:
                next_escaped = re.escape(next_heading)
                next_pattern = r"\s*".join(next_escaped.split(r"\ "))
                next_match = re.search(next_pattern, text[start:], re.IGNORECASE)
                if next_match:
                    end = start + next_match.start()
                    break

            body = text[start:end].strip()
            # Clean heading name: remove numbering prefix
            name = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", heading).strip().title()
            if body:
                sections.append((name, body))

    if len(sections) >= 2:
        return sections

    return chunk_text(text)


def chunk_text(text: str) -> list[tuple[str, str]]:
    """Split text into roughly equal chunks when no headings found."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

    chunks: list[tuple[str, str]] = []
    current = []
    current_len = 0

    for para in paragraphs:
        current.append(para)
        current_len += len(para)
        if current_len >= config.CHUNK_TARGET_CHARS:
            label = f"Part {len(chunks) + 1}"
            chunks.append((label, "\n\n".join(current)))
            current = []
            current_len = 0

    if current:
        label = f"Part {len(chunks) + 1}"
        chunks.append((label, "\n\n".join(current)))

    return chunks


def filter_sections(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Remove sections that should be skipped (e.g., References)."""
    skip = {s.lower() for s in config.SKIP_SECTIONS}
    return [(name, body) for name, body in sections if name.lower() not in skip]


# ── Step 3: Summarize sections with Claude ───────────────────────────────────

def get_claude_client():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not set.")
        print("   Add it to your .env file:  ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)
    from anthropic import Anthropic
    return Anthropic(api_key=api_key)


def summarize_sections(client, sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Summarize each section via the Claude API. Returns (section_name, summary)."""
    system_prompt = (
        "You are a research paper narrator. Summarize the given section of a research paper "
        "in a natural narration style, as if explaining it to a podcast listener. "
        f"Keep the summary concise but informative — {config.MAX_SUMMARY_SENTENCES} sentences max. "
        "Use smooth transitions that reference the section name. For example:\n"
        "- 'In the introduction, the authors discuss...'\n"
        "- 'Moving to the methodology...'\n"
        "- 'The key findings show that...'\n"
        "- 'To wrap up, the authors conclude that...'\n"
        "Do NOT use markdown formatting. Write plain spoken English suitable for audio narration."
    )

    summaries = []
    for name, body in sections:
        print(f"🧠 Summarizing: {name}...")
        # Truncate very long sections to avoid token limits
        truncated = body[:12000] if len(body) > 12000 else body
        message = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=512,
            system=system_prompt,
            messages=[
                {"role": "user", "content": f"Section: {name}\n\n{truncated}"}
            ],
        )
        summary = message.content[0].text.strip()
        summaries.append((name, summary))

    return summaries


# ── Step 4: Generate semantic filename ───────────────────────────────────────

def generate_filename(client, full_text: str) -> str:
    """Ask Claude for a max-5-word kebab-case title for the paper."""
    print("🏷️  Generating title...")
    # Send the first ~3000 chars (abstract/intro) for context
    snippet = full_text[:3000]
    message = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=50,
        messages=[
            {
                "role": "user",
                "content": (
                    "Based on this research paper excerpt, generate a concise title of at most "
                    "5 words that captures the paper's core topic. Return ONLY the title in "
                    "kebab-case (lowercase, hyphens between words). No explanation, no quotes.\n\n"
                    f"Excerpt:\n{snippet}"
                ),
            }
        ],
    )
    title = message.content[0].text.strip().lower()
    # Sanitize: keep only alphanumeric and hyphens
    title = re.sub(r"[^a-z0-9\-]", "", title)
    title = re.sub(r"-+", "-", title).strip("-")
    return title or "paper-summary"


# ── Step 5: Convert to audio ─────────────────────────────────────────────────

def stitch_narration(summaries: list[tuple[str, str]], tts_engine: str) -> str:
    """Combine section summaries into one narration text with pauses."""
    parts = []
    for _name, summary in summaries:
        parts.append(summary)
    # Insert pause markers between sections
    if tts_engine == "edge":
        # Edge TTS supports SSML-like pauses via "..."
        return "\n\n...\n\n".join(parts)
    else:
        # For OpenAI/ElevenLabs, use a simple ellipsis pause
        return "\n\n...\n\n".join(parts)


async def tts_edge(text: str, output_path: str) -> None:
    import edge_tts
    voice = config.TTS_VOICES["edge"]
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def tts_openai(text: str, output_path: str) -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ OPENAI_API_KEY not set.")
        print("   Add it to your .env file:  OPENAI_API_KEY=sk-...")
        sys.exit(1)
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    response = client.audio.speech.create(
        model="tts-1",
        voice=config.TTS_VOICES["openai"],
        input=text,
    )
    response.stream_to_file(output_path)


def tts_elevenlabs(text: str, output_path: str) -> None:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("❌ ELEVENLABS_API_KEY not set.")
        print("   Add it to your .env file:  ELEVENLABS_API_KEY=...")
        sys.exit(1)
    from elevenlabs.client import ElevenLabs
    from elevenlabs import save
    client = ElevenLabs(api_key=api_key)

    import requests as req
    voice_name = config.TTS_VOICES["elevenlabs"]

    # Look up voice_id by name via API
    resp = req.get(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": api_key},
    )
    resp.raise_for_status()
    voice_id = None
    for v in resp.json().get("voices", []):
        if v["name"].lower() == voice_name.lower():
            voice_id = v["voice_id"]
            break
    if not voice_id:
        print(f"⚠️  Voice '{voice_name}' not found. Using first available voice.")
        voice_id = resp.json()["voices"][0]["voice_id"]

    audio = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
    )
    save(audio, output_path)


def generate_audio(text: str, tts_engine: str, output_path: str) -> None:
    """Dispatch to the selected TTS engine."""
    print(f"🔊 Generating audio with {tts_engine.title()} TTS...")

    try:
        if tts_engine == "edge":
            asyncio.run(tts_edge(text, output_path))
        elif tts_engine == "openai":
            tts_openai(text, output_path)
        elif tts_engine == "elevenlabs":
            tts_elevenlabs(text, output_path)
    except Exception as e:
        print(f"❌ TTS generation failed: {e}")
        # Fallback: save text to .txt
        txt_path = output_path.replace(".mp3", ".txt")
        Path(txt_path).write_text(text, encoding="utf-8")
        print(f"📝 Text summary saved as fallback: {txt_path}")
        return

    print(f"✅ Saved: {output_path}")


# ── CLI Entry Point ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="scholar-ear",
        description="Turn research papers into podcast-style audio summaries.",
    )
    parser.add_argument("pdf", help="Path to a PDF research paper")
    parser.add_argument(
        "--tts",
        choices=["edge", "openai", "elevenlabs"],
        default=config.DEFAULT_TTS_ENGINE,
        help="TTS engine to use (default: edge)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print candidate heading lines and skip PDF deletion",
    )
    args = parser.parse_args()

    print_header()

    # Validate PDF path
    pdf_path = os.path.expanduser(args.pdf)
    if not os.path.isfile(pdf_path):
        print(f"❌ File not found: {pdf_path}")
        sys.exit(1)
    if not pdf_path.lower().endswith(".pdf"):
        print(f"❌ Not a PDF file: {pdf_path}")
        sys.exit(1)

    print(f"📄 Loading: {args.pdf}")

    # Step 1: Extract text
    print("📖 Extracting text...", end=" ")
    text, extractor, page_count = extract_text(pdf_path)
    if not text.strip():
        print(f"\n❌ Failed to extract text from PDF using all available libraries.")
        sys.exit(1)
    print(f"({extractor})")
    print(f"✅ Extracted {fmt_chars(len(text))} characters across {page_count} pages")

    if args.debug:
        print("\n🐛 DEBUG — Short lines (likely headings):")
        for i, line in enumerate(text.splitlines()):
            stripped = line.strip()
            if stripped and len(stripped) < 80 and not stripped.endswith("."):
                print(f"  [{i:4d}] {stripped!r}")
        print()

    # Step 2: Detect sections (uses Claude)
    client = get_claude_client()
    sections = detect_sections(client, text)
    sections = filter_sections(sections)
    if not sections:
        print("❌ No usable content found in PDF.")
        sys.exit(1)

    section_names = [name for name, _ in sections]
    print(f"✅ Detected {len(sections)} sections: {', '.join(section_names)}")

    # Step 3: Summarize with Claude
    summaries = summarize_sections(client, sections)

    # Step 4: Generate semantic filename
    title = generate_filename(client, text)
    print(f"🏷️  Generated title: {title}")

    # Step 5 & 6: Generate audio and save
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(config.OUTPUT_DIR, f"{title}.mp3")

    narration = stitch_narration(summaries, args.tts)
    generate_audio(narration, args.tts, output_path)

    # Clean up: delete the source PDF (skip in debug mode)
    if not args.debug:
        os.remove(pdf_path)
        print(f"🗑️  Deleted: {args.pdf}")


if __name__ == "__main__":
    main()
