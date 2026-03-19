# ScholarEar

Turn research papers into podcast-style audio summaries.

ScholarEar extracts text from a PDF research paper, uses Claude to detect sections and summarize each one in a natural narration style, then converts the narration into an MP3 audio file. The source PDF is automatically deleted after processing.

## Installation

```bash
# Create and activate conda environment
conda create -n scholar-ear python=3.11 -y
conda activate scholar-ear

# Install dependencies
pip install -r requirements.txt
```

## Setup

Create a `.env` file in the project root with your API keys:

```
ANTHROPIC_API_KEY=sk-ant-...       # Required — used for summarization and section detection
OPENAI_API_KEY=sk-...              # Only needed if using --tts openai
ELEVENLABS_API_KEY=...             # Only needed if using --tts elevenlabs
```

## Usage

Drop a PDF into the `papers/` folder, then run:

```bash
# Default (Edge TTS — free, no API key needed)
python scholar_ear.py papers/your-paper.pdf

# OpenAI TTS
python scholar_ear.py papers/your-paper.pdf --tts openai

# ElevenLabs TTS
python scholar_ear.py papers/your-paper.pdf --tts elevenlabs

# Debug mode (shows heading candidates, skips PDF deletion)
python scholar_ear.py papers/your-paper.pdf --debug
```

Output is saved to `audio/<semantic-title>.mp3`. The source PDF is deleted after processing (unless `--debug` is used).

## How It Works

1. **Extract text** from PDF (PyPDF2 → pdfplumber → pymupdf fallback)
2. **Detect sections** using Claude to identify top-level headings
3. **Summarize** each section via Claude in a natural narration style
4. **Generate filename** — Claude creates a max 5-word kebab-case title
5. **Convert to audio** using the selected TTS engine
6. **Save** MP3 to `audio/` and delete the source PDF

## Supported TTS Engines

| Engine | API Key Required | Notes |
|--------|-----------------|-------|
| Edge TTS (default) | No | Free, uses Microsoft Edge's online TTS service |
| OpenAI TTS | Yes (`OPENAI_API_KEY`) | High-quality, uses `tts-1` model |
| ElevenLabs | Yes (`ELEVENLABS_API_KEY`) | Premium voices, uses `eleven_multilingual_v2` |

## Config Options

Edit `config.py` to customize:

- **Voice per TTS engine** — default voices for Edge (`en-US-JennyNeural`), OpenAI (`nova`), ElevenLabs (`Ember`)
- **Claude model** — model used for summarization (default: `claude-sonnet-4-6`)
- **Max summary sentences** — controls summary length per section (default: 4)
- **Section pause duration** — silence between sections in seconds
- **Output directory** — where MP3 files are saved (default: `audio/`)

## Project Structure

```
scholar-ear/
├── scholar_ear.py      # main CLI entry point
├── config.py           # voices, model settings, tunable params
├── requirements.txt
├── .env                # API keys (not committed)
├── papers/             # drop PDFs here (not committed)
└── audio/              # output folder (auto-created, not committed)
```
