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

def _normalize_text(text: str) -> str:
    """Normalize whitespace so heading detection works reliably across extractors."""
    # Collapse runs of 3+ newlines into 2 (paragraph break)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove trailing whitespace on each line
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    return text


def detect_sections(text: str) -> list[tuple[str, str]]:
    """Return list of (section_name, section_text). Falls back to chunking."""

    text = _normalize_text(text)
    all_headings = config.SECTION_PATTERNS + config.SKIP_SECTIONS

    # Collect candidates from multiple strategies, pick the one with the most sections.
    best = []

    # Strategy 1: Match known heading names (with optional numbering, case-insensitive)
    known_pattern = re.compile(
        r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(" + "|".join(re.escape(h) for h in all_headings) + r")\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    known_matches = list(known_pattern.finditer(text))
    if len(known_matches) >= 2:
        best.append(("known", known_matches, 1))

    # Strategy 2: Top-level numbered headings only (e.g. "1. INTRODUCTION", "2. BINDING")
    # Skips sub-sections (1.1, 2.3) so they fold into their parent section.
    # Filters out reference-style lines (number followed by author name with comma).
    toplevel_pattern = re.compile(
        r"^\s*(\d+\.?\s+[A-Za-z][A-Za-z\s\-:,/&]{1,60})\s*$",
        re.MULTILINE,
    )
    toplevel_matches = [
        m for m in toplevel_pattern.finditer(text)
        if not re.match(r"^\d+\.?\s+[A-Z][a-z]+,", m.group(1).strip())  # skip "1. Birrell, ..."
        and not re.match(r"^\d+\.?\s+[A-Z]+,", m.group(1).strip())      # skip "1. BIRRELL, ..."
    ]
    if len(toplevel_matches) >= 3:
        best.append(("numbered", toplevel_matches, 1))

    # Strategy 3: ALL-CAPS lines (3+ chars, at least 3 letters)
    caps_pattern = re.compile(
        r"^\s*([A-Z][A-Z\s\-:,/&]{2,60})\s*$",
        re.MULTILINE,
    )
    caps_matches = [m for m in caps_pattern.finditer(text)
                    if sum(c.isalpha() for c in m.group(1)) >= 3]
    if len(caps_matches) >= 3:
        best.append(("caps", caps_matches, 1))

    # Strategy 4: Short standalone lines (title-case, no trailing period, ≤10 words)
    short_line_pattern = re.compile(
        r"^\s*([A-Z][A-Za-z\s\-:,/&]{2,78})\s*$",
        re.MULTILINE,
    )
    short_matches = [m for m in short_line_pattern.finditer(text)
                     if not m.group(1).strip().endswith(".")
                     and len(m.group(1).split()) <= 10]
    if len(short_matches) >= 3:
        best.append(("short", short_matches, 1))

    # Pick the best strategy: prefer known headings and numbered headings over
    # heuristic matches (caps/short lines) which are noisy.
    # Among same-priority strategies, prefer more sections.
    priority = {"known": 0, "numbered": 0, "caps": 1, "short": 2}
    if best:
        best.sort(key=lambda x: (priority.get(x[0], 9), -len(x[1])))
        _, matches, group = best[0]
        return _extract_from_matches(text, matches, group)

    # Fallback: chunk by paragraph density
    return chunk_text(text)


def _extract_from_matches(text: str, matches: list, group: int) -> list[tuple[str, str]]:
    """Given regex matches for headings, extract (name, body) pairs."""
    sections = []
    for i, match in enumerate(matches):
        name = match.group(group).strip()
        # Clean up numbering prefix for display (e.g. "2. Overview" -> "Overview")
        name = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", name).strip().title()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append((name, body))
    return sections


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

    # Resolve voice name to voice_id
    voice_name = config.TTS_VOICES["elevenlabs"]
    voice_id = None
    voices_response = client.voices.get_all()
    for voice in voices_response.voices:
        if voice.name.lower() == voice_name.lower():
            voice_id = voice.voice_id
            break
    if not voice_id:
        print(f"⚠️  Voice '{voice_name}' not found. Using first available voice.")
        voice_id = voices_response.voices[0].voice_id

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

    # Step 2: Detect sections
    sections = detect_sections(text)
    sections = filter_sections(sections)
    if not sections:
        print("❌ No usable content found in PDF.")
        sys.exit(1)

    section_names = [name for name, _ in sections]
    print(f"🔍 Detected {len(sections)} sections: {', '.join(section_names)}")

    # Step 3: Summarize with Claude
    client = get_claude_client()
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
